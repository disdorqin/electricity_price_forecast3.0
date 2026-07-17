"""
step_recorder.py — Persistence layer for the EFM3 Production Circuit (DB Ledger V2).

Every function here writes to the MySQL ledger using pymysql-style parameter
markers (%s). The functions are deliberately free of any heavy imports so they
can be unit-tested with a lightweight fake DB connection (no real MySQL needed).

MIGRATION NOTE (006): The DB uses a 3NF dimensional schema where string values
(stage, model_name, step_name) are FK-referenced from dimension tables. All
INSERTs now include FK ID columns resolved via DimResolver. A DimResolver
instance is passed through CircuitContext (ctx.dim_resolver).

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
        resolver = getattr(self.db_mgr, "_dim_resolver", None)
        try:
            step_name_id = resolver.resolve("step", step_name) if resolver else None
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO efm_pipeline_steps
                    (run_id, task, step_name, step_name_id, step_order, status,
                     input_count, output_count, message, config_json, metrics_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id, _val(task), step_name, step_name_id, step_order, status,
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
    resolver = getattr(conn, "_dim_resolver", None)
    stage_id = resolver.resolve("stage", stage_v) if resolver else None
    ids: list[int] = []
    cur = conn.cursor()

    for row in rows:
        model_name = row.get("model_name")
        model_id = resolver.resolve("model", model_name) if (resolver and model_name) else None
        # efm_predictions has both string columns (stage, model_name) and
        # nullable FK columns (stage_id, model_id). Write both — the FK
        # columns are populated when a resolver is available.
        cur.execute(
            """
            INSERT INTO efm_predictions
                (run_id, target_date, task, stage, stage_id, hour_business,
                 model_name, model_id, model_version, pred_price, is_shadow,
                 is_selected, selected_reason, quality_flags)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_id, target_date, task_v, stage_v, stage_id,
                int(row.get("hour_business", 0)),
                model_name, model_id, row.get("model_version"),
                float(row.get("pred_price", 0.0)),
                bool(row.get("is_shadow", is_shadow)),
                bool(row.get("is_selected", False)),
                row.get("selected_reason"),
                json.dumps(row.get("quality_flags"), ensure_ascii=False)
                if row.get("quality_flags") is not None else None,
            ),
        )
        ids.append(int(cur.lastrowid))

    # Batch bookkeeping (sha256 of the stage + row hours).
    if rows:
        source_step_id = resolver.resolve("step", source_step) if (resolver and source_step) else None
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
                (batch_id, run_id, task, stage_id, model_id, model_version,
                 source_step_id, row_count, is_final_candidate,
                 is_shadow, batch_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE row_count=VALUES(row_count),
                batch_hash=VALUES(batch_hash)
            """,
            (
                batch_id, run_id, task_v, stage_id,
                resolver.resolve("model", rows[0].get("model_name")) if resolver else None,
                rows[0].get("model_version"),
                source_step_id, len(rows), bool(is_final_candidate),
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
    resolver = getattr(conn, "_dim_resolver", None)
    relation_id = resolver.resolve("relation", relation_type) if resolver else None
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO efm_prediction_lineage_edges
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


# ── Repair decisions ─────────────────────────────────────────────────────

def insert_repair_decision(conn: Any, d: RepairDecision) -> None:
    resolver = getattr(conn, "_dim_resolver", None)
    repair_stage_id = resolver.resolve("repairstage", _val(d.repair_stage)) if resolver else None
    rule_id = resolver.resolve("rule", d.rule_name) if resolver else None
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
    resolver = getattr(conn, "_dim_resolver", None)
    candidate_model_id = resolver.resolve("model", c.candidate_model) if resolver else None
    candidate_stage_id = resolver.resolve("stage", _val(c.candidate_stage)) if resolver else None
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
    resolver = getattr(conn, "_dim_resolver", None)
    final_stage_id = resolver.resolve("stage", _val(tf.final_stage)) if resolver else None
    source_policy_id = resolver.resolve("policy", tf.source_policy) if (resolver and tf.source_policy) else None
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO efm_task_finals
            (run_id, target_date, task, hour_business, final_prediction_id,
             final_stage_id, final_price, source_policy_id, confidence_score)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            id = LAST_INSERT_ID(id),
            final_prediction_id = VALUES(final_prediction_id),
            final_stage_id = VALUES(final_stage_id),
            final_price = VALUES(final_price),
            source_policy_id = VALUES(source_policy_id),
            confidence_score = VALUES(confidence_score)
        """,
        (
            tf.run_id, tf.target_date, _val(tf.task), int(tf.hour_business),
            tf.final_prediction_id, final_stage_id,
            float(tf.final_price), source_policy_id, tf.confidence_score,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


# ── Delivery finals (provenance) ─────────────────────────────────────────

def insert_delivery_final(conn: Any, df: DeliveryFinal) -> int:
    resolver = getattr(conn, "_dim_resolver", None)
    delivery_policy_id = resolver.resolve("policy", df.delivery_policy) if (resolver and df.delivery_policy) else None
    separator_rule_id = resolver.resolve("rule", df.separator_rule) if (resolver and df.separator_rule) else None
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
