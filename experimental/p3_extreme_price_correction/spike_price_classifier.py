"""
spike_price_classifier.py — 尖峰分类器（cutoff-safe, walk-forward, 可解释）。

标签：actual > cfg.SPK_LABEL（山东实时高尖峰，默认 >500）。
类型：high（actual>500）/ low（actual<-50，深谷）/ none。
特征：全部 D-1 14:00 前可见；actual 仅用于训练标签。
输出：spike_probability, spike_type, spike_reason, spike_confidence。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from .walkforward import fit_predict_proba_walkforward


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    X = pd.DataFrame(index=df.index)
    X["original_pred"] = df["original_pred"].astype(float)
    X["hist_p90_samehour"] = df["hist_p90_samehour"].fillna(df["original_pred"]).astype(float)
    X["model_std"] = df["model_std"].fillna(0.0).astype(float)
    X["da_anchor"] = df["da_anchor"].fillna(df["original_pred"]).astype(float)
    X["period_9_16"] = (df["period"] == "9_16").astype(float)
    X["period_17_24"] = (df["period"] == "17_24").astype(float)
    return X


def run(df: pd.DataFrame, cfg) -> pd.DataFrame:
    X = build_features(df)
    y = (df["actual"] > cfg.SPK_LABEL).astype(int)
    prob = fit_predict_proba_walkforward(X, y, fallback=0.0, min_samples=20, min_pos=2)

    out = pd.DataFrame(index=df.index)
    out["spike_probability"] = prob

    types, reasons = [], []
    for i in range(len(df)):
        a = df["actual"].iloc[i]
        if a > cfg.SPK_LABEL:
            stype = "high"
        elif a < -50:
            stype = "low"
        else:
            stype = "none"
        types.append(stype)
        parts = []
        if df["hist_p90_samehour"].iloc[i] == df["hist_p90_samehour"].iloc[i] and \
           df["hist_p90_samehour"].iloc[i] > cfg.SPK_LABEL:
            parts.append(f"samehour_p90={df['hist_p90_samehour'].iloc[i]:.0f}")
        if df["original_pred"].iloc[i] > 400:
            parts.append(f"fused={df['original_pred'].iloc[i]:.0f}(high)")
        ms = df["model_std"].iloc[i]
        if ms == ms and ms > 50:
            parts.append(f"disagreement={ms:.1f}")
        reasons.append(";".join(parts) if parts else "weak_signal")
    out["spike_type"] = types
    out["spike_reason"] = reasons
    out["spike_confidence"] = prob
    return out
