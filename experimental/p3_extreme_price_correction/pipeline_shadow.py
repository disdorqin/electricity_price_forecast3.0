"""
pipeline_shadow.py — 影子修正主流程（编排 6 模块，cutoff-safe）。

输入：baseline_features.parquet（含 original_pred / da_anchor / 模型分歧度 / 历史同小时统计 / actual）
输出：逐小时修正结果（corrected_pred、applied、rollback 等），全部 shadow-only。
核心决策：
  负价优先（推向 -80 地板，保守护栏）→ 与尖峰互斥 → 否则残差校准。
  每次修正经 correction_guard 检查 cap/范围，再经 rollback_guard 检查置信/NaN。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as _cfg
from .negative_price_classifier import run as neg_run
from .spike_price_classifier import run as spk_run
from .residual_corrector import run as rc_run
from .correction_guard import guard_pass
from .rollback_guard import evaluate_rollback

MODEL_VERSION = "rt_ensemble_invmae_v1 + p3_correction_v1"
SOURCE_FEATURES = ("original_pred,model_std,model_min,model_max,da_anchor,"
                   "hist_neg_rate_samehour,hist_p50_samehour,hist_p90_samehour,"
                   "period,hour_business (all D-1 14:00 cutoff-safe)")


def run_correction(df: pd.DataFrame, cfg=None) -> tuple[pd.DataFrame, dict]:
    cfg = cfg or _cfg.default_config()

    neg = neg_run(df, cfg) if cfg.negative_classifier_enabled else None
    spk = spk_run(df, cfg) if cfg.spike_classifier_enabled else None
    rc = rc_run(df, cfg) if cfg.residual_corrector_enabled else None

    n = len(df)
    corrected = df["original_pred"].astype(float).values.copy()
    correction_amount = np.zeros(n)
    negative_prob = neg["negative_probability"].values if neg is not None else np.zeros(n)
    negative_reason = neg["negative_reason"].values if neg is not None else [""] * n
    spike_prob = spk["spike_probability"].values if spk is not None else np.zeros(n)
    spike_type = spk["spike_type"].values if spk is not None else ["none"] * n
    spike_reason = spk["spike_reason"].values if spk is not None else [""] * n
    rc_amt = rc["rc_correction_amount"].values if rc is not None else np.zeros(n)
    rc_reason = rc["rc_reason"].values if rc is not None else [""] * n
    rc_conf = rc["rc_confidence"].values if rc is not None else np.zeros(n)
    neg_conf = neg["negative_confidence"].values if neg is not None else np.zeros(n)
    spk_conf = spk["spike_confidence"].values if spk is not None else np.zeros(n)

    applied = np.zeros(n, dtype=bool)
    cap_hit = np.zeros(n, dtype=bool)
    rollback_reason = [""] * n
    confidence = np.zeros(n)
    reason_final = [""] * n
    ctype_used = [""] * n

    for i in range(n):
        original = float(df["original_pred"].iloc[i])
        period = df["period"].iloc[i]
        cur = original
        amt = 0.0
        is_applied = False
        cur_conf = 0.0
        cur_ctype = ""
        rparts = []

        # --- 负价修正（最高优先）---
        if cfg.negative_classifier_enabled and negative_prob[i] >= cfg.NEG_THRESH \
           and original <= cfg.NEG_ACT_PRED_CAP:
            target = cfg.NEG_FLOOR_TARGET
            a = target - original
            passed, ch, greason = guard_pass(a, original, target, cfg, "negative")
            if passed:
                cur = target; amt = a; is_applied = True
                cur_conf = float(neg_conf[i]); cur_ctype = "negative"
                rparts.append(f"NEG[{negative_reason[i]}]")
            else:
                cap_hit[i] = bool(cap_hit[i] or ch)
                rparts.append(f"NEG_blocked:{greason}")

        # --- 尖峰修正（与负价互斥）---
        elif cfg.spike_classifier_enabled and spike_prob[i] >= cfg.SPK_THRESH:
            boost = cfg.SPK_9_16_BOOST if period == "9_16" else 1.0
            if original > cfg.SPK_MIN_ORIGINAL:
                lift = min(cfg.SPK_LIFT_RATIO * original, cfg.SPK_LIFT_ABS) * boost
                a = +lift
                target = original + a
                passed, ch, greason = guard_pass(a, original, target, cfg, "spike")
                if passed:
                    cur = target; amt = a; is_applied = True
                    cur_conf = float(spk_conf[i]); cur_ctype = "spike"
                    rparts.append(f"SPK[{spike_reason[i]}]")
                else:
                    cap_hit[i] = bool(cap_hit[i] or ch)
                    rparts.append(f"SPK_blocked:{greason}")
            else:
                rparts.append("SPK_skip:original<=0")

        # --- 残差校准（极端已修正则跳过）---
        if cfg.residual_corrector_enabled and cur_ctype == "" and abs(rc_amt[i]) > 1e-6 and rc_reason[i]:
            a = float(rc_amt[i])
            target = original + a
            passed, ch, greason = guard_pass(a, original, target, cfg, "residual")
            if passed:
                cur = target; amt = a; is_applied = True
                cur_conf = float(rc_conf[i]); cur_ctype = "residual"
                rparts.append(f"RESID[{rc_reason[i]}]")
            else:
                cap_hit[i] = bool(cap_hit[i] or ch)
                rparts.append(f"RESID_blocked:{greason}")

        # --- 回滚 ---
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
        reason_final[i] = "; ".join(rparts) if rparts else "no_action"

    out = pd.DataFrame({
        "business_day": df["business_day"],
        "ds": df["ds"],
        "hour_business": df["hour_business"].astype(int),
        "period": df["period"],
        "original_pred": df["original_pred"].round(3),
        "corrected_pred": np.round(corrected, 3),
        "correction_amount": np.round(correction_amount, 3),
        "negative_probability": np.round(negative_prob, 4),
        "spike_probability": np.round(spike_prob, 4),
        "spike_type": spike_type,
        "negative_reason": negative_reason,
        "spike_reason": spike_reason,
        "correction_reason": reason_final,
        "confidence": np.round(confidence, 4),
        "applied": applied,
        "cap_hit": cap_hit,
        "rollback_reason": rollback_reason,
        "ctype_used": ctype_used,
        "shadow_only": True,
        "source_features": SOURCE_FEATURES,
        "model_version": MODEL_VERSION,
        "run_id": cfg.RUN_ID,
    })
    summary = {
        "n": int(n),
        "applied_count": int(applied.sum()),
        "cap_hit_count": int(cap_hit.sum()),
        "rollback_count": int(sum(1 for r in rollback_reason if r)),
        "neg_corrected": int(sum(1 for c in ctype_used if c == "negative")),
        "spk_corrected": int(sum(1 for c in ctype_used if c == "spike")),
        "resid_corrected": int(sum(1 for c in ctype_used if c == "residual")),
    }
    return out, summary
