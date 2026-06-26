"""
Daily Ledger GEF (Generalized Ensemble Fusion) weight learner.

Learns fusion weights from the past 30 days of prediction + actual
ledger data. Updates weights day by day from D-1 (most recent) to
D-30 (oldest), using day_gate to weight recency.

Algorithm: BGEW (Bounded Generalized Exponentiated Weighting)

For each (task, period):
  1. Start with equal weights for all models.
  2. For each day in [D-1, D-2, ..., D-30]:
     a. Compute per-model loss for that day + period.
     b. Normalize loss by median of available models.
     c. Update: w_m *= exp(-eta * day_gate * normalized_loss_m)
     d. Clip: w_m = max(w_m, weight_floor)
     e. Renormalize: sum(w) = 1
  3. Apply evidence shrinkage to prevent overfitting on sparse data.

No validation tap / rolling OOF / online validation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ===========================================================================
# Metrics
# ===========================================================================

def smape_floor50(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """
    SMAPE-floor50 (correct formula per docs/metrics_calculation.md).

    Clips individual y_true and y_pred to floor=50 BEFORE computing SMAPE.
    This is the authoritative formula for 2.1.

    y_clip  = max(y_true, 50)
    pred_clip = max(y_pred, 50)
    SMAPE = mean(|pred_clip - y_clip| / ((|pred_clip| + |y_clip|) / 2)) * 100
    """
    yt = np.asarray(y_true, dtype=np.float64)
    yp = np.asarray(y_pred, dtype=np.float64)
    mask = ~(np.isnan(yt) | np.isnan(yp))
    if mask.sum() == 0:
        return np.nan

    yt = yt[mask]
    yp = yp[mask]

    # Clip each value to floor=50 (per docs: clip per value, not per pair sum)
    yt_clip = np.maximum(yt, 50.0)
    yp_clip = np.maximum(yp, 50.0)

    denom = (np.abs(yp_clip) + np.abs(yt_clip)) / 2.0
    smape = np.mean(np.abs(yp_clip - yt_clip) / denom) * 100.0
    return float(smape)


def mae_percent(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """
    MAE as percentage: 100 * MAE / max(median(|y_true_clip|), 50).

    Designed to be on the same 0-100 scale as SMAPE-floor50
    for composite loss blending.
    """
    yt = np.asarray(y_true, dtype=np.float64)
    yp = np.asarray(y_pred, dtype=np.float64)
    mask = ~(np.isnan(yt) | np.isnan(yp))
    if mask.sum() == 0:
        return np.nan

    yt = yt[mask]
    yp = yp[mask]

    mae = np.mean(np.abs(yt - yp))
    denominator = max(np.median(np.abs(np.maximum(yt, 50.0))), 50.0)
    return float(100.0 * mae / denominator)


def compute_daily_loss(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    loss_type: str = "composite",
) -> float:
    """
    Compute loss for a single model on a single day + period.

    Parameters
    ----------
    y_true, y_pred : array-like
        True and predicted values for the hours in this period.
    loss_type : str
        "smape" or "composite" (0.7*smape_floor50 + 0.3*mae_percent).
        Both on 0-100 scale.

    Returns
    -------
    float loss value (lower is better).
    """
    if loss_type == "smape":
        return smape_floor50(y_true, y_pred)
    else:
        s = smape_floor50(y_true, y_pred)
        m = mae_percent(y_true, y_pred)
        if np.isnan(s):
            return m
        if np.isnan(m):
            return s
        # Both on 0-100 scale, so balanced blending
        return 0.7 * s + 0.3 * m


# ===========================================================================
# Dataclasses
# ===========================================================================

@dataclass
class GEFConfig:
    """Configuration for the Daily Ledger GEF learner."""

    # Window
    window_days: int = 30

    # BGEW parameters
    eta: float = 0.8                # Learning rate
    weight_floor: float = 0.03      # Minimum weight per model
    day_gate_recent: float = 0.7    # Weight for D-1
    day_gate_oldest: float = 0.3    # Weight for D-30
    normalized_loss_min: float = 0.25
    normalized_loss_max: float = 4.0

    # Evidence shrinkage
    evidence_prior: float = 5.0     # Prior pseudo-count
    use_evidence_shrinkage: bool = True

    # Loss
    loss_type: str = "composite"    # "smape" or "composite"

    # Periods
    periods: tuple = ("1_8", "9_16", "17_24")


@dataclass
class WeightTraceRow:
    """Single row in the dynamic weight trace."""
    task: str
    period: str
    target_day: str
    age_days: int
    day_gate: float
    model_name: str
    weight_before: float
    loss: float
    normalized_loss: float
    weight_after: float


# ===========================================================================
# Main learner
# ===========================================================================

class DailyLedgerGEF:
    """
    Daily Ledger GEF weight learner.

    Learns per-(task, period) weights from the prediction + actual ledger
    using the BGEW algorithm with day-gated temporal decay.
    """

    def __init__(self, config: Optional[GEFConfig] = None):
        self.config = config or GEFConfig()
        self.weights_: dict = {}         # (task, period) → {model: weight}
        self.trace_: list[WeightTraceRow] = []

    def fit(
        self,
        training_table: pd.DataFrame,
    ) -> dict:
        """
        Learn weights from the training table.

        Parameters
        ----------
        training_table : pd.DataFrame
            Must contain: task, model_name, target_day, business_day,
            hour_business, period, y_pred, y_true, age_days, day_gate.

        Returns
        -------
        dict mapping (task, period) → {model_name: weight}
        """
        cfg = self.config
        self.trace_ = []

        # Get unique tasks and periods
        tasks = sorted(training_table["task"].unique())
        models = sorted(training_table["model_name"].unique())

        logger.info(
            f"DailyLedgerGEF.fit: {len(tasks)} tasks, {len(models)} models, "
            f"{len(training_table)} training rows"
        )

        weights = {}

        for task in tasks:
            task_df = training_table[training_table["task"] == task]

            for period in cfg.periods:
                period_df = task_df[task_df["period"] == period]
                key = (task, period)

                # Get sorted unique days for this period
                days = sorted(period_df["target_day"].unique())
                if len(days) == 0:
                    continue

                # Start with equal weights
                w = {m: 1.0 / len(models) for m in models}

                # Sort days by age_days ascending (D-1 first, D-30 last)
                day_info = (
                    period_df[["target_day", "age_days", "day_gate"]]
                    .drop_duplicates()
                    .sort_values("age_days")
                )

                for _, day_row in day_info.iterrows():
                    day = day_row["target_day"]
                    age = int(day_row["age_days"])
                    gate = float(day_row["day_gate"])

                    day_period_df = period_df[period_df["target_day"] == day]

                    if len(day_period_df) == 0:
                        continue

                    # Compute loss per model — each model's y_pred is aligned
                    # to its own y_true rows (sorted by hour_business, dropping NaN).
                    losses = {}
                    shape_errors = []
                    for m in models:
                        m_df = (
                            day_period_df[day_period_df["model_name"] == m]
                            .sort_values("hour_business")
                            .dropna(subset=["y_true", "y_pred"])
                        )

                        if len(m_df) == 0:
                            losses[m] = np.nan
                            continue

                        y_true_m = m_df["y_true"].values
                        y_pred_m = m_df["y_pred"].values

                        if len(y_true_m) != len(y_pred_m):
                            err = (
                                f"Loss shape mismatch for {task}/{period}/{day}/{m}: "
                                f"y_true={len(y_true_m)}, y_pred={len(y_pred_m)}"
                            )
                            shape_errors.append(err)
                            losses[m] = np.nan
                            continue

                        losses[m] = compute_daily_loss(y_true_m, y_pred_m, cfg.loss_type)

                    if shape_errors:
                        for err in shape_errors:
                            logger.error(err)
                        raise ValueError(
                            f"Shape mismatch in {task}/{period}/{day}: "
                            f"{len(shape_errors)} models affected. "
                            f"First error: {shape_errors[0]}"
                        )

                    # Available models
                    available = [m for m in models if not np.isnan(losses[m])]
                    if len(available) < 2:
                        # Not enough models to compare — skip this day
                        for m in models:
                            self.trace_.append(WeightTraceRow(
                                task=task, period=period, target_day=day,
                                age_days=age, day_gate=gate,
                                model_name=m,
                                weight_before=w.get(m, 0),
                                loss=losses.get(m, np.nan),
                                normalized_loss=np.nan,
                                weight_after=w.get(m, 0),
                            ))
                        continue

                    # Median loss of available models
                    available_losses = [losses[m] for m in available]
                    median_loss = float(np.median(available_losses))
                    if median_loss < 1e-6:
                        median_loss = 1e-6

                    # Update weights
                    for m in models:
                        w_before = w.get(m, cfg.weight_floor)

                        if m not in available:
                            # Missing model: keep weight, no update
                            self.trace_.append(WeightTraceRow(
                                task=task, period=period, target_day=day,
                                age_days=age, day_gate=gate,
                                model_name=m,
                                weight_before=w_before,
                                loss=np.nan,
                                normalized_loss=np.nan,
                                weight_after=w_before,
                            ))
                            continue

                        loss_m = losses[m]
                        norm_loss = loss_m / median_loss
                        norm_loss = float(np.clip(norm_loss, cfg.normalized_loss_min, cfg.normalized_loss_max))

                        # BGEW update
                        decay = np.exp(-cfg.eta * gate * norm_loss)
                        w_new = w_before * decay
                        w_new = max(w_new, cfg.weight_floor)

                        w[m] = w_new

                        self.trace_.append(WeightTraceRow(
                            task=task, period=period, target_day=day,
                            age_days=age, day_gate=gate,
                            model_name=m,
                            weight_before=w_before,
                            loss=loss_m,
                            normalized_loss=norm_loss,
                            weight_after=w_new,
                        ))

                    # Renormalize available model weights
                    total = sum(w[m] for m in models)
                    if total > 0:
                        for m in models:
                            w[m] = w[m] / total

                # --- Evidence shrinkage ---
                if cfg.use_evidence_shrinkage:
                    w = self._apply_evidence_shrinkage(
                        w, models, period_df, key
                    )

                weights[key] = dict(w)

        self.weights_ = weights

        logger.info(
            f"Learned weights for {len(weights)} (task, period) combinations"
        )

        return weights

    def _apply_evidence_shrinkage(
        self,
        w: dict,
        models: list[str],
        period_df: pd.DataFrame,
        key: tuple,
    ) -> dict:
        """
        Apply evidence shrinkage to prevent weights from overfitting
        when a model has too few observations.

        w_final = confidence * w_learned + (1 - confidence) * w_prior
        confidence = evidence_mass / (evidence_mass + evidence_prior)
        """
        cfg = self.config
        w_prior = {m: 1.0 / len(models) for m in models}

        for m in models:
            m_df = period_df[period_df["model_name"] == m]
            if len(m_df) == 0:
                w[m] = w_prior[m]
                continue

            evidence_mass = m_df["day_gate"].sum()
            confidence = evidence_mass / (evidence_mass + cfg.evidence_prior)
            w[m] = confidence * w.get(m, w_prior[m]) + (1.0 - confidence) * w_prior[m]

        # Renormalize
        total = sum(w.values())
        if total > 0:
            w = {m: v / total for m, v in w.items()}

        return w

    # =========================================================================
    # Output
    # =========================================================================

    def get_weights_df(self) -> pd.DataFrame:
        """Return weights as a DataFrame."""
        rows = []
        for (task, period), wdict in self.weights_.items():
            for model, weight in wdict.items():
                rows.append({
                    "task": task,
                    "period": period,
                    "model_name": model,
                    "weight": round(weight, 6),
                })
        return pd.DataFrame(rows)

    def get_trace_df(self) -> pd.DataFrame:
        """Return the dynamic weight trace as a DataFrame."""
        if not self.trace_:
            return pd.DataFrame()
        return pd.DataFrame([vars(r) for r in self.trace_])

    def get_coverage_report(
        self,
        training_table: pd.DataFrame,
    ) -> pd.DataFrame:
        """Generate coverage report per model per day. expected=24 rows."""
        if training_table.empty:
            return pd.DataFrame()

        coverage = (
            training_table
            .groupby(["task", "target_day", "model_name"])
            .size()
            .reset_index(name="n_pred")
        )
        coverage["n_expected"] = 24  # 24 hours per day, not 8
        coverage["coverage_pct"] = (
            coverage["n_pred"] / coverage["n_expected"] * 100
        ).round(1)

        # Status: ok if n_pred == 24, else incomplete
        coverage["status"] = coverage["n_pred"].apply(
            lambda x: "ok" if x == 24 else "incomplete"
        )

        return coverage

    def get_candidate_metrics(self, training_table: pd.DataFrame) -> pd.DataFrame:
        """Compute per-model metrics from the training table."""
        rows = []
        for (task, model), grp in training_table.groupby(["task", "model_name"]):
            if len(grp) == 0:
                continue
            smape = smape_floor50(grp["y_true"].values, grp["y_pred"].values)
            mp = mae_percent(grp["y_true"].values, grp["y_pred"].values)
            mae_raw = float(np.mean(np.abs(grp["y_true"].values - grp["y_pred"].values)))

            learner_loss = np.nan
            if not np.isnan(smape) and not np.isnan(mp):
                learner_loss = round(0.7 * smape + 0.3 * mp, 4)
            elif not np.isnan(smape):
                learner_loss = round(smape, 4)
            elif not np.isnan(mp):
                learner_loss = round(mp, 4)

            rows.append({
                "task": task,
                "model_name": model,
                "n_samples": len(grp),
                "learner_loss": learner_loss,
                "smape_floor50": round(smape, 4) if not np.isnan(smape) else None,
                "mae": round(mae_raw, 4),
                "mae_percent": round(mp, 4) if not np.isnan(mp) else None,
            })
        return pd.DataFrame(rows)
