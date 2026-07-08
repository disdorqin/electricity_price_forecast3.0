# Ops Console Safety

The Ops Console (backend `/api/ops/*` + frontend `OpsConsole`) is the only path
through which the platform **triggers** pipeline work. Every other endpoint is
read-only. This document specifies the safety model.

## Which operations are dangerous

| Operation            | Danger | Confirm required |
| -------------------- | ------ | ---------------- |
| `init-db`            | low    | no               |
| `update-data`        | low    | no               |
| `run-dry-run`        | none   | no               |
| `run-shadow-monitoring` | none | no            |
| `export-submission`  | **HIGH** (writes submission file) | **YES** |
| `run-formal`         | **HIGH** (production delivery)    | **YES** |

`export-submission` and `run-formal` are `DANGEROUS_ACTIONS` and **require
`confirm=true`** both in the router (`assert_confirm`) and again in
`ops_service.run_action`. They can never be triggered silently.

## Why production/export must require confirm

- `run-formal` writes to the production ledger and produces a delivery report.
- `export-submission` writes `submission_ready.csv` — the artifact that is actually
  submitted to the market.
- A single accidental click (or a stray scripted request) could publish a price.
  The explicit `confirm=true` + a second on-screen confirmation in the UI makes this
  a deliberate act, not a side effect.

## Guard layers (defense in depth)

1. **API key / localhost** — `security.require_access`: if `EFM3_API_KEY` is set,
   non-localhost callers must present `X-API-Key`. The frontend dev server runs on
   localhost, so local use works without a key.
2. **Ops enablement** — `security.require_ops`: ops endpoints are disabled for
   non-localhost unless `EFM3_OPS_ENABLED=true`.
3. **Confirm** — `assert_confirm(action, confirm)` rejects `confirm=false` for
   dangerous actions (HTTP 400).
4. **Command whitelist** — `utils/subprocess_runner.py` only ever constructs argv
   from `ALLOWED_ACTIONS`. Unknown actions raise `ValueError`; `shell=False` is
   mandatory; a hard `EFM3_OPS_TIMEOUT` bounds every run. **No arbitrary shell
   command can be executed.**
5. **Run lock** — concurrent `formal` runs for the same `target_date` are rejected
   in `ops_service` (per-date lock).
6. **Password redaction** — the DB URL (with password) is redacted in all logs and
   never returned by any endpoint.

## Local startup

```bash
# backend
set EFM3_DB_URL=mysql+pymysql://root:***@127.0.0.1:3306/efm3
uvicorn backend.app.main:app --reload --port 8000

# frontend
cd frontend && npm install && npm run dev   # http://localhost:5173
```

From localhost the Ops Console is usable immediately. To expose ops to a remote
host, set `EFM3_API_KEY` and `EFM3_OPS_ENABLED=true` and send `X-API-Key` on
requests.
