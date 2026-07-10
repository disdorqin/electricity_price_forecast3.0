"""
3NF ledger smoke test — verifies the normalized data-access layer works
end-to-end against the live MySQL 3NF schema (dimension resolver + repos +
step_recorder). Run with the conda epf-2 interpreter (has pymysql):

    PYTHONPATH=. <conda>/python.exe tests/test_3nf_ledger_smoke.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.db.connection import DbConnectionManager
from common.db.dimensions import clear_dim_cache, dim_name, resolve_dim_id
from common.db.models import (
    PostflightCheckRecord,
    PredictionRecord,
    RunRecord,
)
from common.db.repositories import (
    create_run,
    fetch_predictions,
    insert_prediction,
)
from pipelines.production_circuit.contracts import (
    CircuitStage,
    CircuitTask,
    DeliveryFinal,
    FusionCandidate,
    RepairDecision,
    RepairStage,
    TaskFinal,
)
from pipelines.production_circuit.step_recorder import (
    insert_delivery_final,
    insert_fusion_candidate,
    insert_repair_decision,
    insert_task_final,
)

DB_URL = os.environ.get(
    "EFM3_DB_URL", "mysql+pymysql://root:Zlt20060313%23@127.0.0.1:3306/efm3"
).replace("%%23", "%23")

TARGET_DATE = "2099-01-01"
RUN_ID = f"efm3_smoke_{TARGET_DATE}"


def main() -> int:
    mgr = DbConnectionManager(db_url=DB_URL)
    conn = mgr.new_connection()
    clear_dim_cache()
    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)
            print("  FAIL:", msg)
        else:
            print("  ok:", msg)

    try:
        # 1. run
        create_run(conn, RunRecord(run_id=RUN_ID, target_date=TARGET_DATE, mode="dry_run"))
        print("[1] create_run")

        # 2. dimension resolver auto-creates on write
        sid = resolve_dim_id(conn, "stage", "raw_model", "raw_model")
        mid = resolve_dim_id(conn, "model", "cfg05", "cfg05")
        check(sid and mid, "resolve_dim_id creates stage/model")
        check(dim_name(conn, "stage", sid) == "raw_model", "dim_name inverse works")

        # 3. predictions (resolves stage/model ids internally)
        for hb in range(1, 25):
            insert_prediction(conn, PredictionRecord(
                run_id=RUN_ID, target_date=TARGET_DATE, hour_business=hb,
                task="dayahead", stage="raw_model", model_name="cfg05",
                pred_price=350.0 + hb, model_version="v1"))
        print("[3] insert 24 predictions")

        rows = fetch_predictions(conn, RUN_ID, task="dayahead", stage="raw_model")
        check(len(rows) == 24, f"fetch_predictions returns 24 (got {len(rows)})")
        check(all(r["stage"] == "raw_model" and r["model_name"] == "cfg05" for r in rows),
              "fetch_predictions joins dim names (stage/model_name)")

        # 4. circuit writers (task final / fusion candidate / repair / delivery)
        insert_task_final(conn, TaskFinal(
            run_id=RUN_ID, target_date=TARGET_DATE, task=CircuitTask.DAYAHEAD, hour_business=1,
            final_prediction_id=None, final_stage=CircuitStage.DAYAHEAD_TASK_FINAL,
            final_price=380.5, source_policy="bgew_v1"))
        insert_fusion_candidate(conn, FusionCandidate(
            run_id=RUN_ID, target_date=TARGET_DATE, task=CircuitTask.REALTIME, hour_business=2,
            candidate_prediction_id=None, candidate_model="sgdfnet",
            candidate_stage=CircuitStage.REALTIME_RAW_MODEL, weight_value=0.6,
            rank_value=1, selected=True, rejected_reason=None))
        insert_repair_decision(conn, RepairDecision(
            run_id=RUN_ID, target_date=TARGET_DATE, task=CircuitTask.REALTIME, hour_business=3,
            repair_stage=RepairStage.NEGATIVE_PRICE,
            source_prediction_id=None, repaired_prediction_id=None,
            rule_name="extreme_residual", before_value=900.0, after_value=50.0,
            reason="clamp", severity="warning"))
        insert_delivery_final(conn, DeliveryFinal(
            run_id=RUN_ID, target_date=TARGET_DATE, hour_business=1, dayahead_final_id=None,
            realtime_final_id=None, delivery_prediction_id=None,
            delivery_price=365.0, delivery_policy="separator_v1",
            separator_rule="da_priority", fallback_reason=None))
        print("[4] circuit writers (task_final/fusion/repair/delivery)")

        # 5. verify dim tables got populated and FK integrity holds
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM efm_dim_stage")
            n_stage = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM efm_dim_model")
            n_model = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM efm_task_finals WHERE run_id=%s", (RUN_ID,))
            n_tf = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM efm_fusion_candidates WHERE run_id=%s", (RUN_ID,))
            n_fc = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM efm_repair_decisions WHERE run_id=%s", (RUN_ID,))
            n_rd = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM efm_delivery_finals WHERE run_id=%s", (RUN_ID,))
            n_df = cur.fetchone()[0]
        check(n_stage >= 1 and n_model >= 1, f"dim tables populated (stage={n_stage}, model={n_model})")
        check(n_tf == 1 and n_fc == 1 and n_rd == 1 and n_df == 1,
              f"child rows written (tf={n_tf} fc={n_fc} rd={n_rd} df={n_df})")

        # 6. 3NF invariant: no target_date column on run-children
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM efm_predictions LIKE 'target_date'")
            has_td = cur.fetchone()
        check(has_td is None, "efm_predictions has NO redundant target_date column (3NF)")

    finally:
        # cleanup the smoke run (CASCADE removes children)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM efm_runs WHERE run_id=%s", (RUN_ID,))
        conn.commit()
        conn.close()

    if failures:
        print(f"\nSMOKE FAILED: {len(failures)} issue(s)")
        return 1
    print("\nSMOKE PASSED: 3NF ledger data-access layer OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
