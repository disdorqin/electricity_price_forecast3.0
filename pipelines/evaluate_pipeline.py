from __future__ import annotations

from pathlib import Path

import pandas as pd


def _smape(y_true, y_pred, eps: float = 1e-6) -> float:
    denom = (y_true.abs() + y_pred.abs()).clip(lower=eps)
    return float((200.0 * (y_pred - y_true).abs() / denom).mean())


def _smape_clip50(y_true, y_pred, eps: float = 1e-6) -> float:
    y_true_clip = y_true.where(y_true >= 50, 50)
    y_pred_clip = y_pred.where(y_pred >= 50, 50)
    denom = (y_true_clip.abs() + y_pred_clip.abs()).clip(lower=eps)
    return float((200.0 * (y_pred_clip - y_true_clip).abs() / denom).mean())


def run_evaluate_pipeline(args) -> Path:
    if not args.pred_path or not args.actual_path:
        raise ValueError("evaluate pipeline requires --pred-path and --actual-path")
    pred_df = pd.read_csv(args.pred_path)
    actual_df = pd.read_csv(args.actual_path)
    if "时刻" not in pred_df.columns or "预测值" not in pred_df.columns:
        raise ValueError("Prediction CSV must contain columns: 时刻, 预测值")
    if "时刻" not in actual_df.columns:
        raise ValueError("Actual CSV must contain 时刻 column")
    actual_value_col = "实际值" if "实际值" in actual_df.columns else actual_df.columns[-1]
    merged = pred_df.merge(actual_df[["时刻", actual_value_col]], on="时刻", how="inner")
    merged["时刻"] = pd.to_datetime(merged["时刻"])
    merged["预测值"] = pd.to_numeric(merged["预测值"], errors="coerce")
    merged[actual_value_col] = pd.to_numeric(merged[actual_value_col], errors="coerce")
    merged = merged.dropna(subset=["预测值", actual_value_col]).copy()
    metrics = pd.DataFrame(
        [
            {
                "rows": int(len(merged)),
                "raw_smape": _smape(merged[actual_value_col], merged["预测值"]),
                "smape_clip50": _smape_clip50(merged[actual_value_col], merged["预测值"]),
            }
        ]
    )
    output_path = Path(args.output_root) / "evaluation_metrics.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path
