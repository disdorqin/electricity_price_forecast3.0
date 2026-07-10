"""
step_recorder.py — Persistence layer for the EFM3 Production Circuit (DB Ledger V2).

Every function here writes to the MySQL ledger using pymysql-style parameter
markers (%s). The functions are deliberately free of any heavy imports so they
can be unit-tested with a lightweight fake DB connection (no real MySQL needed).

Public surface used by the circuit modules:
  * StepRecorder(db_mgr).record(...)                 -> one row in efm_pipeline_steps
  * insert_lineage_edge(conn, ...)                   -> efm_prediction_lineage_edges
  * insert_repair_decision(conn, RepairDecision)     -> efm_repair_decisions
  * insert_fusion_candidate(conn, FusionCandidate)   -> efm_fusion_candidates
  * insert_task_final(conn, TaskFinal) -> id         -> efm_task_finals
  * insert_delivery_final(conn, DeliveryFinal) -> id  -> efm_delivery_finals
  * insert_metric_run(conn, dict)                    -> efm_metric_runs
  * write_stage_predictions(conn, ...) -> [ids]       -> efm_predictions + efm_prediction_batches
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from pipelines.production_circuit.contracts import (
    CircuitStage,
    CircuitTask,
    DeliveryFinal,
    FusionCandidate,
    RepairDecision,
    TaskFinal,
)

logger = logging.getLogger(__name__)


def _val(x: Any) -> Any:
    """Coerce enums / dicts to DB-friendly scalars."""
    if hasattr(x, "value"):
        return x.value
    if isinstance(x, dict):
        return json.dumps(x, ensure_ascii=False)
    return x


# ── Step recorder ────────────────────────────────────────────────────────

class StepRecorder:
    """Writes the per-step execution ledger (efm_pipeline_steps)."""

    def __init__(self, db_mgr: Any):
        self.db_mgr = db_mgr

    def record(
        self,
        run_id: str,
        target_date: str,
        task: Any,
        step_name: str,
        step_order: int,
        status: str,
        input_count: int = 0,
        output_count: int = 0,
        message: str = "",
        config_json: Optional[dict] = None,
        metrics_json: Optional[dict] = None,
    ) -> None:
        conn = self.db_mgr.new_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO efm_pipeline_steps
                    (run_id, target_date, task, step_name, step_order, status,
                     input_count, output_count, message, config_json, metrics_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id, target_date, _val(task), step_name, step_order, status,
                    input_count, output_count, message,
                    json.dumps(config_json, ensure_ascii=False) if config_json is not None else None,
                    json.dumps(metrics_json, ensure_ascii=False) if metrics_json is not None else None,
                ),
            )
            conn.commit()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[StepRecorder] failed to record step %s: %s", step_name, exc)
        finally:
            conn.close()


# ── Predictions + batches ────────────────────────────────────────────────

def write_stage_predictions(
    conn: Any,
    run_id: str,
    target_date: str,
    task: Any,
    stage: Any,
    rows: list[dict[str, Any]],
    source_step: Optional[str] = None,
    is_final_candidate: bool = False,
    is_shadow: bool = False,
) -> list[int]:
    """Write 24h of predictions to efm_predictions and record one batch.

    Returns the list of assigned prediction ids (one per row, in order).
    """
    task_v = _val(task)
    stage_v = _val(stage)
    ids: list[int] = []
    cur = conn.cursor()

    for row in rows:
        cur.execute(
            """
            INSERT IGNORE INTO efm_predictions
                (run_id, target_date, task, stage, hour_business, model_name,
                 model_version, pred_price, is_shadow, is_selected,
                 selected_reason, quality_flags)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_id, target_date, task_v, stage_v,
                int(row.get("hour_business", 0)),
                row.get("model_name"),
                row.get("model_version"),
                float(row.get("pred_price", 0.0)),
                bool(row.get("is_shadow", is_shadow)),
                bool(row.get("is_selected", False)),
                row.get("selected_reason"),
                json.dumps(row.get("quality_flags"), ensure_ascii=False)
                if row.get("quality_flags") is not None else None,
            ),
        )
        ids.append(int(cur.lastrowid))

    # Fallback: if lastrowid is 0 (duplicate skipped), query the existing id
    for i, row in enumerate(rows):
        if ids[i] == 0:
            cur.execute(
                "SELECT id FROM efm_predictions WHERE run_id=%s AND target_date=%s "
                "AND hour_business=%s AND stage=%s AND model_name=%s",
                (run_id, target_date, int(row.get("hour_business", 0)),
                 stage_v, row.get("model_name")),
            )
            result = cur.fetchone()
            if result:
                ids[i] = int(result[0])

    # Batch bookkeeping (sha256 of the stage + row hours).
    if rows:
        digest_src = json.dumps(
            {"run_id": run_id, "target_date": target_date, "task": task_v,
             "stage": stage_v, "hours": sorted(int(r.get("hour_business", 0)) for r in rows)},
            sort_keys=True, ensure_ascii=False,
        )
        batch_hash = hashlib.sha256(digest_src.encode()).hexdigest()
        batch_id = f"{run_id}_{task_v}_{stage_v}"
        cur.execute(
            """
            INSERT INTO efm_prediction_batches
                (batch_id, run_id, target_date, task, stage, model_name,
                 model_version, source_step, row_count, is_final_candidate,
                 is_shadow, batch_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE row_count=VALUES(row_count),
                batch_hash=VALUES(batch_hash)
            """,
            (
                batch_id, run_id, target_date, task_v, stage_v,
                rows[0].get("model_name"), rows[0].get("model_version"),
                source_step, len(rows), bool(is_final_candidate),
                bool(is_shadow), batch_hash,
            ),
        )

    conn.commit()
    return ids


# ── Lineage ──────────────────────────────────────────────────────────────

def insert_lineage_edge(
    conn: Any,
    run_id: str,
    target_date: str,
    relation_type: str,
    parent_prediction_id: Optional[int],
    child_prediction_id: Optional[int],
    relation_json: Optional[dict] = None,
) -> None:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT IGNORE INTO efm_prediction_lineage_edges
                (run_id, target_date, parent_prediction_id, child_prediction_id,
                 relation_type, relation_json)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                run_id, target_date, parent_prediction_id, child_prediction_id,
                relation_type,
                json.dumps(relation_json, ensure_ascii=False) if relation_json is not None else None,
            ),
        )
        conn.commit()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("insert_lineage_edge skipped (%s: pid=%s cid=%s): %s",
                       relation_type, parent_prediction_id, child_prediction_id, exc)


# ── Repair decisions ─────────────────────────────────────────────────────

def insert_repair_decision(conn: Any, d: RepairDecision) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO efm_repair_decisions
            (run_id, target_date, task, hour_business, repair_stage,
             source_prediction_id, repaired_prediction_id, rule_name,
             before_value, after_value, reason, severity)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            d.run_id, d.target_date, _val(d.task), int(d.hour_business),
            _val(d.repair_stage), d.source_prediction_id, d.repaired_prediction_id,
            d.rule_name, d.before_value, d.after_value, d.reason, d.severity,
        ),
    )
    conn.commit()


# ── Fusion candidates ────────────────────────────────────────────────────

def insert_fusion_candidate(conn: Any, c: FusionCandidate) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO efm_fusion_candidates
            (run_id, target_date, task, hour_business, candidate_prediction_id,
             candidate_model, candidate_stage, weight_value, rank_value,
             score_json, selected, rejected_reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            c.run_id, c.target_date, _val(c.task), int(c.hour_business),
            c.candidate_prediction_id, c.candidate_model, _val(c.candidate_stage),
            c.weight_value, c.rank_value,
            json.dumps(c.score_json, ensure_ascii=False) if c.score_json else None,
            bool(c.selected), c.rejected_reason,
        ),
    )
    conn.commit()


# ── Task finals (DA / RT separated) ──────────────────────────────────────

def insert_task_final(conn: Any, tf: TaskFinal) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO efm_task_finals
            (run_id, target_date, task, hour_business, final_prediction_id,
             final_stage, final_price, source_policy, confidence_score)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            id = LAST_INSERT_ID(id),
            final_prediction_id = VALUES(final_prediction_id),
            final_stage = VALUES(final_stage),
            final_price = VALUES(final_price),
            source_policy = VALUES(source_policy),
            confidence_score = VALUES(confidence_score)
        """,
        (
            tf.run_id, tf.target_date, _val(tf.task), int(tf.hour_business),
            tf.final_prediction_id, _val(tf.final_stage),
            float(tf.final_price), tf.source_policy, tf.confidence_score,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


# ── Delivery finals (provenance) ─────────────────────────────────────────

def insert_delivery_final(conn: Any, df: DeliveryFinal) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO efm_delivery_finals
            (run_id, target_date, hour_business, dayahead_final_id,
             realtime_final_id, delivery_prediction_id, delivery_price,
             delivery_policy, separator_rule, fallback_reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            id = LAST_INSERT_ID(id),
            dayahead_final_id = VALUES(dayahead_final_id),
            realtime_final_id = VALUES(realtime_final_id),
            delivery_prediction_id = VALUES(delivery_prediction_id),
            delivery_price = VALUES(delivery_price),
            delivery_policy = VALUES(delivery_policy),
            separator_rule = VALUES(separator_rule),
            fallback_reason = VALUES(fallback_reason)
        """,
        (
            df.run_id, df.target_date, int(df.hour_business),
            df.dayahead_final_id, df.realtime_final_id, df.delivery_prediction_id,
            float(df.delivery_price), df.delivery_policy,
            df.separator_rule, df.fallback_reason,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


# ── Metric runs (scope-isolated) ─────────────────────────────────────────

def insert_metric_run(conn: Any, m: dict) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO efm_metric_runs
            (metric_run_id, run_id, target_date_start, target_date_end,
             metric_scope, pred_stage, actual_source, smape, mae, rmse, mape,
             wmape, evaluable_days, evaluable_hours, config_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            smape = VALUES(smape), mae = VALUES(mae), rmse = VALUES(rmse),
            mape = VALUES(mape), wmape = VALUES(wmape),
            evaluable_hours = VALUES(evaluable_hours),
            config_json = VALUES(config_json)
        """,
        (
            m["metric_run_id"], m.get("run_id"), m["target_date_start"],
            m["target_date_end"], m["metric_scope"], m.get("pred_stage"),
            m.get("actual_source"), m.get("smape"), m.get("mae"), m.get("rmse"),
            m.get("mape"), m.get("wmape"), m.get("evaluable_days"),
            m.get("evaluable_hours"),
            json.dumps(m.get("config_json"), ensure_ascii=False)
            if m.get("config_json") is not None else None,
        ),
    )
    conn.commit()
