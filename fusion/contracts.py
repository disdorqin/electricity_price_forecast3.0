from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


REQUIRED_COLUMNS = [
    "task",
    "model_name",
    "target_day",
    "ds",
    "period",
    "hour_business",
    "y_true",
    "y_pred",
]

VALID_TASKS = {"dayahead", "realtime"}
VALID_PERIODS = {"1_8", "9_16", "17_24"}


@dataclass(frozen=True)
class PredictionTableSpec:
    required_columns: tuple[str, ...] = tuple(REQUIRED_COLUMNS)


def normalize_task(value: str) -> str:
    text = str(value).strip().lower()
    mapping = {
        "dayahead": "dayahead",
        "da": "dayahead",
        "日前": "dayahead",
        "realtime": "realtime",
        "rt": "realtime",
        "实时": "realtime",
    }
    if text not in mapping:
        raise ValueError(f"Unsupported task value: {value}")
    return mapping[text]


def infer_period(hour_business: int) -> str:
    hour = int(hour_business)
    if 1 <= hour <= 8:
        return "1_8"
    if 9 <= hour <= 16:
        return "9_16"
    if 17 <= hour <= 24:
        return "17_24"
    raise ValueError(f"hour_business out of range: {hour_business}")


def standardize_prediction_table(df: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"Prediction table is missing required columns: {missing}")

    out = df.copy()
    out["task"] = out["task"].map(normalize_task)
    out["model_name"] = out["model_name"].astype(str).str.strip()
    out["target_day"] = pd.to_datetime(out["target_day"]).dt.strftime("%Y-%m-%d")
    out["ds"] = pd.to_datetime(out["ds"])
    out["hour_business"] = out["hour_business"].astype(int)
    out["period"] = out["period"].astype(str).str.strip()
    out["y_true"] = pd.to_numeric(out["y_true"], errors="coerce")
    out["y_pred"] = pd.to_numeric(out["y_pred"], errors="coerce")

    out["period"] = out.apply(
        lambda row: infer_period(row["hour_business"]) if not row["period"] or row["period"] == "nan" else row["period"],
        axis=1,
    )

    bad_tasks = sorted(set(out["task"]) - VALID_TASKS)
    bad_periods = sorted(set(out["period"]) - VALID_PERIODS)
    if bad_tasks:
        raise ValueError(f"Unsupported task labels after normalization: {bad_tasks}")
    if bad_periods:
        raise ValueError(f"Unsupported period labels after normalization: {bad_periods}")

    if out[["y_true", "y_pred"]].isna().any().any():
        raise ValueError("Prediction table contains NaN in y_true or y_pred")

    return out


def build_wide_frame(df: pd.DataFrame) -> pd.DataFrame:
    standardized = standardize_prediction_table(df)
    id_cols = ["task", "target_day", "ds", "period", "hour_business"]

    truth_counts = (
        standardized.groupby(id_cols)["y_true"].nunique(dropna=False).reset_index(name="truth_nunique")
    )
    conflicts = truth_counts[truth_counts["truth_nunique"] > 1]
    if not conflicts.empty:
        raise ValueError("Found inconsistent y_true values for the same target point across models")

    truth_df = standardized[id_cols + ["y_true"]].drop_duplicates(subset=id_cols)
    pred_wide = (
        standardized.pivot_table(
            index=id_cols,
            columns="model_name",
            values="y_pred",
            aggfunc="last",
        )
        .reset_index()
    )
    pred_wide.columns.name = None
    return truth_df.merge(pred_wide, on=id_cols, how="inner")
