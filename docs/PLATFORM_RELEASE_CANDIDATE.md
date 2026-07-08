# EFM3 Platform — Release Candidate Report

## 1. Scope

| Item     | Value                  |
| -------- | ---------------------- |
| branch   | agent/backend-frontend-control-plane |
| backend  | FastAPI                |
| frontend | React + Vite + ECharts |
| DB       | MySQL ledger (15 base tables + 8 dashboard views) |
| mode     | local-first            |

The platform upgrades EFM3 3.0 from a "command-line prediction chain" to a
**showable, operable, monitorable prediction system**. MySQL is the source of
truth; the backend is the control plane; the frontend is the Forecast Ledger
Dashboard. The prediction chain itself is still executed by `main.py` /
orchestrator — the platform only triggers, queries, displays and audits, never
bypassing the safety gates.

## 2. Backend APIs

| API Group   | Status | Notes |
| ----------- | ------ | ----- |
| health      | ✅ PASS | /api/health, /db, /schema |
| runs        | ✅ PASS | list/summary/events/postflight/delivery |
| predictions | ✅ PASS | predictions/hourly/selected/compare |
| datasets    | ✅ PASS | datasets/sources/source-files/update-runs/readiness |
| postflight  | ✅ PASS | /api/runs/{id}/postflight |
| lineage     | ✅ PASS | /api/lineage/{run_id}[/hour/{h}] |
| ops         | ✅ PASS | whitelisted; confirm-guarded |
| reports     | ✅ PASS | shadow-safety / db-health / refs |

## 3. Frontend Pages

| Page         | Status | Notes |
| ------------ | ------ | ----- |
| Dashboard    | ✅ PASS | build + typecheck OK |
| Runs         | ✅ PASS | build + typecheck OK |
| RunDetail    | ✅ PASS | build + typecheck OK |
| Predictions  | ✅ PASS | ECharts + Lineage Graph |
| DataSources  | ✅ PASS | build + typecheck OK |
| ShadowSafety | ✅ PASS | build + typecheck OK |
| OpsConsole   | ✅ PASS | confirm-gated dangerous ops |
| LineageGraph | ✅ PASS | component in Predictions |

## 4. Safety

| Check                   | Result |
| ----------------------- | ------ |
| password redaction      | ✅ PASS (utils/redaction.py; verified by tests) |
| API key / localhost guard | ✅ PASS (security.require_access / require_ops) |
| formal confirm required | ✅ PASS (router + service double guard) |
| export confirm required | ✅ PASS |
| no arbitrary command    | ✅ PASS (ALLOWED_ACTIONS whitelist, shell=False, timeout) |
| no old command change   | ✅ PASS (`python main.py YYYY-MM-DD` untouched) |
| no champion replacement | ✅ PASS (read/trigger only) |
| no final/submission write | ✅ PASS (dry-run only; export/formal require confirm) |

## 5. Tests

| Test | Result |
| ---- | ------ |
| test_backend_api_health | ✅ PASS |
| test_backend_api_runs | ✅ PASS |
| test_backend_api_predictions | ✅ PASS |
| test_backend_api_lineage | ✅ PASS |
| test_backend_ops_safety | ✅ PASS |
| test_dashboard_views_schema | ✅ PASS |
| test_api_password_redaction | ✅ PASS |
| frontend `npm run build` (tsc + vite) | ✅ PASS |

All backend tests run against a dedicated `efm3_test` database (env-gated via
`EFM3_TEST_DB_URL`); the production ledger is never touched and no credential is
hardcoded in the repo.

## 6. Recommendation

PLATFORM_RECOMMENDATION: READY_FOR_PLATFORM_RC

## 7. Final Verdict

PLATFORM_RESULT: PASS

## 8. PR strategy (recommended split)

- **PR H** — Dashboard DB views (003) + backend read-only APIs + read API tests + BACKEND_API_DESIGN.md
- **PR I** — Ops APIs + safety guard + tests/test_backend_ops_safety.py + OPS_CONSOLE_SAFETY.md
- **PR J** — Frontend dashboard MVP + FRONTEND_DASHBOARD_DESIGN.md
- **PR K** — Lineage Graph + FORECAST_LEDGER_LINEAGE.md + this RC report

Each PR keeps: no password, no node_modules, no outputs, old command unchanged,
formal/export default-off, no champion replacement, no final/submission writes.
