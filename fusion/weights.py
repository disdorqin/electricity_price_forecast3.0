from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .contracts import build_wide_frame
from .metrics import smape_floor50


@dataclass
class WeightFitResult:
    task: str
    period: str
    weights: dict[str, float]
    smape: float
    iterations: int


def _project_with_bounds(values: np.ndarray, lower_bound: float, upper_bound: float) -> np.ndarray:
    n = len(values)
    if n == 1:
        return np.array([1.0], dtype=float)

    clipped = np.clip(values.astype(float), lower_bound, upper_bound)
    total = float(clipped.sum())
    if abs(total - 1.0) <= 1e-9:
        return clipped

    free = np.ones(n, dtype=bool)
    weights = clipped.copy()
    target = 1.0

    for _ in range(20):
        if not free.any():
            break
        free_idx = np.where(free)[0]
        shift = (weights[free_idx].sum() - target) / len(free_idx)
        candidate = weights[free_idx] - shift
        below = candidate < lower_bound
        above = candidate > upper_bound
        if not below.any() and not above.any():
            weights[free_idx] = candidate
            break
        for idx_local, is_below, is_above in zip(free_idx, below, above):
            if is_below:
                weights[idx_local] = lower_bound
                free[idx_local] = False
            elif is_above:
                weights[idx_local] = upper_bound
                free[idx_local] = False
        target = 1.0 - weights[~free].sum()
    else:
        if free.any():
            free_idx = np.where(free)[0]
            weights[free_idx] = target / len(free_idx)

    if free.any():
        free_idx = np.where(free)[0]
        remaining = 1.0 - weights[~free].sum()
        weights[free_idx] = np.clip(remaining / len(free_idx), lower_bound, upper_bound)

    residual = 1.0 - weights.sum()
    if abs(residual) > 1e-8:
        for idx in np.argsort(-np.abs(weights)):
            candidate = weights[idx] + residual
            if lower_bound <= candidate <= upper_bound:
                weights[idx] = candidate
                residual = 0.0
                break
    return weights


def _initial_weights(preds: np.ndarray, y_true: np.ndarray) -> np.ndarray:
    errors = np.mean(np.abs(preds - y_true[:, None]), axis=0)
    scores = 1.0 / np.maximum(errors, 1e-6)
    return scores / scores.sum()


def _objective(preds: np.ndarray, y_true: np.ndarray, weights: np.ndarray, prior: np.ndarray, reg: float) -> float:
    ensemble = preds @ weights
    return smape_floor50(y_true, ensemble) + reg * float(np.sum((weights - prior) ** 2))


def fit_segment_weights(
    preds: np.ndarray,
    y_true: np.ndarray,
    *,
    reg: float = 0.1,
    steps: int = 300,
    lr: float = 0.05,
    lower_bound: float = -0.5,
    upper_bound: float = 1.2,
) -> tuple[np.ndarray, float, int]:
    n_models = preds.shape[1]
    w0 = _initial_weights(preds, y_true)
    w0 = _project_with_bounds(w0, lower_bound, upper_bound)
    prior = w0.copy()

    def objective(w: np.ndarray) -> float:
        return _objective(preds, y_true, w, prior, reg)

    # Try scipy SLSQP first (preferred: handles bounds + equality natively)
    try:
        from scipy.optimize import minimize as scipy_minimize

        bounds = [(lower_bound, upper_bound)] * n_models
        constraints = {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}

        result = scipy_minimize(
            objective,
            w0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": steps, "ftol": 1e-10, "disp": False},
        )
        best_weights = result.x
        # Ensure sum-to-one after numerical rounding
        best_weights = _project_with_bounds(best_weights, lower_bound, upper_bound)
        n_iter = result.nit
        smape_val = smape_floor50(y_true, preds @ best_weights)
        return best_weights, smape_val, n_iter

    except ImportError:
        pass

    # Fallback: hand-rolled projected gradient descent (original implementation)
    weights = w0.copy()
    best_weights = weights.copy()
    best_score = objective(weights)

    for iteration in range(steps):
        grad = np.zeros_like(weights)
        base_score = objective(weights)
        delta = 1e-4
        for idx in range(len(weights)):
            probe = weights.copy()
            probe[idx] += delta
            probe = _project_with_bounds(probe, lower_bound, upper_bound)
            grad[idx] = (objective(probe) - base_score) / delta
        weights = _project_with_bounds(weights - lr * grad, lower_bound, upper_bound)
        score = objective(weights)
        if score < best_score:
            best_score = score
            best_weights = weights.copy()

    return best_weights, smape_floor50(y_true, preds @ best_weights), steps


def fit_weights_from_long_table(
    df: pd.DataFrame,
    *,
    reg: float = 0.1,
    reg_map: dict[str, float] | None = None,
    lower_bound: float = -0.5,
    upper_bound: float = 1.2,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    wide = build_wide_frame(df)
    model_cols = [column for column in wide.columns if column not in {"task", "target_day", "ds", "period", "hour_business", "y_true"}]
    if not model_cols:
        raise ValueError("No model columns found after pivoting prediction table")

    weights_rows: list[dict[str, object]] = []
    report_rows: list[dict[str, object]] = []

    for (task, period), group in wide.groupby(["task", "period"]):
        task_model_cols = [column for column in model_cols if column in group.columns and group[column].notna().any()]
        if not task_model_cols:
            continue
        clean_group = group.dropna(subset=task_model_cols).copy()
        if clean_group.empty:
            continue
        preds = clean_group[task_model_cols].to_numpy(dtype=float)
        y_true = clean_group["y_true"].to_numpy(dtype=float)
        segment_reg = float(reg_map.get(period, reg)) if reg_map else float(reg)
        weights, smape_value, iterations = fit_segment_weights(
            preds,
            y_true,
            reg=segment_reg,
            lower_bound=float(lower_bound),
            upper_bound=float(upper_bound),
        )

        base_abs_errors = np.mean(np.abs(preds - y_true[:, None]), axis=0)

        for model_name, weight in zip(task_model_cols, weights):
            weights_rows.append(
                {
                    "task": task,
                    "period": period,
                    "model_name": model_name,
                    "weight": float(weight),
                    "sample_count": int(len(clean_group)),
                    "weight_lower_bound": float(lower_bound),
                    "weight_upper_bound": float(upper_bound),
                }
            )

        row = {
            "task": task,
            "period": period,
            "sample_count": int(len(clean_group)),
            "smape": float(smape_value),
            "iterations": int(iterations),
            "reg": float(segment_reg),
            "weight_sum": float(weights.sum()),
            "lower_bound": float(lower_bound),
            "upper_bound": float(upper_bound),
            "hit_lower_bound_count": int(np.sum(np.isclose(weights, lower_bound))),
            "hit_upper_bound_count": int(np.sum(np.isclose(weights, upper_bound))),
        }
        for model_name, weight, mae_value in zip(task_model_cols, weights, base_abs_errors):
            row[f"weight_{model_name}"] = float(weight)
            row[f"mae_{model_name}"] = float(mae_value)
        report_rows.append(row)

    return pd.DataFrame(weights_rows), pd.DataFrame(report_rows)
