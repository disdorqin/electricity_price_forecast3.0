## Summary

Production Circuit **Gap Audit + DB Redesign + Chain Reconstruction** for EFM3
(`electricity_price_forecast3.0`). This PR delivers a runnable, observable
production circuit (DB Ledger V2) that mirrors the verified 2.5 topology, while
preserving the honest-status contract: a missing real-time model yields
`PARTIAL` / `NEEDS_MODEL_OUTPUT` and a benchmark is **never** reported as a
model metric.

**Smoke result (2026-02-14, `formal_sim`):**
`PRODUCTION_CIRCUIT_SMOKE_RESULT: PARTIAL` → `READY_TO_MIGRATE_2_5_MODEL_OUTPUTS`.
All 18 steps ran; 8/8 postflight checks passed; benchmark metric persisted with
`metric_scope='benchmark'` (da_anchor vs rt_actual = 30.41% SMAPE — explicitly
**not** a model metric and not comparable to 2.5's 14%/23%).

## What's included

- **2.5 reverse-engineering** (`docs/architecture/EFM25_PRODUCTION_CIRCUIT_REVERSE_ENGINEERING.md`): code-verified model lists, BGEW dynamic weights, repair rule, preservation matrix.
- **Gap audit** (`EFM3_PRODUCTION_CIRCUIT_GAP_AUDIT.md`): AS-IS/TO-BE Mermaid diagrams, Gap Matrix (5 BLOCKERs: G1/G2/G3/G7/G11), Current-Metrics Warning.
- **DB Ledger V2 design** (`EFM3_DB_LEDGER_V2_DESIGN.md`) + **migration 005** (`db/migrations/005_production_circuit_schema.sql`): 8 additive, non-breaking tables.
- **11-module circuit** (`pipelines/production_circuit/`): day-ahead & real-time dual sub-chains (repair → fusion → classifier → task_final), cross-task fusion, separator repair, delivery final, scope-isolated metrics.
- **CLI** `main.py --chain production_circuit` (now routes directly to the circuit, skipping the legacy `ledger_full` pipeline).
- **Scope-isolated metrics** (`tools/db_ops/db_yearly_metrics.py`): benchmark separated from production scopes; production scopes skipped until real model outputs exist.
- **Tests**: 15 (10 new + 5 regression) using a dependency-free fake-MySQL harness; all pass.
- **Smoke report** (`docs/experiments/e2e/PRODUCTION_CIRCUIT_SMOKE_REPORT.md`).

## Bugs fixed during the smoke (all on this branch)

1. **Shared-connection double-close** — `DbConnectionManager.get_connection()` returned a cached singleton; `StepRecorder.record` and chain nodes both closed it → `pymysql "Already closed"`. Added `new_connection()` (always fresh) and routed the package through it. Legacy pipelines unchanged.
2. **`efm_predictions.task` enum constraint** — legacy enum is `dayahead/realtime/fusion/final/shadow`; writing `task='delivery'` truncated. Per 005's non-breaking rule, separator/delivery mirror rows use `task='fusion'` (distinguished by `stage`); `delivery` stays valid only in V2 audit tables.
3. **`efm_metric_runs` has no `id`** — `insert_metric_run`'s `ON DUPLICATE KEY UPDATE id=...` referenced a non-existent column. Removed.
4. **CLI dead-end** — `main.py` always ran `ledger_full` (model training) first, so `--chain production_circuit` was never reached. Now short-circuits to the circuit.

## Honest-status contract (preserved)

- Real-time sub-chain absent → `PARTIAL` / `NEEDS_MODEL_OUTPUT`, never fabricated.
- Day-ahead "final" is a **benchmark** (`da_anchor`); its day-ahead-scope metric is intentionally **not** computed. Only the explicitly-labeled `benchmark` scope metric is persisted.
- `da_anchor` benchmark is **never** reported as a model prediction.
- No shadow is ever selected into the final deliverable.

## Scope guardrails (unchanged capabilities)

No frontend. No data/output/model commits. No password leaks (`.env.local` gitignored). RT916/TimeMixer kept out of the online critical path. Champion model not replaced. No formal submission generated. Shadow never selected into final. PRs #12/#14/#15/#16 capabilities untouched.

## Next steps (to reach `COMPLETE`)

1. Migrate genuine 2.5 **day-ahead** model outputs (`dayahead_raw_model`).
2. Migrate genuine 2.5 **real-time** model outputs (`realtime_raw_model`) so the RT sub-chain populates and `cross_task_fusion` uses the 2.5 delivery policy (RT uses UNCORRECTED fusion).
3. Re-run smoke → expected `PASS` / `READY_FOR_FULL_E2E` with computable production-scope metrics.
