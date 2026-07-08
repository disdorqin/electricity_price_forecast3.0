# EFM3 Control Plane (Backend)

FastAPI backend for the EFM3 Forecast Ledger Platform. It is the **control plane**:
it queries, displays, audits and triggers the prediction pipeline. It never
bypasses the safety gates enforced by `main.py` / the orchestrator.

## Stack

- FastAPI + Uvicorn
- Pydantic / pydantic-settings
- SQLAlchemy-free; reuses `common.db` (pymysql) repositories
- ECharts is on the frontend only

## Install

```bash
pip install -r requirements.txt
```

(recommended: a dedicated virtualenv, e.g. `backend/.venv`)

## Configure

All configuration is via environment variables (nothing is hardcoded):

| Variable           | Meaning                                                        | Default |
| ------------------ | ------------------------------------------------------------- | ------- |
| `EFM3_DB_URL`      | MySQL ledger URL `mysql+pymysql://user:pass@host:3306/efm3`   | (empty) |
| `EFM3_API_KEY`     | Optional API key required for non-localhost access           | (empty) |
| `EFM3_OPS_ENABLED` | Enable ops endpoints for non-localhost callers               | `false` |
| `EFM3_CORS_ORIGINS`| Comma-separated allowed CORS origins                         | localhost:5173/3000 |
| `EFM3_OPS_TIMEOUT` | Hard subprocess timeout (s) for triggered pipelines          | `600`   |
| `EFM3_APP_ENV`     | Label (`local` / `prod`)                                     | `local` |

> The DB password is **never** returned by any endpoint and is **redacted** from
> all logs. `.env.local` is gitignored.

## Run

```bash
set EFM3_DB_URL=mysql+pymysql://root:***@127.0.0.1:3306/efm3
uvicorn backend.app.main:app --reload --port 8000
# docs at http://127.0.0.1:8000/docs
```

## API groups

| Group      | Prefix             | Notes                                          |
| ---------- | ------------------ | ---------------------------------------------- |
| health     | `/api/health`      | backend / db / schema health                   |
| runs       | `/api/runs`        | list, summary, events, postflight, delivery    |
| predictions| `/api/runs/{id}`   | predictions, hourly, selected, compare         |
| datasets   | `/api/datasets`    | datasets, sources, source-files, update-runs  |
| lineage    | `/api/lineage`     | run + per-hour prediction lineage graph        |
| reports    | `/api/reports`     | shadow-safety, db-health, report refs         |
| ops        | `/api/ops`         | init-db / update-data / dry-run / etc.        |

## Safety model

1. **Password redaction** — `utils/redaction.py` scrubs the URL in logs/responses.
2. **No password in API** — no endpoint ever serializes the DB password.
3. **Ops default off / local-only** — `security.require_ops` blocks non-localhost
   unless `EFM3_OPS_ENABLED=true`.
4. **Confirm guard** — `export-submission` and `run-formal` require `confirm=true`
   (enforced both in the router and in `ops_service`).
5. **CORS localhost-only** by default.
6. **Command whitelist** — `utils/subprocess_runner.py` only ever builds argv for
   `ALLOWED_ACTIONS`; `shell=False` and a hard timeout are always used. No arbitrary
   shell command can be executed.
7. **Run lock** — concurrent `formal` runs for the same `target_date` are rejected.

See `docs/BACKEND_API_DESIGN.md`, `docs/OPS_CONSOLE_SAFETY.md`, and
`docs/FORECAST_LEDGER_LINEAGE.md` for details.
