"""
common_metrics.py — P3 shadow-only correction 共享指标与工具。

所有指标定义集中在此，保证 before / after / ablation 使用完全一致的计算口径。

指标约定：
- MAE / RMSE：标准定义。
- sMAPE_floor50：SMAPE 的分母加 floor=50 以稳定近零/负价场景。
    sMAPE_floor50(a, f) = 100 * |a - f| / (0.5 * (|a| + |f|) + 50)
  与 RT916 SMAPEFloor50Loss 的 floor 思路一致；before/after 用同一公式，口径可比。
- 负价小时：actual < 0
- 尖峰小时：actual > SPIKE_HIGH（默认 500）
- 正常小时：其余

本模块不读写任何正式链路文件，仅做纯计算。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SPIKE_HIGH = 500.0          # 高尖峰阈值（与 2.0_exp spike_detector 标签量级一致）
NEGATIVE_THRESHOLD = 0.0    # 负价阈值（actual < 0）
SMAPE_FLOOR = 50.0


def smape_floor50(a: np.ndarray, f: np.ndarray) -> float:
    a = np.asarray(a, dtype=float)
    f = np.asarray(f, dtype=float)
    denom = 0.5 * (np.abs(a) + np.abs(f)) + SMAPE_FLOOR
    return float(np.mean(100.0 * np.abs(a - f) / denom))


def _safe_mean(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return float("nan")
    return float(np.mean(x))


def mae(a, f) -> float:
    return _safe_mean(np.abs(np.asarray(a, float) - np.asarray(f, float)))


def rmse(a, f) -> float:
    d = np.asarray(a, float) - np.asarray(f, float)
    if d.size == 0:
        return float("nan")
    return float(np.sqrt(np.mean(d ** 2)))


def metric_block(actual: np.ndarray, pred: np.ndarray) -> dict:
    """返回一组核心指标（可能含 nan 当样本为空）。"""
    actual = np.asarray(actual, dtype=float)
    pred = np.asarray(pred, dtype=float)
    return {
        "n": int(actual.size),
        "MAE": mae(actual, pred),
        "RMSE": rmse(actual, pred),
        "sMAPE_floor50": smape_floor50(actual, pred),
    }


def split_masks(actual: np.ndarray):
    """返回 (neg_mask, spike_mask, normal_mask) 布尔数组。"""
    actual = np.asarray(actual, dtype=float)
    neg = actual < NEGATIVE_THRESHOLD
    spike = actual > SPIKE_HIGH
    normal = (~neg) & (~spike)
    return neg, spike, normal


def period_of(hour_business: int) -> str:
    h = int(hour_business)
    if 1 <= h <= 8:
        return "1_8"
    if 9 <= h <= 16:
        return "9_16"
    return "17_24"


def full_metrics_table(actual: np.ndarray, pred: np.ndarray,
                       hour_business: np.ndarray | None = None) -> dict:
    """生成全局 + 子集 + 分时段指标表，供报告使用。"""
    out = {"overall": metric_block(actual, pred)}
    neg, spike, normal = split_masks(actual)
    out["negative"] = metric_block(actual[neg], pred[neg])
    out["spike"] = metric_block(actual[spike], pred[spike])
    out["normal"] = metric_block(actual[normal], pred[normal])
    if hour_business is not None:
        hb = np.asarray(hour_business)
        out["period"] = {}
        for p in ["1_8", "9_16", "17_24"]:
            m = np.array([period_of(h) == p for h in hb])
            out["period"][p] = metric_block(actual[m], pred[m])
    return out


def normal_degradation(original: np.ndarray, corrected: np.ndarray,
                       actual: np.ndarray) -> dict:
    """正常时段：correction 相对 original 的误差变化（越大越糟）。"""
    _, _, normal = split_masks(actual)
    if not normal.any():
        return {"n": 0, "MAE_delta": float("nan"), "sMAPE_delta": float("nan")}
    o = mae(actual[normal], original[normal])
    c = mae(actual[normal], corrected[normal])
    return {
        "n": int(normal.sum()),
        "MAE_original": o,
        "MAE_corrected": c,
        "MAE_delta": c - o,  # >0 表示 corrected 在正常时段更差
        "sMAPE_original": smape_floor50(actual[normal], original[normal]),
        "sMAPE_corrected": smape_floor50(actual[normal], corrected[normal]),
        "sMAPE_delta": smape_floor50(actual[normal], corrected[normal])
        - smape_floor50(actual[normal], original[normal]),
    }
