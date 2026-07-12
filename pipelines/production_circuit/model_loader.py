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
DEFAULT_REALTIME_MODELS = ["sgdfnet", "timesfm", "da_aware_sgdf_selector"]

# Conservative gate thresholds for the DA-aware SGDFNet selector (see
# configs/candidate_registry/realtime_da_sgdf_selector.yaml). The selector
# DEFAULTS to DA_anchor and only switches to SGDFNet at high-confidence,
# non-winter windows.
SELECTOR_SWITCH_REL_TOL = 0.10          # non-winter: |sgdf-da|/da < 10% -> trust SGDFNet
SELECTOR_SWITCH_REL_TOL_WINTER = 0.20   # winter: relaxed tolerance (keep diversity, don't disable)
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
        # IMPORTANT: raw predictions are produced ONCE per trading day by the
        # external ingest step into ``efm3_raw_<date>_<task>`` runs. The circuit
        # re-persists them into its own ``efm3_pc_%`` run for provenance, but we
        # MUST read only the canonical ingest runs here — otherwise a stale or
        # the circuit's own write-back copy would be re-loaded and fused, which
        # both contaminates the result and compounds duplicates on every run.
        #
        # target_date is no longer stored on efm_predictions (3NF); join efm_runs.
        # stage/model are foreign keys to efm_dim_* (joined for name filter).
        cur.execute(
            f"""
            SELECT p.hour_business, p.pred_price, m.name AS model_name, p.model_version
            FROM efm_predictions p
            JOIN efm_runs r ON p.run_id = r.run_id
            JOIN efm_dim_stage s ON p.stage_id = s.id
            JOIN efm_dim_model m ON p.model_id = m.id
            WHERE r.target_date=%s AND p.task=%s AND s.name=%s
              AND m.name IN ({placeholders})
              AND p.run_id NOT LIKE 'efm3_pc_%%'
            ORDER BY m.name, p.hour_business
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

    Policy (PER-HOUR mixing): decide independently for each hour. Use SGDFNet at
    hour hb when |sgdf-da|/da < tolerance, else fall back to DA_anchor. Winter
    months use a RELAXED tolerance (SELECTOR_SWITCH_REL_TOL_WINTER) instead of
    disabling SGDFNet entirely — this preserves RT-chain model diversity in
    Nov-Feb while staying conservative on low-confidence hours.
    """
    month = int(target_date.split("-")[1])
    is_winter = month in WINTER_MONTHS
    tol = SELECTOR_SWITCH_REL_TOL_WINTER if is_winter else SELECTOR_SWITCH_REL_TOL
    da_map: dict[int, float] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT hour_business, da_anchor FROM efm_actual_prices "
            "WHERE target_date=%s AND da_anchor IS NOT NULL ORDER BY hour_business",
            (target_date,),
        )
        da_map = {int(hb): float(v) for hb, v in cur.fetchall()}

    # Fallback: future-date DA anchor not published yet -> use ingested DA pred.
    if not da_map:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.hour_business, AVG(p.pred_price) "
                "FROM efm_predictions p JOIN efm_runs r ON p.run_id=r.run_id "
                "JOIN efm_dim_stage s ON p.stage_id=s.id "
                "WHERE r.target_date=%s AND p.task='dayahead' "
                "AND s.name='dayahead_raw_model' AND p.run_id NOT LIKE 'efm3_pc_%%' "
                "GROUP BY p.hour_business ORDER BY p.hour_business",
                (target_date,),
            )
            da_map = {int(hb): float(v) for hb, v in cur.fetchall()}

    rows: list[dict[str, Any]] = []
    n_sg = 0
    for hb in range(1, 25):
        da = da_map.get(hb)
        sg = sgdfnet_by_hour.get(hb)

        # Per-hour decision: prefer SGDFNet when it is close to the DA anchor.
        rel_dev = (abs(sg - da) / abs(da)) if (da not in (None, 0) and sg is not None) else None
        use_sg = (sg is not None) and (rel_dev is not None) and (rel_dev < tol)

        if use_sg:
            value = sg
            reason_text = f"selector -> SGDFNet (rel_dev={rel_dev:.3f} < {tol})"
            flag = "rt"
            n_sg += 1
        else:
            value = da
            reason_text = "selector -> DA_anchor (low-confidence / missing sgdf)"
            flag = "da_default"

        if value is None:
            continue
        rows.append({
            "hour_business": hb,
            "pred_price": float(value),
            "model_name": "da_aware_sgdf_selector",
            "model_version": "p2_11_shadow_adapter",
            "is_shadow": False,
            "is_selected": False,
            "selected_reason": reason_text,
            "quality_flags": ["da_aware_selector", flag],
        })
    logger.info("[da_aware_selector] PER-HOUR -> %d/24h SGDFNet, %d/24h DA (tol=%.2f, %s)",
                n_sg, len(rows) - n_sg, tol, "winter" if is_winter else "non-winter")
    return rows
