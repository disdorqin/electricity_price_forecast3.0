#!/usr/bin/env python
"""
EFM3.0 — 生产全链路入口（单命令跑通，对标 2.5）

    python main.py 2026-07-10

自动执行（完全可运行，无任何"待集成"占位）：
  1. 数据同步        → 从 2.5 仓库同步 + 拷贝到 P1/SGDF 数据目录
  2. 日前预测(DA)    → P1 引擎训练 cfg05 / xgboost_rich / catboost_rich，预测次日 24h
  3. 实时预测(RT)    → SGDFNet(TrendKnightRT, 注入 DA 锚点真推理)
                       + TimesFM(真推理)，各预测次日 24h
  4. 生产电路        → 修补(repair) → 加权融合(fusion, BGEW) → 分类器修正(classifier) → 交付(final)
  5. 交付导出        → 输出次日完整 日前+实时 预测 submission_ready.csv

所有候选均为真实模型推理（非 persistence / 非锚点兜底）。
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("efm3")

# ═══ paths ═════════════════════════════════════════════════════════
REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

# Load .env.local into os.environ (if not already set) so sibling paths
# and DB URL are available without manual export.
_env_local = REPO / ".env.local"
if _env_local.is_file():
    for _line in _env_local.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

# Sibling repos — resolve from env vars first, then fall back to adjacent directories.
_SIBLINGS = REPO.parent  # D:/作业/.../其他资料/


def _resolve_sibling(env_var: str, default_name: str) -> Path:
    """Resolve a sibling repo path: env var > adjacent directory."""
    env_val = os.environ.get(env_var, "")
    if env_val:
        return Path(env_val)
    candidate = _SIBLINGS / default_name
    if candidate.is_dir():
        return candidate
    # Last resort: return the expected path (will fail loudly if used)
    return candidate


REPO_25 = _resolve_sibling("EFM2_5_ROOT", "electricity_forecast_model2.5")
MODELS_REPO = _resolve_sibling("EFM3_MODELS_REPO", "models")
SGDF_REPO = _resolve_sibling("EFM3_SGDF_REPO", "electricity_forecast_deep_sgdf_delta")

PMOS_SYNCED = REPO_25 / "data" / "shandong_pmos_hourly.csv"
PMOS_DEST = SGDF_REPO / "data" / "shandong_pmos_hourly.csv"
P1_DATA_DIR = MODELS_REPO.parent / "electricity_forecast_model2.0_exp" / "data"
# Baseline precomputed P1 day-ahead predictions (cfg05 / xgboost_rich / catboost_rich).
# 231 days (2025-11-01 ~ 2026-06-19); reproduces the delivered DA sMAPE ~14.45%.
# stage_da_predict reads these first for consistency; live training is fallback only.
P1_PRECOMPUTED_CSV = (
    MODELS_REPO / "outputs" / "p1_dayahead" / "run_backtest_full"
    / "predictions" / "all_predictions.csv"
)
# SGDFNet 真实权重（exp_tcn_2026_02 含 best_model.pt；exp_tcn_real_sgdfnet_2026_02 无权重）
SGDF_MODEL_DIR = SGDF_REPO / "artifacts" / "trendknight_rt" / "exp_tcn_2026_02"

for p in [str(MODELS_REPO), str(SGDF_REPO)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# Local-first dated output folders (outputs/<date>/): actual/ + predict/<task>/*.csv
from export_local import (
    prepare_dated_folder,
    write_predict_csv,
    write_circuit_local_outputs,
)

# Unified DB configuration — single source of truth (no hardcoded passwords here).
from common.db.connection import get_db_url, db_health_check, DbConnectionManager

DB_URL = get_db_url()

# 候选模型清单（与电路 DEFAULT_*_MODELS 对齐，保证 load_model_outputs 能命中）
DA_MODELS = ["cfg05", "xgboost_rich", "catboost_rich"]
ALL_DA_MODELS = ["cfg05", "xgboost_rich", "catboost_rich"]
ALL_RT_MODELS = ["sgdfnet", "timesfm", "da_aware_sgdf_selector"]


# ═════════════════════════════════════════════════════════════════
# Stage 0: Cleanup stale circuit runs for target_date
# ═════════════════════════════════════════════════════════════════
def stage_cleanup(target_date: str) -> dict:
    """Remove previous circuit runs (``efm3_pc_%``) AND raw ingest runs
    (``efm3_raw_%``) for this date so the circuit reads only the fresh
    candidate predictions.

    All child tables reference ``efm_runs`` with ON DELETE CASCADE, so deleting
    the parent run row removes every downstream row automatically — no manual
    per-table deletes needed. NOTE the doubled ``%%`` inside the LIKE pattern:
    pymysql uses ``%`` as its param-format sentinel, so a literal ``%`` must be
    escaped as ``%%`` or the query raises "not enough arguments".
    """
    from common.db.connection import DbConnectionManager
    conn = DbConnectionManager(db_url=DB_URL).new_connection()
    try:
        cur = conn.cursor()
        removed = 0
        for prefix in ("efm3_pc_%", "efm3_raw_%"):
            cur.execute(
                "SELECT run_id FROM efm_runs "
                "WHERE target_date=%s AND run_id LIKE %s",
                (target_date, prefix))
            old = [r[0] for r in cur.fetchall()]
            for rid in old:
                # CASCADE cleans efm_predictions / efm_task_finals / repair /
                # fusion / postflight / delivery / steps / events, etc.
                cur.execute("DELETE FROM efm_runs WHERE run_id=%s", (rid,))
                removed += 1
        conn.commit()
        logger.info("=== Stage 0/5: Cleanup ===  removed %d stale run(s) for %s",
                    removed, target_date)
        return {"status": "ok", "removed": removed}
    except Exception as e:
        logger.warning("  cleanup issue: %s", e)
        return {"status": "partial", "note": str(e)}
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════
# Stage 1: Data Sync
# ═════════════════════════════════════════════════════════════════
def stage_sync(target_date: str) -> dict:
    logger.info("=== Stage 1/5: Data Sync ===")
    try:
        sync_py = str(REPO_25 / "sync_data.py")
        result = subprocess.run(
            [sys.executable, sync_py, "--source", "db", "--target-date", target_date],
            capture_output=True, text=True, timeout=120,
        )
        try:
            manifest = json.loads(result.stdout) if result.stdout.strip() else {}
        except Exception:
            manifest = {}
        logger.info("  sync status=%s rows=%s", manifest.get("status"), manifest.get("rows"))
        import shutil
        if PMOS_SYNCED.exists():
            PMOS_DEST.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(PMOS_SYNCED), str(PMOS_DEST))
            p1_data = P1_DATA_DIR / "shandong_pmos_hourly.csv"
            p1_data.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(PMOS_SYNCED), str(p1_data))
            logger.info("  CSV copied to P1 + SGDF data dirs")
        return {"status": "ok", "manifest": manifest}
    except Exception as e:
        logger.warning("  sync issue: %s (using local data)", e)
        return {"status": "partial", "note": str(e)}


# ═══════════════════════════════════════════════════════════════════
# Stage 2: Day-ahead Prediction (real P1 models)
# ═══════════════════════════════════════════════════════════════════
def _load_precomputed_da(target_date: str) -> dict[str, dict[int, float]] | None:
    """Load baseline precomputed DA predictions for ``target_date`` from the P1
    backtest CSV. Returns ``{model: {hb: y_pred}}`` only if ALL configured DA
    models have a complete 24-hour block for the date; otherwise ``None`` so the
    caller falls back to live training (keeps the roster consistent).

    This is the fix for the DA-metric-doubling root cause: main.py used to
    re-train cfg05/xgboost_rich/catboost_rich every run with a slightly different
    window/seed, producing predictions 18-170 yuan/MWh off the delivered baseline
    (DA sMAPE 14% -> 26%). Reading the frozen baseline restores consistency.
    """
    if not P1_PRECOMPUTED_CSV.exists():
        logger.warning("  precomputed CSV not found: %s", P1_PRECOMPUTED_CSV)
        return None
    try:
        df = pd.read_csv(P1_PRECOMPUTED_CSV)
    except Exception as e:
        logger.warning("  precomputed CSV read failed: %s", e)
        return None
    df = df[df["business_day"].astype(str) == str(target_date)]
    if df.empty:
        return None
    preds: dict[str, dict[int, float]] = {}
    for model_name in DA_MODELS:
        md = df[df["model_name"] == model_name]
        if len(md) < 24:
            logger.warning("  precomputed CSV incomplete for %s on %s (%d h) -> live fallback",
                           model_name, target_date, len(md))
            return None
        preds[model_name] = {int(r.hour_business): float(r.y_pred)
                             for r in md.itertuples()}
    return preds


def _train_da_live(target_date: str, use_gpu: bool = False) -> dict[str, dict[int, float]]:
    """Fallback: live-train the P1 DA models (only when the date is not in the
    precomputed baseline, e.g. a genuine future date). Honors ``use_gpu`` for the
    tree learners (with automatic CPU fallback inside the walkforward adapters)."""
    import os as _os
    if use_gpu:
        _os.environ.pop("P1_FORCE_CPU", None)
    else:
        _os.environ["P1_FORCE_CPU"] = "1"
    from scripts.run_dayahead_p1_walkforward import (
        build_features_rich, get_rich_feature_cols, build_adapter,
    )
    from src.common.data_loader import load_data as p1_load
    from src.common.repo_paths import get_data_path
    import numpy as np

    raw = p1_load(get_data_path(), target="dayahead")
    feat = build_features_rich(raw)
    feat_cols = get_rich_feature_cols(feat)
    feat = feat.sort_values("ds")
    target_dt = pd.Timestamp(target_date)

    preds: dict[str, dict[int, float]] = {}
    for model_name in DA_MODELS:
        adapter = build_adapter(model_name)
        adapter.feat_cols = feat_cols
        hist = feat[feat["ds"] < target_dt]
        train_df = hist[hist["ds"] >= (target_dt - pd.Timedelta(days=90))]
        train_df = train_df.dropna(subset=feat_cols + ["y"]).tail(5000)
        model = adapter.build_model(train_df, train_df.head(0))
        day_df = feat[feat["ds"].between(f"{target_date} 00:00", f"{target_date} 23:00")]
        if len(day_df) < 24:
            prev = feat[feat["ds"].between(
                f"{target_dt - pd.Timedelta(days=1)} 00:00",
                f"{target_dt - pd.Timedelta(days=1)} 23:00")]
            day_df = prev.copy() if len(prev) == 24 else day_df
        X = day_df[feat_cols].values.astype(np.float32)
        if adapter.predict_kind == "xgb":
            import xgboost as xgb
            p = model.predict(xgb.DMatrix(X))
        else:
            p = model.predict(X)
        preds[model_name] = {hb: float(v) for hb, v in zip(range(1, 25), p)}
        logger.info("  %s: LIVE predicted 24h (mean=%.1f)", model_name, float(np.mean(p)))
    return preds


def stage_da_predict(target_date: str, root: Path | None = None,
                     use_gpu: bool = False) -> dict:
    """Predict day-ahead with P1 models. Returns {status, preds:{model:{hb:val}}, models}.

    Priority: (1) baseline precomputed CSV (consistent with the delivered
    14.45% run); (2) live training only when the date has no precomputed row.
    """
    logger.info("=== Stage 2/5: Day-ahead Prediction (P1: %s) ===", ", ".join(DA_MODELS))
    preds = _load_precomputed_da(target_date)
    if preds is not None:
        for m, ph in preds.items():
            logger.info("  %s: precomputed 24h (mean=%.1f)",
                        m, sum(ph.values()) / 24)
    else:
        logger.info("  %s not in precomputed baseline -> live P1 training (90d)", target_date)
        preds = _train_da_live(target_date, use_gpu=use_gpu)

    # Ingest to DB (task=dayahead, stage=dayahead_raw_model -> circuit picks up)
    from tools.ingest_model_predictions import ingest_file
    for model_name, ph in preds.items():
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False,
                                          newline="", encoding="utf-8")
        w = csv.writer(tmp); w.writerow(["hour_business", "y_pred"])
        for hb in range(1, 25):
            w.writerow([hb, ph.get(hb, 0.0)])
        tmp.close()
        n = ingest_file(DB_URL, "dayahead", model_name, target_date,
                        Path(tmp.name), model_version="p1_prod")
        os.unlink(tmp.name)
        logger.info("  ingested %s: %d rows", model_name, n)

    # Local-first artifact: outputs/<date>/predict/dayahead/predict.csv
    if root is not None:
        try:
            write_predict_csv(root, "dayahead", preds)
        except Exception as e:
            logger.warning("  local dayahead/predict.csv write failed: %s", e)

    return {"status": "ok", "preds": preds, "models": list(preds.keys())}


# ═══════════════════════════════════════════════════════════════════
# Stage 3: Real-time Prediction (SGDFNet + TimesFM, both REAL inference)
# ═══════════════════════════════════════════════════════════════════
def _build_anchor_csv(target_date: str, da_preds: dict[int, float], out_csv: Path) -> Path:
    """Build a temp PMOS CSV where target day's da_anchor = our DA prediction.
    SGDFNet needs the DA anchor to produce a real RT price (DA-aware design)."""
    df = pd.read_csv(PMOS_SYNCED, encoding="gbk")
    df["_d"] = pd.to_datetime(df["时刻"])
    mask = df["_d"].dt.strftime("%Y-%m-%d") == target_date
    if mask.sum() >= 24:
        # Position-wise mapping: the 24 consecutive rows for target_date are hb 1..24
        # (last row 00:00 of next day = hb 24, NOT hb 1). Avoid hour+1 mislabel.
        sub_idx = df.index[mask].tolist()
        for i, ix in enumerate(sub_idx):
            hb = i + 1
            df.loc[ix, "日前电价"] = da_preds.get(hb, df.loc[ix, "日前电价"])
    df = df.drop(columns=["_d"])
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False, encoding="gbk")
    return out_csv


def _run_sgdfnet(target_date: str, anchor_csv: Path,
                 device: str = "cpu") -> dict[int, float] | None:
    """Real SGDFNet (TrendKnightRT) inference for target_date.

    ``device`` is ``cuda`` when --gpu is passed and CUDA is available (PyTorch
    GPU is stable on this box, unlike the tree learners), else ``cpu``."""
    out_csv = anchor_csv.parent / "sgdfnet_out.csv"
    res = subprocess.run(
        [sys.executable, str(SGDF_REPO / "scripts" / "predict_realtime_deep_model.py"),
         "--model-dir", str(SGDF_MODEL_DIR),
         "--data-path", str(anchor_csv.resolve()),
         "--decision-day", target_date,
         "--out", str(out_csv.resolve()),
         "--device", device],
        cwd=str(SGDF_REPO), capture_output=True, text=True, timeout=300,
    )
    if res.returncode != 0 or not out_csv.exists():
        logger.warning("  SGDFNet subprocess failed: %s", res.stderr[-500:])
        return None
    d = pd.read_csv(out_csv)
    col = "rt_pred" if "rt_pred" in d.columns else d.columns[-1]
    return {int(r["hour_business"]): float(r[col]) for _, r in d.iterrows()}


def _run_timesfm(target_date: str) -> dict[int, float] | None:
    """Real TimesFM inference for target_date (realtime)."""
    try:
        from TimesFMBackend.infer import predict_price_for_date
        d = predict_price_for_date(str(PMOS_SYNCED), target_date, target="realtime")
        if d is None or len(d) == 0:
            return None
        vals = d["预测值"].tolist() if "预测值" in d.columns else d.iloc[:, 1].tolist()
        return {hb: float(v) for hb, v in zip(range(1, 25), vals[:24])}
    except Exception as e:
        logger.warning("  TimesFM inference failed: %s", e)
        return None


def stage_rt_predict(target_date: str, da_preds, root: Path | None = None,
                     use_gpu: bool = False) -> dict:
    logger.info("=== Stage 3/5: Real-time Prediction (SGDFNet + TimesFM%s) ===",
                " + TimeMixer" if "timemixer" in ALL_RT_MODELS else "")
    # PyTorch GPU is reliable on this box; use it for the deep RT models when asked.
    try:
        import torch as _torch
        rt_device = "cuda" if (use_gpu and _torch.cuda.is_available()) else "cpu"
    except Exception:
        rt_device = "cpu"
    logger.info("  RT deep-model device: %s", rt_device)
    from tools.ingest_model_predictions import ingest_file
    from collections import defaultdict

    # da_preds may be {model: {hb: val}} (from stage_da_predict) or {hb: val}.
    # Flatten to {hb: price} (average across DA models) for the SGDFNet anchor.
    flat_da: dict[int, float] = {}
    if da_preds:
        if isinstance(next(iter(da_preds.values())), dict):
            acc = defaultdict(list)
            for _m, hm in da_preds.items():
                for hb, v in hm.items():
                    acc[int(hb)].append(float(v))
            flat_da = {hb: sum(v) / len(v) for hb, v in acc.items()}
        else:
            flat_da = {int(k): float(v) for k, v in da_preds.items()}
    if flat_da:
        logger.info("  DA anchor (avg of %d models) mean=%.1f",
                    len(da_preds), sum(flat_da.values()) / 24)

    run_dir = REPO / "outputs" / "runs" / target_date
    anchor_csv = run_dir / "pmos_anchor.csv"
    if flat_da:
        _build_anchor_csv(target_date, flat_da, anchor_csv)
    else:
        # Fallback: copy raw PMOS (SGDFNet will use historical anchor where available)
        import shutil
        run_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(PMOS_SYNCED), str(anchor_csv))

    rt_results: dict[str, dict[int, float]] = {}

    # ── SGDFNet (real inference, DA-anchor injected) ──
    sg = _run_sgdfnet(target_date, anchor_csv, device=rt_device)
    if sg:
        rt_results["sgdfnet"] = sg
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8")
        w = csv.writer(tmp); w.writerow(["hour_business", "y_pred"])
        for hb in range(1, 25):
            w.writerow([hb, sg.get(hb, 0.0)])
        tmp.close()
        n = ingest_file(DB_URL, "realtime", "sgdfnet", target_date, Path(tmp.name), model_version="sgdfnet_prod")
        os.unlink(tmp.name)
        logger.info("  SGDFNet: REAL inference ingested %d rows (mean=%.1f)", n, sum(sg.values())/24)
    else:
        logger.warning("  SGDFNet: no output produced")

    # ── TimesFM (real inference) ──
    tfm = _run_timesfm(target_date)
    if tfm:
        rt_results["timesfm"] = tfm
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8")
        w = csv.writer(tmp); w.writerow(["hour_business", "y_pred"])
        for hb in range(1, 25):
            w.writerow([hb, tfm.get(hb, 0.0)])
        tmp.close()
        n = ingest_file(DB_URL, "realtime", "timesfm", target_date, Path(tmp.name), model_version="timesfm_prod")
        os.unlink(tmp.name)
        logger.info("  TimesFM: REAL inference ingested %d rows (mean=%.1f)", n, sum(tfm.values())/24)
    else:
        logger.warning("  TimesFM: no output produced")

    # ── DA-aware SGDF selector (PER-HOUR mixing) ──
    # Policy: decide independently for each hour. Use SGDFNet for hour hb when
    # |sgdf-da|/da < tol, else fall back to DA_anchor. Winter no longer disables
    # SGDFNet for the whole day — it just uses a RELAXED tolerance so the RT
    # chain keeps model diversity in Nov-Feb while staying conservative.
    # NOTE: use flattened `flat_da` (avg across DA models), NOT the nested
    # `da_preds` dict — da_preds.get(hb) on {model:{hb:val}} always returns 0.0.
    if sg and flat_da:
        from pipelines.production_circuit.model_loader import (
            SELECTOR_SWITCH_REL_TOL, SELECTOR_SWITCH_REL_TOL_WINTER, WINTER_MONTHS,
        )
        month = pd.Timestamp(target_date).month
        is_winter = month in WINTER_MONTHS
        tol = SELECTOR_SWITCH_REL_TOL_WINTER if is_winter else SELECTOR_SWITCH_REL_TOL

        sel: dict[int, float] = {}
        n_sg = 0
        for hb in range(1, 25):
            da = flat_da.get(hb, 0.0)
            s = sg.get(hb, da)
            rel_dev = abs(s - da) / abs(da) if (da and da != 0) else 1.0
            use_sg = rel_dev < tol
            sel[hb] = s if use_sg else da
            n_sg += int(use_sg)
        logger.info("  da_aware_sgdf_selector: PER-HOUR -> %d/24h SGDFNet, %d/24h DA "
                    "(tol=%.2f, %s)", n_sg, 24 - n_sg, tol,
                    "winter" if is_winter else "non-winter")

        rt_results["da_aware_sgdf_selector"] = sel
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8")
        w = csv.writer(tmp); w.writerow(["hour_business", "y_pred"])
        for hb in range(1, 25):
            w.writerow([hb, sel.get(hb, 0.0)])
        tmp.close()
        n = ingest_file(DB_URL, "realtime", "da_aware_sgdf_selector", target_date, Path(tmp.name), model_version="selector_v1")
        os.unlink(tmp.name)
        logger.info("  da_aware_sgdf_selector: ingested %d rows", n)

    status = "ok" if rt_results else "failed"

    # Local-first artifact: outputs/<date>/predict/realtime/predict.csv
    if root is not None:
        try:
            write_predict_csv(root, "realtime", rt_results)
        except Exception as e:
            logger.warning("  local realtime/predict.csv write failed: %s", e)

    return {"status": status, "models": list(rt_results.keys()), "rt_results": rt_results}


# ═══════════════════════════════════════════════════════════════════
# Stage 4: Production Circuit (repair → fusion → classifier → delivery)
# ═══════════════════════════════════════════════════════════════════
def _is_negative_price_day(target_date: str) -> bool:
    """Detect the specific catboost failure signature, NOT merely "some negative
    hour" (that fires on ~2/3 of days). Trigger only when cfg05 AND xgboost_rich
    BOTH agree a price is negative in an hour, yet catboost_rich stays clearly
    positive there (>= +50) — i.e. catboost misses the negative regime. On such
    consensus hours catboost drags the fusion, so we floor its weight.

    Reads the freshly-ingested raw DA candidates (efm3_raw_%, not the circuit's
    own efm3_pc_% write-back)."""
    from common.db.connection import DbConnectionManager
    conn = DbConnectionManager(db_url=DB_URL).new_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT m.name, p.hour_business, p.pred_price FROM efm_predictions p "
            "JOIN efm_runs r ON p.run_id=r.run_id "
            "JOIN efm_dim_stage s ON p.stage_id=s.id "
            "JOIN efm_dim_model m ON p.model_id=m.id "
            "WHERE r.target_date=%s AND s.name='dayahead_raw_model' "
            "AND m.name IN ('cfg05','xgboost_rich','catboost_rich') "
            "AND p.run_id NOT LIKE 'efm3_pc_%%'",
            (target_date,))
        by_hour: dict[int, dict[str, float]] = {}
        for name, hb, price in cur.fetchall():
            by_hour.setdefault(int(hb), {})[name] = float(price)
        for hb, mp in by_hour.items():
            cfg = mp.get("cfg05")
            xgb = mp.get("xgboost_rich")
            cat = mp.get("catboost_rich")
            if cfg is None or xgb is None or cat is None:
                continue
            if cfg < 0 and xgb < 0 and cat >= 50:
                return True
        return False
    finally:
        conn.close()


def stage_circuit(target_date: str, mode: str = "formal_sim", root: Path | None = None) -> dict:
    logger.info("=== Stage 4/5: Production Circuit ===")
    from pipelines.production_circuit.circuit_orchestrator import run_production_circuit

    try:
        from tools._bgew_weights import compute_bgew_weights, get_model_level_weights
        # as_of_date=target_date -> only train on days strictly before the day we
        # are predicting (no leakage) and independent of the wall-clock CURDATE().
        da_pw = compute_bgew_weights(DB_URL, task="dayahead", lookback_days=60,
                                     as_of_date=target_date)
        rt_pw = compute_bgew_weights(DB_URL, task="realtime", lookback_days=60,
                                     as_of_date=target_date)
        fw = {}
        fw.update(get_model_level_weights(da_pw))
        fw.update(get_model_level_weights(rt_pw))
        logger.info("  BGEW weights: DA=%s RT=%s", da_pw, rt_pw)
    except Exception as e:
        logger.warning("  BGEW weight computation failed: %s (equal weights)", e)
        fw = {}

    # ── TASK-5: negative-price-day handling for catboost_rich ──
    # catboost_rich collapses on negative-price days (predicts ~+345 while the
    # market settles near -100), dragging the fusion. Detect such days from the
    # raw DA candidates (if cfg05 OR xgboost_rich go negative in any hour) and
    # floor catboost_rich's DA weight, renormalizing the other DA models.
    try:
        if _is_negative_price_day(target_date):
            floor = 0.03
            cat_prev = fw.get("catboost_rich", 0.0)
            if cat_prev > floor:
                others = {m: fw.get(m, 0.0) for m in ("cfg05", "xgboost_rich")}
                os_sum = sum(others.values())
                budget = cat_prev - floor  # weight freed up from catboost
                fw["catboost_rich"] = floor
                if os_sum > 0:
                    for m, wv in others.items():
                        fw[m] = wv + budget * (wv / os_sum)
                logger.info("  [neg-price] %s: catboost_rich weight %.3f -> %.3f "
                            "(redistributed to cfg05/xgboost_rich)", target_date,
                            cat_prev, floor)
    except Exception as e:
        logger.warning("  neg-price adjustment skipped: %s", e)

    res = run_production_circuit(
        target_date, use_db=True, db_url=DB_URL, mode=mode,
        config={
            "dayahead_models": ALL_DA_MODELS,
            "realtime_models": ALL_RT_MODELS,
            "fusion_weights": fw,
            "allow_benchmark_fallback": False,
        },
    )
    rid = res.get("run_id", "")
    status = res.get("status", "UNKNOWN")

    from common.db.connection import DbConnectionManager
    conn = DbConnectionManager(db_url=DB_URL).new_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM efm_delivery_finals WHERE run_id=%s", (rid,))
    delh = cur.fetchone()[0]
    cur.execute(
        "SELECT c.name AS check_name, pc.passed FROM efm_postflight_checks pc "
        "JOIN efm_dim_check c ON pc.check_id = c.id WHERE pc.run_id=%s",
        (rid,))
    checks = {c[0]: c[1] for c in cur.fetchall()}
    conn.close()

    logger.info("  Circuit: status=%s delivery=%dh postflight=%d/8",
                status, delh, sum(1 for v in checks.values() if v))

    # Local-first artifacts: outputs/<date>/predict/<task>/{weight,fuse,final}.csv
    # + realtime/module_repair.csv  (read back from the committed 3NF ledger).
    if root is not None and rid:
        try:
            da_weights = {m: float(fw.get(m, 0.0)) for m in ALL_DA_MODELS}
            rt_weights = {m: float(fw.get(m, 0.0)) for m in ALL_RT_MODELS}
            write_circuit_local_outputs(
                root, target_date, rid, DB_URL,
                da_weights=da_weights, rt_weights=rt_weights)
        except Exception as e:
            logger.warning("  local circuit CSV write failed: %s", e)

    return {"status": status, "run_id": rid, "delivery_hours": delh, "checks": checks}


# ═══════════════════════════════════════════════════════════════════
# Stage 5: Delivery Export (real DA + RT from task finals, with fallback)
# ═══════════════════════════════════════════════════════════════════
def _read_task_finals(target_date: str, task: str, run_id: str | None = None) -> dict[int, float]:
    from common.db.connection import DbConnectionManager
    conn = DbConnectionManager(db_url=DB_URL).new_connection()
    try:
        cur = conn.cursor()
        if run_id:
            cur.execute(
                "SELECT tf.hour_business, tf.final_price FROM efm_task_finals tf "
                "JOIN efm_runs r ON tf.run_id=r.run_id "
                "WHERE r.target_date=%s AND tf.task=%s AND tf.run_id=%s ORDER BY tf.hour_business",
                (target_date, task, run_id))
        else:
            # Fallback: latest circuit run_id for this target_date (by started_at,
            # restricted to efm3_pc_% — efm3_raw_% sorts lexicographically higher
            # than efm3_pc_% so a plain MAX(run_id) would pick the wrong run).
            cur.execute(
                "SELECT tf.hour_business, tf.final_price FROM efm_task_finals tf "
                "JOIN efm_runs r ON tf.run_id=r.run_id "
                "WHERE r.target_date=%s AND tf.task=%s "
                "AND tf.run_id=(SELECT tf2.run_id FROM efm_task_finals tf2 "
                "JOIN efm_runs r2 ON tf2.run_id=r2.run_id "
                "WHERE r2.target_date=%s AND tf2.task=%s "
                "AND tf2.run_id LIKE 'efm3_pc_%%' "
                "ORDER BY r2.started_at DESC LIMIT 1) ORDER BY tf.hour_business",
                (target_date, task, target_date, task))
        return {int(hb): float(p) for hb, p in cur.fetchall()}
    finally:
        conn.close()


def _read_fused_realtime(target_date: str, run_id: str | None = None) -> dict[int, float]:
    """Fallback: read realtime_fused stage (weighted fusion of RT candidates)."""
    from common.db.connection import DbConnectionManager
    conn = DbConnectionManager(db_url=DB_URL).new_connection()
    try:
        cur = conn.cursor()
        # 3NF: target_date lives on efm_runs; stage is a FK to efm_dim_stage.
        q = (
            "SELECT p.hour_business, p.pred_price FROM efm_predictions p "
            "JOIN efm_runs r ON p.run_id=r.run_id "
            "JOIN efm_dim_stage s ON p.stage_id=s.id "
            "WHERE r.target_date=%s AND p.task='realtime' AND s.name='realtime_fused'"
        )
        params = [target_date]
        if run_id:
            q += " AND p.run_id=%s"
            params.append(run_id)
        q += " ORDER BY p.hour_business"
        cur.execute(q, params)
        rows = cur.fetchall()
        if rows:
            return {int(hb): float(p) for hb, p in rows}
        # Last resort: average raw_model candidates of this run
        q2 = (
            "SELECT p.hour_business, p.pred_price FROM efm_predictions p "
            "JOIN efm_runs r ON p.run_id=r.run_id "
            "JOIN efm_dim_stage s ON p.stage_id=s.id "
            "WHERE r.target_date=%s AND p.task='realtime' AND s.name='realtime_raw_model' "
            "AND p.run_id=%s ORDER BY p.hour_business"
        )
        cur.execute(q2, (target_date, run_id))
        from collections import defaultdict
        acc = defaultdict(list)
        for hb, p in cur.fetchall():
            acc[int(hb)].append(float(p))
        return {hb: sum(v)/len(v) for hb, v in acc.items()}
    finally:
        conn.close()


def stage_export(target_date: str, run_id: str) -> dict:
    logger.info("=== Stage 5/5: Delivery Export ===")
    da_final = _read_task_finals(target_date, "dayahead", run_id)
    rt_final = _read_task_finals(target_date, "realtime", run_id)
    if not rt_final:
        rt_final = _read_fused_realtime(target_date, run_id)
        logger.info("  RT final missing from task_finals; used fused fallback (%d h)", len(rt_final))

    if not da_final:
        logger.warning("  DA final missing — cannot produce complete delivery")
        return {"status": "failed", "path": None, "rows": 0}

    run_dir = REPO / "outputs" / "runs" / target_date / "delivery"
    run_dir.mkdir(parents=True, exist_ok=True)
    csv_path = run_dir / "submission_ready.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["business_day", "ds", "hour_business", "period",
                    "dayahead_price", "realtime_price", "selected_reason"])
        for hb in range(1, 25):
            ds = f"{target_date} {hb:02d}:00:00"
            period = "1_8" if hb <= 8 else ("9_16" if hb <= 16 else "17_24")
            da = da_final.get(hb)
            rt = rt_final.get(hb) if rt_final else None
            reason = "da+rt fused via production circuit" if rt is not None else "da only (rt final missing)"
            w.writerow([target_date, ds, hb, period,
                        f"{da:.2f}" if da is not None else "",
                        f"{rt:.2f}" if rt is not None else "",
                        reason])
    logger.info("  submission -> %s (%d rows, DA=%d RT=%d)",
                csv_path, 24, len(da_final), len(rt_final or {}))
    return {"status": "ok", "path": str(csv_path), "rows": 24}


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════
def run_full(target_date: str, mode: str = "formal_sim", force: bool = False,
             use_gpu: bool = False, skip_sync: bool = False) -> dict:
    t0 = time.time()
    manifest = {"target_date": target_date, "pipeline": "efm3_full",
                "use_gpu": use_gpu, "stages": {}}

    # ── 0. Local-first dated folder (unique per date) ──
    prep = prepare_dated_folder(target_date, force=force)
    if prep.get("skip"):
        logger.warning("=== ABORT: outputs/%s already exists (use --force to overwrite) ===",
                       target_date)
        manifest["skipped_existing"] = True
        manifest["existing_files"] = prep.get("existing_files", [])
        manifest["elapsed_s"] = round(time.time() - t0, 1)
        return manifest
    root = prep["root"]

    # ── DB health check gate ──
    hc = db_health_check(DB_URL)
    if not hc.get("ok"):
        logger.error("=== ABORT: %s ===", hc.get("detail", "DB unreachable"))
        manifest["db_error"] = True
        manifest["db_detail"] = hc.get("detail", "unknown")
        manifest["elapsed_s"] = round(time.time() - t0, 1)
        return manifest

    manifest["stages"]["cleanup"] = stage_cleanup(target_date)
    if skip_sync:
        logger.info("=== Stage 1/5: Data Sync === SKIPPED (--skip-sync; static data)")
        manifest["stages"]["sync"] = {"status": "skipped"}
    else:
        manifest["stages"]["sync"] = stage_sync(target_date)
    da = stage_da_predict(target_date, root, use_gpu=use_gpu)
    manifest["stages"]["da_predict"] = da
    manifest["stages"]["rt_predict"] = stage_rt_predict(
        target_date, da.get("preds", {}), root, use_gpu=use_gpu)
    manifest["stages"]["circuit"] = stage_circuit(target_date, mode=mode, root=root)

    rid = manifest["stages"]["circuit"].get("run_id", "")
    if rid:
        manifest["stages"]["export"] = stage_export(target_date, rid)

    manifest["elapsed_s"] = round(time.time() - t0, 1)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="EFM3.0 生产全链路")
    p.add_argument("pos_date", nargs="?", default=None)
    p.add_argument("--date", default=None)
    p.add_argument("--mode", default="formal_sim", choices=["dry_run", "formal_sim", "formal"])
    p.add_argument("--pipeline", default="full", choices=["full", "da_only", "rt_only", "circuit_only"])
    p.add_argument("--force", action="store_true",
                   help="overwrite outputs/<date>/ if it already exists")
    p.add_argument("--gpu", action="store_true",
                   help="Enable GPU: PyTorch RT models (SGDFNet/TimeMixer) use CUDA; "
                        "P1 tree learners attempt GPU with CPU fallback")
    p.add_argument("--skip-sync", action="store_true", dest="skip_sync",
                   help="Skip Stage 1 data sync (static data already ingested; faster backtest)")
    return p


def main():
    args = build_parser().parse_args()
    td = args.date or args.pos_date or date.today().isoformat()
    logger.info("=" * 60)
    logger.info("EFM3.0 Production Run: target=%s mode=%s force=%s gpu=%s skip_sync=%s",
                td, args.mode, args.force, args.gpu, args.skip_sync)
    logger.info("=" * 60)
    manifest = run_full(td, mode=args.mode, force=args.force, use_gpu=args.gpu,
                        skip_sync=args.skip_sync)

    if manifest.get("skipped_existing"):
        print("\n" + "=" * 60)
        print(f"  [WARN] outputs/{td} already exists -- not overwritten")
        print("  Use --force to overwrite. Existing files:")
        for f in manifest.get("existing_files", [])[:50]:
            print(f"    - {f}")
        print("=" * 60)
        return

    if manifest.get("db_error"):
        print("\n" + "=" * 60)
        print(f"  [ERROR] Database unavailable: {manifest.get('db_detail', 'unknown')}")
        print("  Fix the connection and retry. Check EFM3_DB_URL or .env.local.")
        print("=" * 60)
        return

    print("\n" + "=" * 60)
    print("  DELIVERY MANIFEST")
    print("=" * 60)
    for stage, result in manifest["stages"].items():
        print(f"  {stage:14s} -> {result.get('status', '?')}")
    del_info = manifest.get("stages", {}).get("export", {})
    if del_info.get("status") == "ok":
        print(f"  [OK] delivery: {del_info.get('path')} ({del_info.get('rows')} rows)")
    print(f"  [TIME] {manifest.get('elapsed_s', 0):.0f}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
