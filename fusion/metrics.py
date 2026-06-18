from __future__ import annotations

import numpy as np
import pandas as pd


def smape_floor50(y_true: np.ndarray, y_pred: np.ndarray, eps: float = 1e-6) -> float:
    true_clip = np.where(y_true < 50.0, 50.0, y_true)
    pred_clip = np.where(y_pred < 50.0, 50.0, y_pred)
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
