# EFM3 Backend API Design

The backend is the **control plane** for the EFM3 Forecast Ledger. It connects to
the MySQL ledger (`EFM3_DB_URL`), exposes read APIs for the dashboard, and triggers
whitelisted pipeline operations. It does **not** re-implement forecasting — it calls
the existing `main.py` / orchestrator through a single audited child process
(`backend/app/ops_dispatch.py`).

## How the backend connects to the DB

- `app/db.py` yields a `pymysql` connection from `common.db.connection.DbConnectionManager`.
- If `EFM3_DB_URL` is empty, every data endpoint degrades gracefully to HTTP `503`
  (`Database not configured`); health endpoints always work.
- All queries are **parameterized** (`services/base.py`); no user input is concatenated
  into SQL.
- The password is read from the environment only and is never emitted.

## How the backend triggers a dry-run

A dry-run is triggered by `POST /api/ops/run-dry-run` with `{ "target_date": "YYYY-MM-DD" }`.
The flow:

1. `routers/ops.py` validates the request and the `require_ops` guard.
2. `services/ops_service.run_action("run-dry-run", params)` builds a **fixed argv**
   via `utils/subprocess_runner.build_ops_command` (never a shell string).
3. `ops_dispatch.py` (the only spawned script) maps the action to
   `pipelines.full_chain_orchestrator.run_full_chain(target_date=..., mode="dry_run",
   use_db=True, export_submission=False, export_report=False)`.
4. The subprocess runs with `shell=False` and a hard timeout; its JSON result is
   returned to the caller.

Because `mode="dry_run"`, no `final/submission_ready.csv` is ever written.

## Endpoint reference (read-only)

### Health
- `GET /api/health` — backend status, `db_configured`, `ops_enabled`.
- `GET /api/health/db` — DB connectivity (`ok` / `not_configured` / `error`).
- `GET /api/health/schema` — table list.

### Runs
- `GET /api/runs` — recent runs (limit/mode).
- `GET /api/runs/{run_id}` — raw run row.
- `GET /api/runs/{run_id}/summary` — run summary.
- `GET /api/runs/{run_id}/events` — run events.
- `GET /api/runs/{run_id}/postflight` — postflight checks.
- `GET /api/runs/{run_id}/delivery-outputs` — delivery outputs.

### Predictions
- `GET /api/runs/{run_id}/predictions` — all predictions (filter `task`/`stage`/`selected_only`).
- `GET /api/runs/{run_id}/predictions/hourly` — hourly view.
- `GET /api/runs/{run_id}/predictions/selected` — selected final.
- `GET /api/runs/{run_id}/predictions/compare?models=da_anchor,official_baseline,seasonal_da_router` — chart series.

### Datasets / Data sources
- `GET /api/datasets`, `/api/datasets/{dataset_id}`, `/api/datasets/latest?target_date=`
- `GET /api/data-sources`, `/api/source-files`, `/api/data-update-runs`
- `GET /api/datasets/readiness` — wraps `v_efm_dataset_readiness`.

### Lineage (see FORECAST_LEDGER_LINEAGE.md)
- `GET /api/lineage/{run_id}` — per-hour router summary.
- `GET /api/lineage/{run_id}/hour/{hour_business}` — full chain graph.

### Reports
- `GET /api/reports/latest`, `/api/reports/run/{run_id}`
- `GET /api/reports/shadow-safety` — `v_efm_shadow_safety` (or computed fallback).
- `GET /api/reports/db-health` — table inventory.

## Dashboard views

`db/migrations/003_dashboard_views.sql` adds 8 read-only views used by the read APIs
and the frontend charts:

`v_efm_latest_runs`, `v_efm_run_prediction_counts`, `v_efm_selected_predictions`,
`v_efm_shadow_safety`, `v_efm_dataset_readiness`, `v_efm_postflight_summary`,
`v_efm_delivery_summary`, `v_efm_hourly_prediction_compare`.
