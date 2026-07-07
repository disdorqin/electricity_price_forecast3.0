"""
walkforward.py — 时序 walk-forward 训练/预测（严格 leakage-free）。

对按时间排序的样本，第 i 个样本用 i 之前的所有样本训练，预测第 i 个。
绝不使用未来样本。无历史时返回 fallback（默认 0.0，即不触发）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from .models import SimpleLogistic


def fit_predict_proba_walkforward(X: pd.DataFrame, y: pd.Series,
                                  model_factory=None, fallback: float = 0.0,
                                  min_samples: int = 20, min_pos: int = 3):
    """返回与 X 行序对齐的概率数组（索引顺序不变）。"""
    X = X.reset_index(drop=True)
    y = pd.Series(y).reset_index(drop=True)
    n = len(X)
    out = np.full(n, fallback, dtype=float)
    for i in range(n):
        train_X = X.iloc[:i]
        train_y = y.iloc[:i]
        if len(train_X) < min_samples or train_y.sum() < min_pos:
            out[i] = fallback
            continue
        m = (model_factory or SimpleLogistic)()
        m.fit(train_X.values, train_y.values)
        out[i] = float(np.clip(m.predict_proba(X.iloc[[i]].values)[0], 0.0, 1.0))
    return out
