from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge

from .contracts import build_wide_frame
from .metrics import smape_floor50


BASE_ID_COLS = ["task", "target_day", "ds", "period", "hour_business", "y_true"]


@dataclass
class SegmentMetaModel:
    task: str
    period: str
    model_names: list[str]
    imputer: SimpleImputer
    estimator: Ridge


def _build_features(group: pd.DataFrame, model_cols: list[str]) -> pd.DataFrame:
    return group[model_cols].copy()


def fit_meta_learners_from_long_table(
    df: pd.DataFrame,
    *,
    alpha: float = 2.0,
) -> tuple[dict[tuple[str, str], SegmentMetaModel], pd.DataFrame]:
    wide = build_wide_frame(df)
    model_cols = [column for column in wide.columns if column not in BASE_ID_COLS]
    if not model_cols:
        raise ValueError("No model columns found after pivoting prediction table")

    models: dict[tuple[str, str], SegmentMetaModel] = {}
    report_rows: list[dict[str, object]] = []

    for (task, period), group in wide.groupby(["task", "period"], sort=True):
        active_model_cols = [column for column in model_cols if column in group.columns and group[column].notna().any()]
        clean_group = group.dropna(subset=["y_true"]).copy()
        if not active_model_cols or clean_group.empty:
            continue

        feature_frame = _build_features(clean_group, active_model_cols)
        imputer = SimpleImputer(strategy="median")
        x_train = imputer.fit_transform(feature_frame)
        y_train = clean_group["y_true"].to_numpy(dtype=float)

        estimator = Ridge(alpha=float(alpha), fit_intercept=False, positive=True)
        estimator.fit(x_train, y_train)
        y_fit = estimator.predict(x_train)

        segment_model = SegmentMetaModel(
            task=str(task),
            period=str(period),
            model_names=active_model_cols,
            imputer=imputer,
            estimator=estimator,
        )
        models[(str(task), str(period))] = segment_model

        coef_map = dict(zip(segment_model.model_names, estimator.coef_.tolist()))
        report_rows.append(
            {
                "task": task,
                "period": period,
                "sample_count": int(len(clean_group)),
                "smape_fit": float(smape_floor50(y_train, y_fit)),
                "ridge_alpha": float(alpha),
                **{f"coef_{name}": float(coef_map.get(name, 0.0)) for name in segment_model.model_names},
            }
        )

    return models, pd.DataFrame(report_rows)


def apply_meta_learners(
    df: pd.DataFrame,
    models: dict[tuple[str, str], SegmentMetaModel],
    *,
    task: str,
    test_start: str,
    test_end: str,
) -> pd.DataFrame:
    wide = build_wide_frame(df)
    task_df = wide[wide["task"] == task].copy()
    task_days = pd.to_datetime(task_df["target_day"])
    task_df = task_df[(task_days >= pd.Timestamp(test_start)) & (task_days <= pd.Timestamp(test_end))].copy()
    if task_df.empty:
        raise RuntimeError(f"No test rows found for task={task}.")

    fused_parts: list[pd.DataFrame] = []
    for period, group in task_df.groupby("period", sort=True):
        key = (task, period)
        if key not in models:
            raise RuntimeError(f"Missing meta learner for task={task}, period={period}.")
        meta_model = models[key]
        feature_frame = _build_features(group, meta_model.model_names)
        x_test = meta_model.imputer.transform(feature_frame[meta_model.model_names])
        y_pred = meta_model.estimator.predict(x_test)
        out = group.copy()
        out["y_fused"] = y_pred
        fused_parts.append(out)

    return pd.concat(fused_parts, ignore_index=True).sort_values(["target_day", "hour_business"]).reset_index(drop=True)
