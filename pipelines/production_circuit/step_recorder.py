"""
step_recorder.py — Persistence layer for the EFM3 Production Circuit (3NF ledger).

Every function writes to the MySQL 3NF ledger using pymysql-style parameter
markers (%s). Free-text domains (stage/model/policy/rule/...) are resolved to
surrogate ids via :mod:`common.db.dimensions`; ``target_date`` is no longer
stored on run-children (derived by joining ``efm_runs``).

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

from common.db.dimensions import resolve_dim_id

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
        # target_date is recorded on efm_runs; efm_pipeline_steps references run_id only.
        conn = self.db_mgr.new_connection()
        try:
            step_name_id = resolve_dim_id(conn, "step", step_name, step_name)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO efm_pipeline_steps
                    (run_id, task, step_name_id, step_order, status,
                     input_count, output_count, message, config_json, metrics_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id, _val(task), step_name_id, step_order, status,
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
    stage_id = resolve_dim_id(conn, "stage", stage_v, stage_v)
    ids: list[int] = []
    cur = conn.cursor()

    for row in rows:
        model_id = resolve_dim_id(conn, "model", row.get("model_name"), row.get("model_name"))
        cur.execute(
            """
            INSERT IGNORE INTO efm_predictions
                (run_id, hour_business, task, stage_id, model_id, model_version,
                 pred_price, is_shadow, is_selected, selected_reason, quality_flags)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_id, int(row.get("hour_business", 0)), task_v, stage_id, model_id,
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
            model_id = resolve_dim_id(conn, "model", row.get("model_name"), row.get("model_name"))
            cur.execute(
                "SELECT id FROM efm_predictions WHERE run_id=%s "
                "AND hour_business=%s AND stage_id=%s AND model_id=%s",
                (run_id, int(row.get("hour_business", 0)), stage_id, model_id),
            )
            result = cur.fetchone()
            if result:
                ids[i] = int(result[0])

    # Batch bookkeeping
    if rows:
        digest_src = json.dumps(
            {"run_id": run_id, "task": task_v,
             "stage": stage_v, "hours": sorted(int(r.get("hour_business", 0)) for r in rows)},
            sort_keys=True, ensure_ascii=False,
        )
        batch_hash = hashlib.sha256(digest_src.encode()).hexdigest()
        batch_id = f"{run_id}_{task_v}_{stage_v}"
        source_step_id = resolve_dim_id(conn, "step", source_step, source_step) if source_step else None
        model_0 = rows[0].get("model_name")
        model_id_0 = resolve_dim_id(conn, "model", model_0, model_0) if model_0 else None
        cur.execute(
            """
            INSERT INTO efm_prediction_batches
                (batch_id, run_id, task, stage_id, model_id, model_version,
                 source_step_id, row_count, is_final_candidate, is_shadow, batch_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE row_count=VALUES(row_count),
                batch_hash=VALUES(batch_hash)
            """,
            (
                batch_id, run_id, task_v, stage_id, model_id_0,
                rows[0].get("model_version"), source_step_id, len(rows),
                bool(is_final_candidate), bool(is_shadow), batch_hash,
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
    relation_id = resolve_dim_id(conn, "relation", relation_type, relation_type)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT IGNORE INTO efm_prediction_lineage_edges
                (run_id, parent_prediction_id, child_prediction_id,
                 relation_id, relation_json)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                run_id, parent_prediction_id, child_prediction_id,
                relation_id,
                json.dumps(relation_json, ensure_ascii=False) if relation_json is not None else None,
            ),
        )
        conn.commit()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("insert_lineage_edge skipped (%s: pid=%s cid=%s): %s",
                       relation_type, parent_prediction_id, child_prediction_id, exc)


# ── Repair decisions ─────────────────────────────────────────────────────

def insert_repair_decision(conn: Any, d: RepairDecision) -> None:
    repair_stage_id = resolve_dim_id(conn, "repairstage", _val(d.repair_stage), _val(d.repair_stage))
    rule_id = resolve_dim_id(conn, "rule", d.rule_name, d.rule_name)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO efm_repair_decisions
            (run_id, task, hour_business, repair_stage_id,
             source_prediction_id, repaired_prediction_id, rule_id,
             before_value, after_value, reason, severity)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            d.run_id, _val(d.task), int(d.hour_business),
            repair_stage_id, d.source_prediction_id, d.repaired_prediction_id,
            rule_id, d.before_value, d.after_value, d.reason, d.severity,
        ),
    )
    conn.commit()


# ── Fusion candidates ────────────────────────────────────────────────────

def insert_fusion_candidate(conn: Any, c: FusionCandidate) -> None:
    candidate_model_id = resolve_dim_id(conn, "model", c.candidate_model, c.candidate_model)
    candidate_stage_id = resolve_dim_id(conn, "stage", _val(c.candidate_stage), _val(c.candidate_stage))
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO efm_fusion_candidates
            (run_id, task, hour_business, candidate_prediction_id,
             candidate_model_id, candidate_stage_id, weight_value, rank_value,
             score_json, selected, rejected_reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            c.run_id, _val(c.task), int(c.hour_business),
            c.candidate_prediction_id, candidate_model_id, candidate_stage_id,
            c.weight_value, c.rank_value,
            json.dumps(c.score_json, ensure_ascii=False) if c.score_json else None,
            bool(c.selected), c.rejected_reason,
        ),
    )
    conn.commit()


# ── Task finals (DA / RT separated) ──────────────────────────────────────

def insert_task_final(conn: Any, tf: TaskFinal) -> int:
    final_stage_id = resolve_dim_id(conn, "stage", _val(tf.final_stage), _val(tf.final_stage))
    source_policy_id = resolve_dim_id(conn, "policy", tf.source_policy, tf.source_policy)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO efm_task_finals
            (run_id, task, hour_business, final_prediction_id,
             final_stage_id, final_price, source_policy_id, confidence_score)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            id = LAST_INSERT_ID(id),
            final_prediction_id = VALUES(final_prediction_id),
            final_stage_id = VALUES(final_stage_id),
            final_price = VALUES(final_price),
            source_policy_id = VALUES(source_policy_id),
            confidence_score = VALUES(confidence_score)
        """,
        (
            tf.run_id, _val(tf.task), int(tf.hour_business),
            tf.final_prediction_id, final_stage_id,
            float(tf.final_price), source_policy_id, tf.confidence_score,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


# ── Delivery finals (provenance) ─────────────────────────────────────────

def insert_delivery_final(conn: Any, df: DeliveryFinal) -> int:
    delivery_policy_id = resolve_dim_id(conn, "policy", df.delivery_policy, df.delivery_policy)
    separator_rule_id = resolve_dim_id(conn, "rule", df.separator_rule, df.separator_rule) if df.separator_rule else None
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO efm_delivery_finals
            (run_id, hour_business, dayahead_final_id,
             realtime_final_id, delivery_prediction_id, delivery_price,
             delivery_policy_id, separator_rule_id, fallback_reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            id = LAST_INSERT_ID(id),
            dayahead_final_id = VALUES(dayahead_final_id),
            realtime_final_id = VALUES(realtime_final_id),
            delivery_prediction_id = VALUES(delivery_prediction_id),
            delivery_price = VALUES(delivery_price),
            delivery_policy_id = VALUES(delivery_policy_id),
            separator_rule_id = VALUES(separator_rule_id),
            fallback_reason = VALUES(fallback_reason)
        """,
        (
            df.run_id, int(df.hour_business),
            df.dayahead_final_id, df.realtime_final_id, df.delivery_prediction_id,
            float(df.delivery_price), delivery_policy_id,
            separator_rule_id, df.fallback_reason,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


# ── Metric runs (scope-isolated; window columns kept) ─────────────────────

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
