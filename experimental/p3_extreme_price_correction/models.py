"""
models.py — 透明、可解释、零额外依赖的分类器。

不使用黑箱模型：采用带 L2 正则的逻辑回归（numpy 实现），
权重可直接作为 correction_reason 的组成部分，满足"可解释、不允许黑箱强行改价"。
"""
from __future__ import annotations

import numpy as np


class SimpleLogistic:
    """最小逻辑回归（梯度下降 + L2 正则），可解释。"""

    def __init__(self, n_iter: int = 300, lr: float = 0.05, l2: float = 1e-2,
                 add_intercept: bool = True, seed: int = 42):
        self.n_iter = n_iter
        self.lr = lr
        self.l2 = l2
        self.add_intercept = add_intercept
        self.seed = seed
        self.coef_ = None
        self.mu_ = None
        self.sigma_ = None

    def _standardize(self, X, fit=False):
        X = np.asarray(X, dtype=float)
        if self.add_intercept:
            X = np.hstack([np.ones((X.shape[0], 1)), X])
        if fit:
            self.mu_ = X.mean(axis=0, keepdims=True)
            self.sigma_ = X.std(axis=0, keepdims=True) + 1e-8
        Xs = (X - self.mu_) / self.sigma_
        return Xs

    def fit(self, X, y):
        Xs = self._standardize(X, fit=True)
        y = np.asarray(y, dtype=float)
        rng = np.random.default_rng(self.seed)
        n_feat = Xs.shape[1]
        self.coef_ = np.zeros(n_feat)
        # 简单初始化
        for _ in range(self.n_iter):
            z = Xs @ self.coef_
            p = 1.0 / (1.0 + np.exp(-z))
            grad = Xs.T @ (p - y) / len(y)
            grad[1:] += self.l2 * self.coef_[1:]  # 截距不惩罚
            self.coef_ -= self.lr * grad
        return self

    def predict_proba(self, X):
        Xs = self._standardize(X, fit=False)
        z = Xs @ self.coef_
        return 1.0 / (1.0 + np.exp(-z))

    def feature_names_out(self, names):
        if self.add_intercept:
            return ["intercept"] + list(names)
        return list(names)
