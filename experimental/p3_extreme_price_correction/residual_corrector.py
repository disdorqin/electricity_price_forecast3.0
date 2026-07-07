"""
residual_corrector.py — 残差校正器（SGDFNet 式 delta 分段偏差校准，leakage-free）。

思路：residual r = actual - original_pred；按 (period, hour_business, month_bucket)
计算历史中位数偏差 bias（expanding，仅用过去日）；corrected = original + α·bias。
误差门控：样本不足或偏差不显著则不校。仅修正"一般性偏差"，不触碰极端价。
注意：本模块只产出建议值，最终是否应用由 orchestrator + guard + rollback 决定。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _month_bucket(d: pd.Timestamp) -> str:
    return f"M{d.month}"


def run(df: pd.DataFrame, cfg) -> pd.DataFrame:
    work = df.copy()
    work["actual"] = work["actual"].astype(float)
    work["original_pred"] = work["original_pred"].astype(float)
    work["_resid"] = work["actual"] - work["original_pred"]
    work["_mb"] = pd.to_datetime(work["target_day"]).apply(_month_bucket)
    work = work.sort_values("target_day").reset_index(drop=True)

    amt = np.zeros(len(work))
    conf = np.zeros(len(work))
    reason = [""] * len(work)

    # 逐日 expanding 计算分组偏差
    for i in range(len(work)):
        day = work.iloc[i]["target_day"]
        prior = work[work["target_day"] < day]
        if len(prior) == 0:
            continue
        grp = prior[(prior["period"] == work.iloc[i]["period"]) &
                    (prior["hour_business"] == work.iloc[i]["hour_business"]) &
                    (prior["_mb"] == work.iloc[i]["_mb"])]
        if len(grp) < cfg.RESIDUAL_MIN_SAMPLES:
            continue
        bias = grp["_resid"].median()
        if not cfg.RESIDUAL_ERROR_GATE:
            pass
        # 误差门控：偏差不显著（接近 0）则不校
        if abs(bias) < 5.0:
            continue
        amt[i] = cfg.RESIDUAL_ALPHA * bias
        conf[i] = min(1.0, len(grp) / 30.0)
        reason[i] = f"bias[{work.iloc[i]['period']},h{int(work.iloc[i]['hour_business'])},{work.iloc[i]['_mb']}]={bias:.1f}(n={len(grp)})"

    out = pd.DataFrame(index=df.index)
    out["rc_correction_amount"] = amt
    out["rc_corrected_pred"] = work["original_pred"].values + amt
    out["rc_confidence"] = conf
    out["rc_reason"] = reason
    return out
