"""
negative_price_classifier.py — 负价分类器（cutoff-safe, walk-forward, 可解释）。

标签：actual <= cfg.NEG_LABEL（山东极端负价，聚焦 -80 地板）。
特征：全部为 D-1 14:00 前可见信息；actual 仅用于训练标签，绝不作在线特征。
输出：negative_probability, negative_reason, negative_confidence。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from .walkforward import fit_predict_proba_walkforward


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    X = pd.DataFrame(index=df.index)
    X["original_pred"] = df["original_pred"].astype(float)
    X["hist_neg_rate_samehour"] = df["hist_neg_rate_samehour"].fillna(0.0).astype(float)
    X["model_std"] = df["model_std"].fillna(0.0).astype(float)
    X["da_anchor"] = df["da_anchor"].fillna(df["original_pred"]).astype(float)
    X["model_max"] = df["model_max"].fillna(df["original_pred"]).astype(float)
    X["period_9_16"] = (df["period"] == "9_16").astype(float)
    X["period_17_24"] = (df["period"] == "17_24").astype(float)
    return X


def run(df: pd.DataFrame, cfg) -> pd.DataFrame:
    X = build_features(df)
    y = (df["actual"] <= cfg.NEG_LABEL).astype(int)
    prob = fit_predict_proba_walkforward(X, y, fallback=0.0, min_samples=20, min_pos=3)

    out = pd.DataFrame(index=df.index)
    out["negative_probability"] = prob

    reasons = []
    for i in range(len(df)):
        parts = []
        hnr = df["hist_neg_rate_samehour"].iloc[i]
        if hnr == hnr and hnr > 0.2:
            parts.append(f"samehour_neg_rate={hnr:.2f}")
        if df["original_pred"].iloc[i] <= cfg.NEG_ACT_PRED_CAP:
            parts.append(f"fused={df['original_pred'].iloc[i]:.1f}(low)")
        ms = df["model_std"].iloc[i]
        if ms == ms and ms > 40:
            parts.append(f"disagreement={ms:.1f}")
        da = df["da_anchor"].iloc[i]
        if da == da and da < 50:
            parts.append(f"da_anchor={da:.1f}(low)")
        reasons.append(";".join(parts) if parts else "weak_signal")
    out["negative_reason"] = reasons
    out["negative_confidence"] = prob
    return out
