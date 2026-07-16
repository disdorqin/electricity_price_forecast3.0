"""
model_loader.py — Load OUR configured model predictions into circuit rows.

This is the real integration point that replaces the placeholder
``_load_2_5_*_model_outputs`` (which returned ``[]``).

Model predictions are produced by an EXTERNAL process (the P1 day-ahead engine
for cfg05/xgboost_rich/catboost_rich; ledger_predict / SGDFNet / TimesFMBackend
for sgdfnet/timesfm) and ingested into the ledger via
``tools/ingest_model_predictions.py`` (which also satisfies the DB-import test).
The circuit then reads them back here, strictly by ``target_date`` (the raw
predictions are NOT per-circuit-run — they are generated once per trading day).

Default rosters are OUR 3.0 models:
  * Day-ahead : cfg05, xgboost_rich, catboost_rich   (NOT the 2.5 lightgbm)
  * Real-time : sgdfnet, timesfm, da_aware_sgdf_selector
Both are overridable via the circuit ``config`` dict.
"""

from __future__ import annotations

import logging
from typing import Any

from pipelines.production_circuit.contracts import CircuitStage, CircuitTask

logger = logging.getLogger(__name__)

# OUR 3.0 models (overridable via config["dayahead_models"] / config["realtime_models"]).
DEFAULT_DAYAHEAD_MODELS = ["cfg05", "xgboost_rich", "catboost_rich"]
DEFAULT_REALTIME_MODELS = ["sgdfnet", "timesfm", "da_aware_sgdf_selector", "a05_composite"]

# Conservative gate thresholds for the DA-aware SGDFNet selector (see
# configs/candidate_registry/realtime_da_sgdf_selector.yaml). The selector
# DEFAULTS to DA_anchor and only switches to SGDFNet at high-confidence,
# non-winter windows.
SELECTOR_SWITCH_REL_TOL = 0.10   # |sgdf - da| / da < 10% considered "confident"
WINTER_MONTHS = {11, 12, 1, 2}


def _stage_for(task: CircuitTask) -> CircuitStage:
    return (
        CircuitStage.DAYAHEAD_RAW_MODEL
        if task == CircuitTask.DAYAHEAD
        else CircuitStage.REALTIME_RAW_MODEL
    )


def load_model_outputs(
    conn: Any,
    run_id: str,
    target_date: str,
    task: CircuitTask,
    model_names: list[str],
) -> list[dict[str, Any]]:
    """Read raw model predictions for ``task`` + ``model_names`` from the ledger.

    Filtered by ``target_date`` + ``task`` + ``stage`` + ``model_name`` (NOT by
    ``run_id`` — raw predictions are generated once per trading day, outside the
    circuit run). Returns ``[]`` when none are present.

    Returns rows in the ``efm_predictions`` ledger-row format expected by
    ``write_stage_predictions`` / downstream steps.
    """
    if not model_names:
        return []
    stage = _stage_for(task)
    placeholders = ",".join(["%s"] * len(model_names))
    rows: list[dict[str, Any]] = []
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT hour_business, pred_price, model_name, model_version
            FROM efm_predictions
            WHERE target_date=%s AND task=%s AND stage=%s
              AND model_name IN ({placeholders})
            ORDER BY model_name, hour_business
            """,
            (target_date, task.value, stage.value, *model_names),
        )
        for hb, price, mname, mver in cur.fetchall():
            rows.append({
                "hour_business": int(hb),
                "pred_price": float(price),
                "model_name": str(mname),
                "model_version": str(mver) if mver else "v1",
                "is_shadow": False,
                "is_selected": False,
                "selected_reason": "model raw output",
                "quality_flags": ["model_raw"],
            })
    if rows:
        present = sorted(set(r["model_name"] for r in rows))
        logger.info("[model_loader] %s: loaded %d rows from models %s",
                    task.value, len(rows), present)
    return rows


def derive_da_aware_selector(
    conn: Any,
    target_date: str,
    sgdfnet_by_hour: dict[int, float],
) -> list[dict[str, Any]]:
    """Derive the ``da_aware_sgdf_selector`` candidate for real-time.

    Policy (per realtime_da_sgdf_selector.yaml): DEFAULT to DA_anchor; only
    switch to SGDFNet at high-confidence, non-winter windows. This keeps the
    selector a *fusion object* that is conservative by construction.
    """
    month = int(target_date.split("-")[1])
    is_winter = month in WINTER_MONTHS
    da_map: dict[int, float] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT hour_business, da_anchor FROM efm_actual_prices "
            "WHERE target_date=%s AND da_anchor IS NOT NULL ORDER BY hour_business",
            (target_date,),
        )
        da_map = {int(hb): float(v) for hb, v in cur.fetchall()}

    rows: list[dict[str, Any]] = []
    for hb in range(1, 25):
        da = da_map.get(hb)
        sg = sgdfnet_by_hour.get(hb)
        use_sg = (
            (not is_winter)
            and da is not None and da > 0
            and sg is not None
            and abs(sg - da) / da < SELECTOR_SWITCH_REL_TOL
        )
        value = sg if use_sg else da
        if value is None:
            continue
        rows.append({
            "hour_business": hb,
            "pred_price": float(value),
            "model_name": "da_aware_sgdf_selector",
            "model_version": "p2_11_shadow_adapter",
            "is_shadow": False,
            "is_selected": False,
            "selected_reason": (
                "selector -> SGDFNet (high-confidence window)"
                if use_sg else "selector -> DA_anchor (default/conservative)"
            ),
            "quality_flags": ["da_aware_selector", "rt" if use_sg else "da_default"],
        })
    return rows
