"""
New tests for the EFM3 Production Circuit (DB Ledger V2).

These run ENTIRELY against ``pc_fake_db`` (no live MySQL). They verify that the
circuit nodes (a) land rows in the correct V2 tables, (b) honour the honest
status contract (no fabrication of realtime / benchmark-as-model), and (c) the
metric-scope semantics isolate benchmark from production metrics.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pc_fake_db import FakeDbManager, make_ctx  # noqa: E402

from pipelines.production_circuit.contracts import (  # noqa: E402
    CircuitStage,
    CircuitTask,
    StepStatus,
    STAGE_TO_TASK,
)
from pipelines.production_circuit.step_recorder import (  # noqa: E402
    StepRecorder,
    insert_metric_run,
    write_stage_predictions,
)
from pipelines.production_circuit.repair_chain import run_repair  # noqa: E402
from pipelines.production_circuit.fusion_chain import run_fusion  # noqa: E402
from pipelines.production_circuit.classifier_chain import run_classifier  # noqa: E402
from pipelines.production_circuit.separator_chain import run_separator_repair  # noqa: E402
from pipelines.production_circuit.dayahead_chain import (  # noqa: E402
    run_day_ahead_chain,
    run_day_ahead_task_final,
)
from pipelines.production_circuit.realtime_chain import (  # noqa: E402
    run_real_time_chain,
    run_real_time_task_final,
)
from pipelines.production_circuit.delivery_chain import (  # noqa: E402
    run_cross_task_fusion,
    run_delivery_final,
)
import tools.db_ops.db_yearly_metrics as mym  # noqa: E402


RUN_ID = "efm3_pc_test"
TARGET = "2026-02-14"


def _seed_stage(mgr, task, stage, n=24, base=300.0, fmt="{h}", run_id=RUN_ID):
    rows = [{
        "hour_business": h,
        "pred_price": float(base + h),
        "model_name": fmt.format(h=h),
        "model_version": "v1",
        "is_shadow": False, "is_selected": False,
        "selected_reason": None, "quality_flags": None,
    } for h in range(1, n + 1)]
    write_stage_predictions(mgr.get_connection(), run_id, TARGET, task, stage, rows)
    return rows


def _seed_actuals(mgr, n=24, da=300.0, rt=350.0):
    mgr.seed_actual_prices(TARGET, [(h, da + h, rt + h) for h in range(1, n + 1)])


# ── T1: contract mapping ─────────────────────────────────────────────────
def test_contracts_stage_task_mapping():
    assert STAGE_TO_TASK[CircuitStage.DAYAHEAD_TASK_FINAL] == CircuitTask.DAYAHEAD
    assert STAGE_TO_TASK[CircuitStage.REALTIME_TASK_FINAL] == CircuitTask.REALTIME
    assert STAGE_TO_TASK[CircuitStage.CROSS_TASK_FUSION] == CircuitTask.FUSION
    assert STAGE_TO_TASK[CircuitStage.DELIVERY_FINAL] == CircuitTask.DELIVERY
    assert STAGE_TO_TASK[CircuitStage.BENCHMARK_DA_ANCHOR] == CircuitTask.DAYAHEAD
    assert StepStatus.NEEDS_MODEL_OUTPUT.value == "NEEDS_MODEL_OUTPUT"
    assert "benchmark_da_anchor" in [s.value for s in CircuitStage]


# ── T2: recorder round-trip ──────────────────────────────────────────────
def test_recorder_roundtrip():
    mgr, _ = make_ctx(RUN_ID, TARGET)
    rec = StepRecorder(mgr)
    rec.record(RUN_ID, TARGET, "dayahead", "step_x", 1, "COMPLETE",
               input_count=3, output_count=2)
    assert mgr.count_rows("efm_pipeline_steps") == 1

    rows = _seed_stage(mgr, CircuitTask.DAYAHEAD, CircuitStage.DAYAHEAD_FUSED)
    conn = mgr.get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM efm_predictions WHERE run_id=%s AND stage=%s",
        (RUN_ID, "dayahead_fused"))
    assert cur.fetchone()[0] == 24
    assert mgr.count_rows("efm_prediction_batches") == 1


# ── T3: repair carries all hours forward + clamps out-of-range ───────────
def test_repair_chain():
    mgr, ctx = make_ctx(RUN_ID, TARGET)
    rows = [{
        "hour_business": h,
        "pred_price": (5000.0 if h == 3 else 300.0),
        "model_name": "da_anchor_benchmark", "model_version": "v",
        "is_shadow": False, "is_selected": False,
        "selected_reason": None, "quality_flags": None,
    } for h in range(1, 25)]
    write_stage_predictions(mgr.get_connection(), RUN_ID, TARGET,
                            CircuitTask.DAYAHEAD, CircuitStage.BENCHMARK_DA_ANCHOR, rows)

    res = run_repair(ctx, CircuitTask.DAYAHEAD, CircuitStage.BENCHMARK_DA_ANCHOR,
                     CircuitStage.DAYAHEAD_MODULE_REPAIRED, 4, "dayahead_repair")
    assert res.status == StepStatus.COMPLETE

    conn = mgr.get_connection(); cur = conn.cursor()
    cur.execute(
        "SELECT hour_business, pred_price FROM efm_predictions "
        "WHERE run_id=%s AND target_date=%s AND task=%s AND stage=%s "
        "ORDER BY hour_business",
        (RUN_ID, TARGET, "dayahead", "dayahead_module_repaired"))
    got = {int(hb): float(p) for hb, p in cur.fetchall()}
    assert len(got) == 24                 # all hours carried forward
    assert got[3] == 2000.0                # range_guard clamped 5000 -> MAX_PRICE
    assert got[1] == 300.0                 # within bounds untouched
    # every hour logged as a decision (1 changed + 23 no_op)
    assert mgr.count_rows("efm_repair_decisions") == 24


# ── T4: fusion records candidates + single-candidate weight 1.0 ─────────
def test_fusion_chain():
    mgr, ctx = make_ctx(RUN_ID, TARGET)
    _seed_stage(mgr, CircuitTask.DAYAHEAD, CircuitStage.DAYAHEAD_MODULE_REPAIRED)
    res = run_fusion(ctx, CircuitTask.DAYAHEAD,
                     CircuitStage.DAYAHEAD_MODULE_REPAIRED,
                     CircuitStage.DAYAHEAD_FUSED, 5, "dayahead_fusion")
    assert res.status == StepStatus.COMPLETE
    assert mgr.count_rows("efm_fusion_candidates") == 24
    # One candidate per hour => per-hour weight normalises to 1.0 (single
    # candidate fusion). All candidates selected.
    conn = mgr.get_connection(); cur = conn.cursor()
    cur.execute("SELECT weight_value, selected FROM efm_fusion_candidates LIMIT 1")
    w, sel = cur.fetchone()
    assert abs(float(w) - 1.0) < 1e-9 and sel == 1
    assert mgr.count_rows("efm_predictions") >= 24  # fused stage written

    # SKIPPED when no source rows
    mgr2, ctx2 = make_ctx("r_empty", TARGET)
    res2 = run_fusion(ctx2, CircuitTask.REALTIME,
                      CircuitStage.REALTIME_MODULE_REPAIRED,
                      CircuitStage.REALTIME_FUSED, 10, "realtime_fusion")
    assert res2.status == StepStatus.SKIPPED


# ── T5: classifier day-ahead pass-through / realtime placeholder ────────
def test_classifier_chain():
    mgr, ctx = make_ctx(RUN_ID, TARGET)
    _seed_stage(mgr, CircuitTask.DAYAHEAD, CircuitStage.DAYAHEAD_FUSED)
    res = run_classifier(ctx, CircuitTask.DAYAHEAD, CircuitStage.DAYAHEAD_FUSED,
                         CircuitStage.DAYAHEAD_CLASSIFIER_ADJUSTED, 6,
                         "dayahead_classifier", is_placeholder=False)
    assert res.status == StepStatus.COMPLETE
    conn = mgr.get_connection(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM efm_predictions WHERE stage=%s",
                ("dayahead_classifier_adjusted",))
    assert cur.fetchone()[0] == 24

    # realtime placeholder => SKIPPED (NOT a real classifier)
    res2 = run_classifier(ctx, CircuitTask.REALTIME, CircuitStage.REALTIME_FUSED,
                          CircuitStage.REALTIME_CLASSIFIER_ADJUSTED, 11,
                          "realtime_classifier", is_placeholder=True)
    assert res2.status == StepStatus.SKIPPED

    # SKIPPED when nothing to classify
    mgr2, ctx2 = make_ctx("r2", TARGET)
    res3 = run_classifier(ctx2, CircuitTask.DAYAHEAD, CircuitStage.DAYAHEAD_FUSED,
                          CircuitStage.DAYAHEAD_CLASSIFIER_ADJUSTED, 6,
                          "dayahead_classifier", is_placeholder=False)
    assert res3.status == StepStatus.SKIPPED


# ── T6: separator repair on cross-task fusion ───────────────────────────
def test_separator_chain():
    mgr, ctx = make_ctx(RUN_ID, TARGET)
    _seed_stage(mgr, CircuitTask.FUSION, CircuitStage.CROSS_TASK_FUSION)
    res = run_separator_repair(ctx)
    assert res.status == StepStatus.COMPLETE
    conn = mgr.get_connection(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM efm_predictions WHERE stage=%s",
                ("separator_repaired",))
    assert cur.fetchone()[0] == 24

    # SKIPPED when no cross_task_fusion
    mgr2, ctx2 = make_ctx("r_sep", TARGET)
    res2 = run_separator_repair(ctx2)
    assert res2.status == StepStatus.SKIPPED


# ── T7: day-ahead chain honest default = NEEDS_MODEL_OUTPUT (no da_anchor) ─
def test_dayahead_chain_no_models_needs_output():
    mgr, ctx = make_ctx(RUN_ID, TARGET)
    # Default config: benchmark fallback DISABLED -> stale da_anchor can
    # NEVER leak in as a fake day-ahead model.
    res = run_day_ahead_chain(ctx)
    assert res.status == StepStatus.NEEDS_MODEL_OUTPUT
    assert res.artifacts["model_available"] is False
    assert res.artifacts["stage"] is None
    conn = mgr.get_connection(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM efm_predictions WHERE task=%s", ("dayahead",))
    assert cur.fetchone()[0] == 0


def test_dayahead_chain_benchmark_optin():
    mgr, ctx = make_ctx(RUN_ID, TARGET)
    ctx.config = {"allow_benchmark_fallback": True}  # explicit opt-in only
    _seed_actuals(mgr, da=300.0)
    res = run_day_ahead_chain(ctx)
    assert res.status == StepStatus.COMPLETE
    assert res.artifacts["model_available"] is False
    assert res.artifacts["stage"] == "benchmark_da_anchor"
    conn = mgr.get_connection(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM efm_predictions WHERE stage=%s",
                ("benchmark_da_anchor",))
    assert cur.fetchone()[0] == 24


# ── T8: real-time chain NEVER fabricates (PARTIAL / NEEDS_MODEL_OUTPUT) ──
def test_realtime_chain_honest():
    mgr, ctx = make_ctx(RUN_ID, TARGET)
    res = run_real_time_chain(ctx)
    assert res.status == StepStatus.PARTIAL
    assert res.artifacts["model_available"] is False
    # zero realtime rows written
    conn = mgr.get_connection(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM efm_predictions WHERE task=%s", ("realtime",))
    assert cur.fetchone()[0] == 0
    # realtime task final is ABSENT
    res2 = run_real_time_task_final(ctx)
    assert res2.status == StepStatus.SKIPPED
    assert res2.artifacts["realtime_final_present"] is False


# ── T9: full day-ahead tail with OUR 3 models -> multi-model fusion ─────
RAW_RUN = "efm3_raw_2026-02-14_dayahead"


def test_dayahead_task_final_separated():
    mgr, ctx = make_ctx(RUN_ID, TARGET)
    # Simulate the external P1 engine + ingest_model_predictions.py: our 3
    # day-ahead models land as dayahead_raw_model under a SEPARATE raw run_id.
    for mname, base in [("cfg05", 300.0), ("xgboost_rich", 310.0), ("catboost_rich", 320.0)]:
        rows = [{
            "hour_business": h, "pred_price": float(base + h),
            "model_name": mname, "model_version": "v1",
            "is_shadow": False, "is_selected": False,
            "selected_reason": None, "quality_flags": None,
        } for h in range(1, 25)]
        write_stage_predictions(mgr.get_connection(), RAW_RUN, TARGET,
                                CircuitTask.DAYAHEAD, CircuitStage.DAYAHEAD_RAW_MODEL, rows)
    res = run_day_ahead_chain(ctx)
    assert res.status == StepStatus.COMPLETE
    assert res.artifacts["model_available"] is True
    run_repair(ctx, CircuitTask.DAYAHEAD, CircuitStage.DAYAHEAD_RAW_MODEL,
               CircuitStage.DAYAHEAD_MODULE_REPAIRED, 4, "dayahead_repair")
    res_f = run_fusion(ctx, CircuitTask.DAYAHEAD, CircuitStage.DAYAHEAD_MODULE_REPAIRED,
               CircuitStage.DAYAHEAD_FUSED, 5, "dayahead_fusion")
    assert "multi_model" in res_f.artifacts["mode"]
    run_classifier(ctx, CircuitTask.DAYAHEAD, CircuitStage.DAYAHEAD_FUSED,
                   CircuitStage.DAYAHEAD_CLASSIFIER_ADJUSTED, 6,
                   "dayahead_classifier", is_placeholder=False)
    res_tf = run_day_ahead_task_final(ctx)
    assert res_tf.status == StepStatus.COMPLETE
    conn = mgr.get_connection(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM efm_task_finals WHERE task=%s", ("dayahead",))
    assert cur.fetchone()[0] == 24
    cur.execute("SELECT COUNT(*) FROM efm_predictions WHERE stage=%s",
                ("dayahead_task_final",))
    assert cur.fetchone()[0] == 24
    # 3 models x 24 hours = 72 fusion candidates recorded
    assert mgr.count_rows("efm_fusion_candidates") == 72


# ── T10: metric scope isolation (benchmark vs production) ────────────────
def test_metric_scope_isolation():
    # (a) dayahead scope with NO real model final -> UNCLEAR (never fabricated)
    mgr, _ = make_ctx(RUN_ID, TARGET)
    cur = mgr.get_connection().cursor()
    res = mym.run_scope_metric(cur, RUN_ID, TARGET, "dayahead", floor50=True)
    assert res["result"] == "UNCLEAR"
    assert "NEEDS_MODEL_OUTPUT" in res["reason"]

    # (b) benchmark scope (da_anchor vs rt_actual) computes floor50 and persists
    mgr2, _ = make_ctx("r_bm", TARGET)
    _seed_actuals(mgr2, da=300.0, rt=350.0)
    _seed_stage(mgr2, CircuitTask.DAYAHEAD, CircuitStage.BENCHMARK_DA_ANCHOR,
                base=300.0, run_id="r_bm")
    cur2 = mgr2.get_connection().cursor()
    res2 = mym.run_scope_metric(cur2, "r_bm", TARGET, "benchmark", floor50=True)
    assert res2["result"] == "OK"
    assert "smape" in res2 and res2["smape"] is not None
    # persisted to efm_metric_runs with explicit benchmark scope
    c = mgr2.get_connection().cursor()
    c.execute("SELECT metric_scope, pred_stage, actual_source FROM efm_metric_runs")
    sc, ps, ac = c.fetchone()
    assert sc == "benchmark" and ps == "benchmark_da_anchor" and ac == "rt_actual"
