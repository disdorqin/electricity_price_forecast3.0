"""
extreme_price_shadow.py — P3.2 Controlled Shadow Integration for electricity_price_forecast3.0.

Design goals (controlled shadow, NEVER production):
- DEFAULT OFF. Only runs when --enable-extreme-price-shadow is passed.
- Reads realtime fused predictions / model predictions / DA anchor / period /
  hour_business / calendar from the 3.0 run + ledger (cutoff-safe, D-1 14:00).
- Fits the P3 Extreme Price classifiers on HISTORY ONLY (actuals strictly before
  the target day), then predicts the target day. This is strictly leakage-free:
  it never uses D-day actual, never uses D14-after realtime actual.
- Applies the validated guard + rollback from the P3 engine.
- Writes ONLY to outputs/runs/{date}/extreme_price_shadow/.
  It NEVER writes final/, NEVER writes submission_ready.csv, NEVER replaces the
  original fused realtime prediction, and is NEVER marked champion / NORMAL-improvement.

Failure policy: failures are NEVER silently swallowed. They are logged and surfaced
in a degraded shadow output + report (status != ok) so the main chain is unaffected.

Reuses validated P3 math (guard/rollback/classifier feature builders) from
experimental.p3_extreme_price_correction to avoid logic divergence.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# --- Reuse validated P3 engine pieces (read-only) ---
from experimental.p3_extreme_price_correction.negative_price_classifier import (
    build_features as _neg_feat_build,
)
from experimental.p3_extreme_price_correction.spike_price_classifier import (
    build_features as _spk_feat_build,
)
from experimental.p3_extreme_price_correction.correction_guard import guard_pass
from experimental.p3_extreme_price_correction.rollback_guard import evaluate_rollback
from experimental.p3_extreme_price_correction.models import SimpleLogistic
from experimental.p3_extreme_price_correction.config import P3Config

logger = logging.getLogger(__name__)

RT_MODELS = ["rt916", "sgdfnet", "timemixer", "timesfm"]
DA_MODELS = ["lightgbm", "timemixer", "timesfm"]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "extreme_price_shadow.yaml"

# Required output columns (schema contract from P3.2 spec)
REQUIRED_COLUMNS = [
    "business_day", "ds", "hour_business", "period",
    "original_pred", "shadow_corrected_pred", "correction_amount",
    "negative_probability", "spike_probability", "spike_type",
    "correction_reason", "confidence", "applied", "rollback_reason",
    "shadow_only", "model_version", "run_id",
]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class ExtremePriceShadowConfig:
    # integration-level
    enabled: bool = False                 # master switch (also set by CLI flag)
    shadow_only: bool = True              # always True; --shadow-only reaffirms
    model_version: str = "extreme_price_shadow_v1"
    history_window_days: int = 90
    run_id_prefix: str = "eps_shadow"
    require_risk_pack: bool = False       # if True, demand risk probabilities; else degraded diagnostics

    # correction knobs (mirror P3 candidate config)
    negative_classifier_enabled: bool = True
    spike_classifier_enabled: bool = True
    residual_corrector_enabled: bool = False
    NEG_THRESH: float = 0.80
    NEG_LABEL: float = -50.0
    NEG_ACT_PRED_CAP: float = 50.0
    NEG_FLOOR_TARGET: float = -80.0
    SPK_THRESH: float = 0.60
    SPK_LABEL: float = 500.0
    SPK_MIN_ORIGINAL: float = 250.0
    SPK_LIFT_RATIO: float = 0.35
    SPK_LIFT_ABS: float = 350.0
    SPK_9_16_BOOST: float = 1.15
    CAP_ABS: float = 350.0
    CAP_RATIO: float = 0.35
    PRICE_FLOOR: float = -100.0
    PRICE_CEIL: float = 1500.0
    ROLLBACK_MIN_CONF: float = 0.30

    def to_p3_config(self) -> P3Config:
        """Build the validated P3 engine config from these knobs."""
        c = P3Config()
        c.negative_classifier_enabled = self.negative_classifier_enabled
        c.spike_classifier_enabled = self.spike_classifier_enabled
        c.residual_corrector_enabled = self.residual_corrector_enabled
        c.NEG_THRESH = self.NEG_THRESH
        c.NEG_LABEL = self.NEG_LABEL
        c.NEG_ACT_PRED_CAP = self.NEG_ACT_PRED_CAP
        c.NEG_FLOOR_TARGET = self.NEG_FLOOR_TARGET
        c.SPK_THRESH = self.SPK_THRESH
        c.SPK_LABEL = self.SPK_LABEL
        c.SPK_MIN_ORIGINAL = self.SPK_MIN_ORIGINAL
        c.SPK_LIFT_RATIO = self.SPK_LIFT_RATIO
        c.SPK_LIFT_ABS = self.SPK_LIFT_ABS
        c.SPK_9_16_BOOST = self.SPK_9_16_BOOST
        c.CAP_ABS = self.CAP_ABS
        c.CAP_RATIO = self.CAP_RATIO
        c.PRICE_FLOOR = self.PRICE_FLOOR
        c.PRICE_CEIL = self.PRICE_CEIL
        c.ROLLBACK_MIN_CONF = self.ROLLBACK_MIN_CONF
        c.CUTOFF = "D14"
        return c

    def build_run_id(self, target_date: str) -> str:
        return f"{self.run_id_prefix}_{target_date}"


# ---------------------------------------------------------------------------
# Minimal YAML loader (self-contained, no PyYAML dependency required)
# ---------------------------------------------------------------------------
def _strip_comment(line: str) -> str:
    """Remove an inline '# ...' comment (no quoting used in our config)."""
    return line.split("#", 1)[0].rstrip()


def _load_yaml_simple(path: Path) -> dict:
    """Parse a simple two-level YAML (key: value, one level of indentation)."""
    data: dict = {}
    cur: dict | None = None
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = _strip_comment(raw)
            if not line.strip():
                continue
            if line.startswith(" ") or line.startswith("\t"):
                k, _, v = line.strip().partition(":")
                if cur is not None and k:
                    cur[k.strip()] = _coerce(v.strip())
            else:
                k, _, v = line.partition(":")
                key = k.strip()
                if v.strip() == "":
                    cur = data.setdefault(key, {})
                else:
                    data[key] = _coerce(v.strip())
                    cur = None
    return data


def _coerce(v: str):
    if v == "":
        return None
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        if "." in v:
            return float(v)
        return int(v)
    except ValueError:
        return v


def load_config(path: Path | None = None) -> ExtremePriceShadowConfig:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not path.exists():
        logger.warning(f"Shadow config not found at {path}; using built-in defaults.")
        return ExtremePriceShadowConfig()
    raw = _load_yaml_simple(path)
    cfg = ExtremePriceShadowConfig()
    shadow = raw.get("shadow", {}) or {}
    corr = raw.get("correction", {}) or {}
    for k, val in shadow.items():
        if hasattr(cfg, k):
            setattr(cfg, k, val)
    for k, val in corr.items():
        if hasattr(cfg, k):
            setattr(cfg, k, val)
    return cfg


# ---------------------------------------------------------------------------
# Ledger I/O
# ---------------------------------------------------------------------------
def _load_pred_ledger(ledger_root: Path, task: str) -> pd.DataFrame:
    p = Path(ledger_root) / task / "prediction" / "prediction_ledger.parquet"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


def _load_actual_ledger(ledger_root: Path, task: str) -> pd.DataFrame:
    p = Path(ledger_root) / task / "actual" / "actual_ledger.parquet"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


def _pivot_wide(pred_df: pd.DataFrame, models):
    if pred_df.empty:
        return pd.DataFrame(), []
    w = pred_df.pivot_table(
        index=["target_day", "business_day", "ds", "hour_business", "period"],
        columns="model_name", values="y_pred",
    ).reset_index()
    present = [m for m in models if m in w.columns]
    if present:
        w = w.dropna(subset=present)
    return w, present


def _mae(a, f) -> float:
    a = np.asarray(a, float)
    f = np.asarray(f, float)
    if a.size == 0:
        return float("inf")
    return float(np.mean(np.abs(a - f)))


def _expanding_inverse_mae_fused(wide: pd.DataFrame, models, actuals: pd.DataFrame) -> pd.Series:
    """Per-day expanding inverse-MAE weighted fusion (uses only past actuals)."""
    if wide.empty:
        return pd.Series(dtype=float)
    actuals = actuals.set_index("target_day") if "target_day" in actuals.columns else actuals
    fused = pd.Series(index=wide.index, dtype=float)
    days = sorted(wide["target_day"].unique())
    for d in days:
        past = actuals[actuals.index < d]
        cols = [m for m in models if m in wide.columns]
        if len(past) == 0 or len(cols) == 0:
            w = np.ones(len(cols)) / max(len(cols), 1)
        else:
            maes = []
            for m in cols:
                sub = wide[(wide["target_day"] < d)][["target_day", "hour_business", m]].copy()
                sub = sub.merge(
                    actuals.reset_index()[["target_day", "hour_business", "y_true"]],
                    on=["target_day", "hour_business"], how="inner")
                maes.append(_mae(sub[m].values, sub["y_true"].values) if len(sub) > 0 else np.inf)
            maes = np.array(maes)
            inv = 1.0 / (maes + 1e-3)
            if inv.sum() == 0 or np.isnan(inv).any():
                w = np.ones(len(cols)) / len(cols)
            else:
                w = inv / inv.sum()
        X = wide.loc[wide["target_day"] == d, cols].values
        fused[wide["target_day"] == d] = X @ w
    return fused


def _hist_stats(actual_full: pd.DataFrame, days_arr, hours_arr):
    """Per-row historical same-hour stats using only actuals strictly BEFORE each row's day."""
    if actual_full.empty:
        n = len(days_arr)
        return (np.zeros(n), np.zeros(n), np.zeros(n))
    af = actual_full.copy()
    af["target_day"] = pd.to_datetime(af["target_day"])
    rate, p50, p90 = [], [], []
    for d, h in zip(days_arr, hours_arr):
        td = pd.Timestamp(d)
        past = af[(af["target_day"] < td) & (af["hour_business"] == int(h))]["y_true"]
        if len(past) == 0:
            rate.append(np.nan); p50.append(np.nan); p90.append(np.nan)
        else:
            rate.append(float((past < 0).mean()))
            p50.append(float(past.median()))
            p90.append(float(past.quantile(0.9)))
    return np.array(rate, float), np.array(p50, float), np.array(p90, float)


# ---------------------------------------------------------------------------
# Feature builder (target day + history buffer, cutoff-safe)
# ---------------------------------------------------------------------------
def _read_run_realtime_fused(runs_root: Path | None, target_date: str):
    """Read realtime fused predictions from the 3.0 run output (preferred source).

    Tries `realtime/final/realtime_final_predictions.csv` then
    `realtime/fuse/fused_predictions.csv`. Returns a small DataFrame with
    business_day, hour_business, original_pred, or None.
    """
    if not runs_root:
        return None
    runs_root = Path(runs_root)
    for rel in [f"{target_date}/realtime/final/realtime_final_predictions.csv",
                f"{target_date}/realtime/fuse/fused_predictions.csv"]:
        p = runs_root / rel
        if p.exists():
            df = pd.read_csv(p)
            if "y_fused" in df.columns:
                out = df[["business_day", "hour_business", "y_fused"]].copy()
                out = out.rename(columns={"y_fused": "original_pred"})
                return out
    return None


def build_shadow_features(target_date: str, ledger_root: Path, cfg: ExtremePriceShadowConfig,
                          runs_root: Path | None = None):
    """Return (buffer_df, meta). buffer_df has history (with actual) + target (actual=NaN).

    meta carries diagnostics (history_days, has_risk_pack, degraded, reason).
    """
    meta: dict = {"target_date": target_date, "degraded": False, "reason": ""}
    pred_rt = _load_pred_ledger(ledger_root, "realtime")
    actual_rt_full = _load_actual_ledger(ledger_root, "realtime")

    tgt_pred = pred_rt[pred_rt["target_day"] == target_date] if not pred_rt.empty else pred_rt
    if tgt_pred.empty:
        meta["degraded"] = True
        meta["reason"] = f"no realtime predictions for {target_date} in ledger"
        return pd.DataFrame(), meta

    hist_pred = pred_rt[pred_rt["target_day"] < target_date]

    wide_tgt, _ = _pivot_wide(tgt_pred, RT_MODELS)
    wide_hist, _ = _pivot_wide(hist_pred, RT_MODELS)
    if wide_tgt.empty:
        meta["degraded"] = True
        meta["reason"] = f"target day {target_date} has no complete RT model panel"
        return pd.DataFrame(), meta

    wide_all = pd.concat([wide_hist, wide_tgt]).sort_values(
        ["target_day", "hour_business"]).reset_index(drop=True)

    fused_all = _expanding_inverse_mae_fused(wide_all, RT_MODELS, actual_rt_full)

    df = wide_all[["target_day", "business_day", "ds", "hour_business", "period"]].copy()
    for m in RT_MODELS:
        if m in wide_all.columns:
            df[f"pred_{m}"] = wide_all[m].values
    df["original_pred"] = fused_all.values

    pred_cols = [f"pred_{m}" for m in RT_MODELS if f"pred_{m}" in df.columns]
    df["model_std"] = df[pred_cols].std(axis=1)
    df["model_min"] = df[pred_cols].min(axis=1)
    df["model_max"] = df[pred_cols].max(axis=1)

    # DA anchor (target day only; fallback to original_pred)
    da_pred = _load_pred_ledger(ledger_root, "dayahead")
    da_actual_full = _load_actual_ledger(ledger_root, "dayahead")
    da_wt = da_pred[da_pred["target_day"] == target_date] if not da_pred.empty else da_pred
    if not da_wt.empty:
        da_w, _ = _pivot_wide(da_wt, DA_MODELS)
        if not da_w.empty:
            da_fused = _expanding_inverse_mae_fused(da_w, DA_MODELS, da_actual_full)
            da_df = da_w[["target_day", "hour_business"]].copy()
            da_df["da_anchor"] = da_fused.values
            df = df.merge(da_df, on=["target_day", "hour_business"], how="left")
    if "da_anchor" not in df.columns:
        df["da_anchor"] = np.nan
    df["da_anchor"] = df["da_anchor"].fillna(df["original_pred"])

    # Historical same-hour stats (cutoff-safe: only past actuals)
    rate, p50, p90 = _hist_stats(
        actual_rt_full, df["target_day"].values, df["hour_business"].values)
    df["hist_neg_rate_samehour"] = rate
    df["hist_p50_samehour"] = p50
    df["hist_p90_samehour"] = p90

    # Actual column: history rows carry real actual; target rows = NaN (never used as label)
    act = actual_rt_full[["target_day", "hour_business", "y_true"]].copy() \
        if not actual_rt_full.empty else pd.DataFrame(columns=["target_day", "hour_business", "y_true"])
    df = df.merge(act.rename(columns={"y_true": "actual"}),
                  on=["target_day", "hour_business"], how="left")
    # Force target-day actual to NaN (do not peek at D-day actual)
    df.loc[df["target_day"] == target_date, "actual"] = np.nan

    # History days available for classifier training
    hist_rows = df[df["target_day"] < target_date]
    meta["history_days"] = int(hist_rows["target_day"].nunique()) if len(hist_rows) else 0
    meta["has_risk_pack"] = False  # 3.0 run does not emit a separate risk pack yet
    if cfg.require_risk_pack and not meta["has_risk_pack"]:
        meta["degraded"] = True
        meta["reason"] = "risk pack required but not available -> degraded diagnostics only"

    # Fill feature NaNs (first target rows may lack history stats)
    for c in ["hist_neg_rate_samehour", "hist_p50_samehour", "hist_p90_samehour",
              "model_std", "model_min", "model_max", "da_anchor"]:
        df[c] = df[c].fillna(0.0)
    df["model_std"] = df["model_std"].fillna(0.0)

    # Prefer realtime fused predictions from the 3.0 run output when available;
    # fall back to the ledger-recomputed fused otherwise.
    run_fused = _read_run_realtime_fused(runs_root, target_date)
    if run_fused is not None and not run_fused.empty:
        df = df.merge(run_fused, on=["business_day", "hour_business"], how="left",
                     suffixes=("", "_run"))
        if "original_pred_run" in df.columns:
            df["original_pred"] = df["original_pred_run"].fillna(df["original_pred"])
            df = df.drop(columns=["original_pred_run"])

    return df, meta


# ---------------------------------------------------------------------------
# Classifier fit-on-history / predict-target (strictly leakage-free)
# ---------------------------------------------------------------------------
def _fit_predict(history_df: pd.DataFrame, target_df: pd.DataFrame,
                 feat_build, label_fn, cfg, min_samples: int, min_pos: int):
    if history_df.empty:
        return np.zeros(len(target_df)), "no_history"
    Xh = feat_build(history_df)
    yh = label_fn(history_df, cfg).values.astype(float)
    Xt = feat_build(target_df)
    if Xh.shape[0] < min_samples or yh.sum() < min_pos or Xh.shape[1] == 0:
        return np.zeros(len(target_df)), "insufficient_history"
    m = SimpleLogistic()
    m.fit(Xh.values, yh)
    proba = np.clip(m.predict_proba(Xt.values), 0.0, 1.0)
    return proba, "ok"


def _neg_label(df, cfg):
    return (df["actual"] <= cfg.NEG_LABEL).astype(int)


def _spk_label(df, cfg):
    return (df["actual"] > cfg.SPK_LABEL).astype(int)


def _neg_reasons(target_df, cfg):
    parts = []
    for i in range(len(target_df)):
        p = []
        hnr = target_df["hist_neg_rate_samehour"].iloc[i]
        if hnr == hnr and hnr > 0.2:
            p.append(f"samehour_neg_rate={hnr:.2f}")
        if target_df["original_pred"].iloc[i] <= cfg.NEG_ACT_PRED_CAP:
            p.append(f"fused={target_df['original_pred'].iloc[i]:.1f}(low)")
        ms = target_df["model_std"].iloc[i]
        if ms == ms and ms > 40:
            p.append(f"disagreement={ms:.1f}")
        da = target_df["da_anchor"].iloc[i]
        if da == da and da < 50:
            p.append(f"da_anchor={da:.1f}(low)")
        parts.append(";".join(p) if p else "weak_signal")
    return parts


def _spk_reasons(target_df, cfg):
    parts = []
    for i in range(len(target_df)):
        p = []
        hp = target_df["hist_p90_samehour"].iloc[i]
        if hp == hp and hp > cfg.SPK_LABEL:
            p.append(f"samehour_p90={hp:.0f}")
        if target_df["original_pred"].iloc[i] > 400:
            p.append(f"fused={target_df['original_pred'].iloc[i]:.0f}(high)")
        ms = target_df["model_std"].iloc[i]
        if ms == ms and ms > 50:
            p.append(f"disagreement={ms:.1f}")
        parts.append(";".join(p) if p else "weak_signal")
    return parts


# ---------------------------------------------------------------------------
# Decision block (replicates P3 pipeline_shadow logic; residual disabled)
# ---------------------------------------------------------------------------
def _decide(target_df, neg_prob, neg_reason, spk_prob, spk_type, spk_reason, cfg):
    n = len(target_df)
    corrected = target_df["original_pred"].astype(float).values.copy()
    correction_amount = np.zeros(n)
    applied = np.zeros(n, dtype=bool)
    cap_hit = np.zeros(n, dtype=bool)
    confidence = np.zeros(n)
    ctype_used = [""] * n
    correction_reason = [""] * n
    rollback_reason = [""] * n

    for i in range(n):
        original = float(target_df["original_pred"].iloc[i])
        period = target_df["period"].iloc[i]
        cur = original
        amt = 0.0
        is_applied = False
        cur_conf = 0.0
        cur_ctype = ""
        rparts = []

        if cfg.negative_classifier_enabled and neg_prob[i] >= cfg.NEG_THRESH \
                and original <= cfg.NEG_ACT_PRED_CAP:
            target = cfg.NEG_FLOOR_TARGET
            a = target - original
            passed, ch, greason = guard_pass(a, original, target, cfg, "negative")
            if passed:
                cur = target; amt = a; is_applied = True
                cur_conf = float(neg_prob[i]); cur_ctype = "negative"
                rparts.append(f"NEG[{neg_reason[i]}]")
            else:
                cap_hit[i] = bool(cap_hit[i] or ch)
                rparts.append(f"NEG_blocked:{greason}")

        elif cfg.spike_classifier_enabled and spk_prob[i] >= cfg.SPK_THRESH:
            boost = cfg.SPK_9_16_BOOST if period == "9_16" else 1.0
            if original > cfg.SPK_MIN_ORIGINAL:
                lift = min(cfg.SPK_LIFT_RATIO * original, cfg.SPK_LIFT_ABS) * boost
                a = +lift
                target = original + a
                passed, ch, greason = guard_pass(a, original, target, cfg, "spike")
                if passed:
                    cur = target; amt = a; is_applied = True
                    cur_conf = float(spk_prob[i]); cur_ctype = "spike"
                    rparts.append(f"SPK[{spk_reason[i]}]")
                else:
                    cap_hit[i] = bool(cap_hit[i] or ch)
                    rparts.append(f"SPK_blocked:{greason}")
            else:
                rparts.append("SPK_skip:original<=0")

        if is_applied:
            should_rb, rb = evaluate_rollback(cur, original, cur_conf, cfg, cur_ctype, True)
            if should_rb:
                cur = original; amt = 0.0; is_applied = False
                rollback_reason[i] = rb
                rparts.append(f"ROLLBACK:{rb}")

        corrected[i] = cur
        correction_amount[i] = amt
        applied[i] = is_applied
        confidence[i] = cur_conf
        ctype_used[i] = cur_ctype
        correction_reason[i] = "; ".join(rparts) if rparts else "no_action"

    return {
        "corrected": corrected, "correction_amount": correction_amount,
        "applied": applied, "cap_hit": cap_hit, "confidence": confidence,
        "ctype_used": ctype_used, "correction_reason": correction_reason,
        "rollback_reason": rollback_reason,
    }


# ---------------------------------------------------------------------------
# Output assembly + reports
# ---------------------------------------------------------------------------
def _assemble_shadow_df(target_df, dec, cfg, target_date, run_id):
    out = pd.DataFrame()
    out["business_day"] = target_df["business_day"].values
    out["ds"] = target_df["ds"].values
    out["hour_business"] = target_df["hour_business"].astype(int).values
    out["period"] = target_df["period"].values
    out["original_pred"] = target_df["original_pred"].round(3).values
    out["shadow_corrected_pred"] = np.round(dec["corrected"], 3)
    out["correction_amount"] = np.round(dec["correction_amount"], 3)
    out["negative_probability"] = np.round(np.zeros(len(target_df)), 4)  # filled below
    out["spike_probability"] = np.round(np.zeros(len(target_df)), 4)
    out["spike_type"] = dec.get("spike_type", ["none"] * len(target_df))
    out["correction_reason"] = dec["correction_reason"]
    out["confidence"] = np.round(dec["confidence"], 4)
    out["applied"] = dec["applied"]
    out["rollback_reason"] = dec["rollback_reason"]
    out["rollback_reason"] = out["rollback_reason"].fillna("none")
    # No empty cells (read back as NaN): normalize blank rollback reasons to "none"
    out["rollback_reason"] = out["rollback_reason"].replace("", "none")
    out["shadow_only"] = True
    out["model_version"] = cfg.model_version
    out["run_id"] = run_id
    out["cap_hit"] = dec["cap_hit"]  # extra column for cap-existence test
    # No-NaN guarantee
    num_cols = ["original_pred", "shadow_corrected_pred", "correction_amount",
                "negative_probability", "spike_probability", "confidence"]
    for c in num_cols:
        out[c] = out[c].fillna(0.0)
    out["spike_type"] = out["spike_type"].fillna("none")
    out["correction_reason"] = out["correction_reason"].fillna("no_action")
    out["rollback_reason"] = out["rollback_reason"].fillna("")
    return out


def _write_outputs(shadow_df, summary, target_date, out_dir: Path, cfg, meta, run_id, neg_prob, spk_prob, spk_type):
    out_dir.mkdir(parents=True, exist_ok=True)
    # inject probabilities
    shadow_df = shadow_df.copy()
    shadow_df["negative_probability"] = np.round(np.asarray(neg_prob, float), 4)
    shadow_df["spike_probability"] = np.round(np.asarray(spk_prob, float), 4)
    shadow_df["spike_type"] = spk_type
    # final no-NaN sweep
    for c in shadow_df.columns:
        if shadow_df[c].dtype.kind in ("f", "c"):
            shadow_df[c] = shadow_df[c].fillna(0.0)
        elif shadow_df[c].dtype == object:
            shadow_df[c] = shadow_df[c].fillna("")
    shadow_df.to_csv(out_dir / "shadow_predictions.csv", index=False)

    # shadow_report.json
    report = {
        "pipeline": "extreme_price_shadow",
        "target_date": target_date,
        "run_id": run_id,
        "model_version": cfg.model_version,
        "shadow_only": True,
        "enabled": True,
        "status": "ok" if not meta.get("degraded") else "degraded",
        "degraded_reason": meta.get("reason", ""),
        "history_days": meta.get("history_days", 0),
        "has_risk_pack": meta.get("has_risk_pack", False),
        "correction_cap_abs": cfg.CAP_ABS,
        "correction_cap_ratio": cfg.CAP_RATIO,
        "config": {k: getattr(cfg, k) for k in [
            "negative_classifier_enabled", "spike_classifier_enabled",
            "residual_corrector_enabled", "NEG_THRESH", "NEG_ACT_PRED_CAP",
            "SPK_THRESH", "CAP_ABS", "ROLLBACK_MIN_CONF",
        ]},
        "summary": summary,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(out_dir / "shadow_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    # shadow_report.md
    _write_markdown(out_dir / "shadow_report.md", shadow_df, report, cfg)

    # rollback_report.json
    rb_rows = shadow_df[~shadow_df["rollback_reason"].astype(str).isin(["", "none"])]
    rollback_report = {
        "target_date": target_date,
        "run_id": run_id,
        "shadow_only": True,
        "rollback_count": int(len(rb_rows)),
        "correction_cap_abs": cfg.CAP_ABS,
        "cap_hit_count": int(shadow_df["cap_hit"].astype(bool).sum()) if "cap_hit" in shadow_df else 0,
        "applied_count": int(shadow_df["applied"].astype(bool).sum()),
        "rollbacks": rb_rows[["hour_business", "period", "original_pred",
                              "shadow_corrected_pred", "rollback_reason"]].to_dict("records"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(out_dir / "rollback_report.json", "w", encoding="utf-8") as f:
        json.dump(rollback_report, f, indent=2, ensure_ascii=False, default=str)
    return shadow_df


def _write_markdown(path: Path, shadow_df, report: dict, cfg):
    lines = []
    lines.append(f"# Extreme Price Shadow Report — {report['target_date']}")
    lines.append("")
    lines.append(f"- run_id: `{report['run_id']}`")
    lines.append(f"- model_version: `{report['model_version']}`")
    lines.append(f"- shadow_only: **{report['shadow_only']}** (never written to submission_ready.csv)")
    lines.append(f"- status: **{report['status']}**")
    if report["degraded_reason"]:
        lines.append(f"- degraded_reason: {report['degraded_reason']}")
    lines.append(f"- history_days used for classifier training: {report['history_days']}")
    lines.append(f"- risk pack available: {report['has_risk_pack']}")
    lines.append("")
    lines.append("## Correction summary")
    s = report["summary"]
    lines.append(f"- rows: {s.get('n')}")
    lines.append(f"- applied: {s.get('applied_count')}")
    lines.append(f"- cap_hit: {s.get('cap_hit_count')}")
    lines.append(f"- rollback: {s.get('rollback_count')}")
    lines.append(f"- neg_corrected: {s.get('neg_corrected')}")
    lines.append(f"- spk_corrected: {s.get('spk_corrected')}")
    lines.append("")
    lines.append(f"## Safety")
    lines.append(f"- correction cap (abs): {cfg.CAP_ABS}")
    lines.append(f"- correction cap (ratio): {cfg.CAP_RATIO}")
    lines.append(f"- rollback min confidence: {cfg.ROLLBACK_MIN_CONF}")
    lines.append("")
    lines.append("## Per-hour (applied only)")
    applied = shadow_df[shadow_df["applied"].astype(bool)]
    if len(applied) == 0:
        lines.append("_No corrections applied._")
    else:
        lines.append("| hour | period | original | corrected | amount | neg_prob | spk_prob | reason |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for _, r in applied.iterrows():
            lines.append(
                f"| {int(r['hour_business'])} | {r['period']} | {r['original_pred']:.1f} | "
                f"{r['shadow_corrected_pred']:.1f} | {r['correction_amount']:+.1f} | "
                f"{r['negative_probability']:.3f} | {r['spike_probability']:.3f} | {r['correction_reason']} |")
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Core run
# ---------------------------------------------------------------------------
def run_extreme_price_shadow(args: Any) -> dict:
    """Run the controlled shadow for (a) args.date, or (b) each day in a range.

    Returns a manifest. NEVER writes final/ or submission_ready.csv.
    Raises nothing fatal to the caller — callers should use run_extreme_price_shadow_safe.
    """
    cfg = load_config(getattr(args, "extreme_price_shadow_config", None))
    # CLI flag forces enable regardless of YAML
    cfg.enabled = True
    if getattr(args, "shadow_only", False):
        cfg.shadow_only = True

    ledger_root = Path(getattr(args, "ledger_root", "outputs/ledger"))
    runs_root = Path(getattr(args, "runs_root", "outputs/runs"))

    target_dates = []
    if getattr(args, "date", None):
        target_dates = [args.date]
    elif getattr(args, "start", None) and getattr(args, "end", None):
        d0 = pd.Timestamp(args.start)
        d1 = pd.Timestamp(args.end)
        target_dates = [(d0 + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
                        for i in range((d1 - d0).days + 1)]

    if not target_dates:
        raise ValueError("extreme_price_shadow requires --date or --start/--end")

    per_day = {}
    for td in target_dates:
        per_day[td] = _run_one_day(td, ledger_root, runs_root, cfg)
    manifest = {
        "pipeline": "extreme_price_shadow",
        "enabled": True,
        "shadow_only": True,
        "target_dates": target_dates,
        "results": per_day,
        "status": "ok",
    }
    if any(r.get("status") != "ok" for r in per_day.values()):
        manifest["status"] = "degraded"
    return manifest


def _run_one_day(target_date: str, ledger_root: Path, runs_root: Path, cfg) -> dict:
    out_dir = runs_root / target_date / "extreme_price_shadow"
    run_id = cfg.build_run_id(target_date)
    p3cfg = cfg.to_p3_config()

    buffer_df, meta = build_shadow_features(target_date, ledger_root, cfg, runs_root=runs_root)
    if buffer_df.empty:
        # Degraded: emit a minimal-but-valid 24-row CSV so the contract still holds
        return _emit_degraded(target_date, out_dir, cfg, run_id, meta,
                              reason=meta.get("reason", "no data"))

    target_df = buffer_df[buffer_df["target_day"] == target_date].reset_index(drop=True)
    history_df = buffer_df[buffer_df["target_day"] < target_date].reset_index(drop=True)
    # Only rows with a real actual can be used as classifier training labels.
    history_df = history_df.dropna(subset=["actual"]).reset_index(drop=True)

    neg_prob, neg_status = _fit_predict(
        history_df, target_df, _neg_feat_build, _neg_label, p3cfg, min_samples=20, min_pos=3)
    spk_prob, spk_status = _fit_predict(
        history_df, target_df, _spk_feat_build, _spk_label, p3cfg, min_samples=20, min_pos=2)
    spk_type = ["none"] * len(target_df)
    neg_reason = _neg_reasons(target_df, p3cfg)
    spk_reason = _spk_reasons(target_df, p3cfg)

    dec = _decide(target_df, neg_prob, neg_reason, spk_prob, spk_type, spk_reason, p3cfg)

    shadow_df = _assemble_shadow_df(target_df, dec, cfg, target_date, run_id)
    summary = {
        "n": int(len(target_df)),
        "applied_count": int(dec["applied"].sum()),
        "cap_hit_count": int(dec["cap_hit"].sum()),
        "rollback_count": int(sum(1 for r in dec["rollback_reason"] if r)),
        "neg_corrected": int(sum(1 for c in dec["ctype_used"] if c == "negative")),
        "spk_corrected": int(sum(1 for c in dec["ctype_used"] if c == "spike")),
        "resid_corrected": 0,
        "neg_classifier_status": neg_status,
        "spk_classifier_status": spk_status,
    }
    shadow_df = _write_outputs(shadow_df, summary, target_date, out_dir, cfg, meta, run_id,
                               neg_prob, spk_prob, spk_type)
    return {
        "status": "ok" if not meta.get("degraded") else "degraded",
        "reason": meta.get("reason", ""),
        "history_days": meta.get("history_days", 0),
        "summary": summary,
        "out_dir": str(out_dir),
        "shadow_predictions_csv": str(out_dir / "shadow_predictions.csv"),
    }


def _emit_degraded(target_date, out_dir, cfg, run_id, meta, reason: str) -> dict:
    """Emit a valid 24-row degraded shadow output (no corrections claimed)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # Build 24 placeholder rows keyed by hour 1..24
    rows = []
    for h in range(1, 25):
        period = "1_8" if h <= 8 else ("9_16" if h <= 16 else "17_24")
        rows.append({
            "business_day": target_date, "ds": f"{target_date} {h:02d}:00:00",
            "hour_business": h, "period": period,
            "original_pred": 0.0, "shadow_corrected_pred": 0.0,
            "correction_amount": 0.0, "negative_probability": 0.0,
            "spike_probability": 0.0, "spike_type": "none",
            "correction_reason": f"degraded:{reason}", "confidence": 0.0,
            "applied": False, "rollback_reason": "none", "shadow_only": True,
            "model_version": cfg.model_version, "run_id": run_id, "cap_hit": False,
        })
    shadow_df = pd.DataFrame(rows)
    summary = {"n": 24, "applied_count": 0, "cap_hit_count": 0, "rollback_count": 0,
               "neg_corrected": 0, "spk_corrected": 0, "resid_corrected": 0,
               "degraded": True}
    _write_outputs(shadow_df, summary, target_date, out_dir, cfg,
                   {"degraded": True, "reason": reason, "history_days": 0,
                    "has_risk_pack": False}, run_id,
                   np.zeros(24), np.zeros(24), ["none"] * 24)
    return {
        "status": "degraded", "reason": reason, "history_days": 0,
        "summary": summary, "out_dir": str(out_dir),
        "shadow_predictions_csv": str(out_dir / "shadow_predictions.csv"),
    }


def run_extreme_price_shadow_safe(args: Any) -> dict:
    """Safe wrapper for main.py hook. Never raises; logs failures (never silent)."""
    try:
        return run_extreme_price_shadow(args)
    except Exception as e:  # pragma: no cover - defensive
        logger.error(f"[extreme_price_shadow] non-fatal failure (main chain untouched): {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            "pipeline": "extreme_price_shadow",
            "enabled": True,
            "status": "failed",
            "error": str(e),
            "shadow_only": True,
            "final_contaminated": False,
            "main_chain_affected": False,
        }


def run_ledger_extreme_price_shadow(args: Any) -> dict:
    """Pipeline entry for --pipeline extreme_price_shadow."""
    return run_extreme_price_shadow(args)
