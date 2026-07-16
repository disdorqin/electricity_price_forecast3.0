from __future__ import annotations

import numpy as np
import pandas as pd


def plain_smape(y_true: np.ndarray, y_pred: np.ndarray, eps: float = 1e-9) -> float:
    """Plain sMAPE in percent.

    Formula (single, canonical definition used across EFM3 research):
        100 * |y_true - y_pred| / ( (|y_true| + |y_pred|) / 2 )

    - Perfect prediction -> 0.
    - Symmetric in true/pred (sign of denominator cancels).
    - Handles negative and zero prices via the (|y_true|+|y_pred|)/2 denominator;
      `eps` only guards the degenerate 0/0 case.
    """
    a = np.asarray(y_true, dtype=float)
    p = np.asarray(y_pred, dtype=float)
    denom = (np.abs(a) + np.abs(p)) / 2.0
    denom = np.where(denom < eps, eps, denom)
    return float(np.mean(np.abs(a - p) / denom) * 100.0)


def smape_floor50(y_true: np.ndarray, y_pred: np.ndarray, floor: float = 50.0,
                  eps: float = 1e-6) -> float:
    """sMAPE with the denominator floored at `floor` (tail-weighted variant).

    Each of true/pred is clipped to |x| >= floor before forming the symmetric
    denominator, which amplifies errors on low/negative-price hours (the tail).
    This is a DIFFERENT metric from plain_smape and must never be silently
    substituted for it.
    """
    a = np.asarray(y_true, dtype=float)
    p = np.asarray(y_pred, dtype=float)
    true_clip = np.where(np.abs(a) < floor, np.sign(a) * floor if floor != 0 else a, a)
    pred_clip = np.where(np.abs(p) < floor, np.sign(p) * floor if floor != 0 else p, p)
    denom = (np.abs(true_clip) + np.abs(pred_clip)) / 2.0
    denom = np.where(denom < eps, eps, denom)
    return float(np.mean(np.abs(pred_clip - true_clip) / denom) * 100.0)


def mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean(np.abs(y_true - y_pred)))


def arbitrage_metrics(df: pd.DataFrame) -> dict[str, float]:
    required = {"y_true_rt", "y_true_da", "y_pred_rt", "y_pred_da"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Arbitrage metrics require columns: {missing}")

    q_base = (df["y_pred_da"] > df["y_true_da"]).astype(int)
    q_improved = ((df["y_pred_rt"] > df["y_pred_da"]) & (df["y_pred_da"] > df["y_true_da"])).astype(int)

    spread = df["y_true_rt"] - df["y_true_da"]
    total_profit = float((q_base * spread).sum())
    total_volume = int(q_base.sum())
    improved_profit = float((q_improved * spread).sum())
    improved_volume = int(q_improved.sum())

    return {
        "arbitrage_total_profit": total_profit,
        "arbitrage_total_volume": total_volume,
        "arbitrage_unit_profit": float(total_profit / total_volume) if total_volume else float("nan"),
        "arbitrage_improved_total_profit": improved_profit,
        "arbitrage_improved_total_volume": improved_volume,
        "arbitrage_improved_unit_profit": float(improved_profit / improved_volume) if improved_volume else float("nan"),
    }
