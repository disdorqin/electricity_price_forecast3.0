# Production Circuit — Smoke Run Report

- **Date under test:** 2026-02-14
- **Mode:** `formal_sim`
- **Command:**
  ```bash
  python main.py --use-db --chain production_circuit \
      --date 2026-02-14 --mode formal_sim --db-url "$EFM3_DB_URL"
  ```
- **Run id:** `efm3_pc_20260214_1cb15b69_20260709T042137`
- **Runtime:** ~4.4 s (18 pipeline steps)
- **Result:** ✅ **`PRODUCTION_CIRCUIT_SMOKE_RESULT: PARTIAL`**
- **Recommendation:** ✅ **`READY_TO_MIGRATE_2_5_MODEL_OUTPUTS`**

This is the **expected, honest** outcome for the current skeleton: the day-ahead
sub-chain is wired but backed only by the `da_anchor` **benchmark** (no 2.5 model
output migrated yet), and the real-time sub-chain is **absent / NEEDS_MODEL_OUTPUT**.
The circuit is fully observable end-to-end; the metric layer refuses to report a
benchmark as a model metric.

---

## 1. Step-by-step status

| # | Step | Status | Note |
|---|------|--------|------|
| 1 | data_update | COMPLETE | source rows already in ledger via PMOS backfill |
| 2 | feature_snapshot | COMPLETE | deferred to historical ledger |
| 3 | dayahead_chain | COMPLETE | wrote `benchmark_da_anchor` (NOT a model) |
| 4 | dayahead_repair | COMPLETE | module repair over 24h |
| 5 | dayahead_fusion | COMPLETE | single-candidate fusion (weight 1.0) |
| 6 | dayahead_classifier | COMPLETE | pass-through (DA classifier is model-only) |
| 7 | dayahead_task_final | COMPLETE | 24 `efm_task_finals` |
| 8 | realtime_chain | **PARTIAL** | `MISSING_MODEL_OUTPUT` — 0 candidate rows |
| 9 | realtime_repair | SKIPPED | no realtime source |
| 10 | realtime_fusion | SKIPPED | no realtime source |
| 11 | realtime_classifier | SKIPPED | placeholder (RT classifier is model-only) |
| 12 | realtime_task_final | SKIPPED | no realtime final |
| 13 | cross_task_fusion | PARTIAL | dayahead_only_fallback (realtime absent) |
| 14 | separator_repair | COMPLETE | carried all 24h forward (no-op + repaired) |
| 15 | delivery_final | **PARTIAL** | 24 `efm_delivery_finals` selected (benchmark-backed) |
| 16 | postflight | COMPLETE | 8/8 checks passed |
| 17 | metrics | COMPLETE | benchmark scope persisted; production scopes skipped |
| 18 | finish_run | COMPLETE | overall=PARTIAL |

---

## 2. Database persistence verification (this run)

| Table | Rows | Notes |
|-------|------|-------|
| `efm_pipeline_steps` | 18 | one per executed step |
| `efm_predictions` | 192 | 8 stages × 24h (benchmark/repaired/fused/classifier/task_final/cross_fusion/separator/delivery) |
| `efm_predictions` (`is_selected=TRUE`) | 24 | the delivery final (the selected deliverable) |
| `efm_delivery_finals` | 24 | authoritative delivery with provenance |
| `efm_task_finals` | 24 | day-ahead finals (realtime absent) |
| `efm_repair_decisions` | 48 | 24 day-ahead + 24 separator (changed **and** no_op logged) |
| `efm_fusion_candidates` | 24 | full fusion audit trail |
| `efm_metric_runs` | 1 | **benchmark** scope only |

**Postflight:** 8/8 checks PASSED (row_count_24, hour_range, no_nan,
no_duplicates, price_range, selected_source, shadow_not_final,
submission_row_count).

**Benchmark metric (clearly labeled, NOT a model metric):**

| metric_scope | smape | mae | evaluable_hours |
|--------------|-------|-----|-----------------|
| `benchmark` (da_anchor vs rt_actual) | 30.41% | 76.91 | 24 |

> The 30.41% is the **cross-product spread** (day-ahead clearing price vs
> real-time actual), not model accuracy. It must never be compared to the 2.5
> 14%/23% figures, which are same-product model-vs-settlement metrics. The
> day-ahead / real-time production scopes are intentionally **not computed**
> until real model outputs exist.

---

## 3. Issues found & fixed during the smoke

These were real defects in the circuit code that the smoke surfaced and that
are now fixed on this branch:

1. **Shared-connection double-close.** `DbConnectionManager.get_connection()`
   returns a single cached connection. `StepRecorder.record` and each chain
   node both called `get_connection()` → same object → closing it in one place
   silently invalidated the other's handle (`pymysql.err.Error: Already closed`).
   **Fix:** added `DbConnectionManager.new_connection()` (always returns a fresh,
   independent connection) and routed the entire `production_circuit` package
   through it. Legacy pipelines keep using `get_connection()` (unchanged).

2. **`efm_predictions.task` enum constraint.** The legacy `efm_predictions.task`
   enum is `dayahead/realtime/fusion/final/shadow`; the V2 `efm_prediction_batches.task`
   enum is `dayahead/realtime/fusion/delivery`. Writing `task='delivery'` truncated.
   Per the 005 migration's non-breaking rule (never ALTER existing tables), the
   `separator_repaired` and `delivery_final` **mirror** rows now use `task='fusion'`
   (distinguished by `stage`); `delivery` remains valid only inside the V2 audit
   tables (`efm_pipeline_steps`, `efm_prediction_batches`, `efm_repair_decisions`),
   which already allow it.

3. **`efm_metric_runs` has no `id` column.** `insert_metric_run`'s
   `ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)` referenced a non-existent
   column (`efm_metric_runs` PK is `metric_run_id`). Removed the `id` reference.

4. **CLI routing dead-end.** `main.py` always ran the legacy `ledger_full`
   pipeline first (which trains/predicts models and would hang), so
   `--chain production_circuit` was never reached. **Fix:** `main()` now
   short-circuits to `run_production_circuit` when `--chain production_circuit`
   is given with `--use-db`, returning before the legacy pipeline.

---

## 4. Honest-status contract (preserved)

- Real-time sub-chain absent → `PARTIAL` / `NEEDS_MODEL_OUTPUT`, never fabricated.
- Day-ahead "final" is a **benchmark** → its day-ahead-scope metric is NOT
  computed (would be misleading); only the explicitly-labeled `benchmark` scope
  metric is persisted.
- `da_anchor` benchmark is **never** reported as a model prediction.
- No shadow is ever selected into the final deliverable.

---

## 5. Next steps (to reach `COMPLETE`)

1. Migrate genuine **2.5 day-ahead model outputs** into the ledger
   (`dayahead_raw_model` stage) so `run_day_ahead_chain` loads them instead of
   falling back to the benchmark.
2. Migrate genuine **2.5 real-time model outputs** (`realtime_raw_model`) so the
   real-time sub-chain populates and `cross_task_fusion` uses the 2.5 delivery
   policy (RT uses UNCORRECTED fusion).
3. Once both exist, re-run this smoke; expected result flips to
   `PRODUCTION_CIRCUIT_SMOKE_RESULT: PASS` and `READY_FOR_FULL_E2E`, and the
   day-ahead / real-time production-scope metrics become computable.
