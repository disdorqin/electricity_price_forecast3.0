"""
EFM3 Research — Metrics Contract (research-only, production-isolated).

Provides the single canonical research metric entry points:
  - plain_smape
  - smape_floor50 (corrected magnitude-clip version)

Production's fusion/metrics.py is NOT modified by this module.
The production bug-fix (smape_floor50 negative-price clipping) lives in
a separate branch: fix/metric-contract-parity (independent PR).
"""
from __future__ import annotations

import numpy as np


def plain_smape(y_true: np.ndarray, y_pred: np.ndarray, eps: float = 1e-9) -> float:
    """Plain sMAPE in percent.

    Formula (single canonical definition used across EFM3 research):
        100 * |y_true - y_pred| / ( (|y_true| + |y_pred|) / 2 )

    - Perfect prediction -> 0.
    - Symmetric in true/pred.
    - Handles negative and zero prices via the (|y_true|+|y_pred|)/2 denominator;
      `eps` only guards the degenerate 0/0 case.
    """
    a = np.asarray(y_true, dtype=float)
    p = np.asarray(y_pred, dtype=float)
    denom = (np.abs(a) + np.abs(p)) / 2.0
    denom = np.where(denom < eps, eps, denom)
    return float(np.mean(np.abs(a - p) / denom) * 100.0)


def smape_floor50(y_true: np.ndarray, y_pred: np.ndarray,
                  floor: float = 50.0, eps: float = 1e-6) -> float:
    """sMAPE with the denominator floored at `floor` (tail-weighted variant).

    Each of true/pred is clipped to |x| >= floor before forming the symmetric
    denominator, which amplifies errors on low/negative-price hours (the tail).
    This is a DIFFERENT metric from plain_smape and must never be silently
    substituted for it.

    NOTE: This is the CORRECTED version (magnitude clip = preserve sign);
    the production version (fusion/metrics.py on main) clips negatives to +50,
    which is a bug being fixed via an independent PR.
    """
    a = np.asarray(y_true, dtype=float)
    p = np.asarray(y_pred, dtype=float)
    true_clip = np.where(np.abs(a) < floor, np.sign(a) * floor, a)
    pred_clip = np.where(np.abs(p) < floor, np.sign(p) * floor, p)
    denom = (np.abs(true_clip) + np.abs(pred_clip)) / 2.0
    denom = np.where(denom < eps, eps, denom)
    return float(np.mean(np.abs(pred_clip - true_clip) / denom) * 100.0)


__all__ = ["plain_smape", "smape_floor50"]
