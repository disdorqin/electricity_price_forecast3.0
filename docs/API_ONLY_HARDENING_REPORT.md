# EFM3 API-only Control Plane & Config Hardening Report

**Branch:** `agent/api-only-control-plane-config-hardening`
**PR:** [#14 — EFM3 API-only Control Plane, Local Config, and Fallback Hardening](https://github.com/disdorqin/electricity_price_forecast3.0/pull/14)
**Base:** `main` (post PR #12 merge, commit `8d3ffe2`)
**Date:** 2026-07-08

---

## 1. Scope

| Item | Status | Notes |
|---|---|---|
| API-only backend control plane (from PR #13) | ✅ Extracted | `backend/` only — no frontend / npm / node |
| Old CLI commands unchanged | ✅ Verified | conda `epf-2` legacy suite: 241 passed |
| One-click full chain | ✅ Kept | `pipelines/full_chain_orchestrator.py` guarded, not altered in behavior |
| Auto DB storage of predictions | ✅ Implemented | `PredictionStore` → MySQL contract + tests |
| Local Docker MySQL config | ✅ Encapsulated | `.env*`, `configs/local.*.yaml`, `docker-compose.mysql.yml`, `scripts/bootstrap_local_db.py` |
| dry_run / formal / shadow fallback rules | ✅ Explicit | `common/fallback_policy.py` + `docs/CHAIN_FALLBACK_MATRIX.md` |
| No DB password leakage | ✅ Verified | only `***` / `YOUR_PASSWORD` placeholders committed |
| **Frontend (React)** | ⛔ Excluded | PR #13 kept open as full-platform demo; not merged here |
| `daemon/`, `exports/`, unrelated top-level files | ⛔ Excluded | not staged / not in PR |

---

## 2. API Contract (§三)

OpenAPI 3.0 generated via `scripts/export_openapi.py` → `docs/api/openapi.json` (76 KB) + `docs/api/API_CONTRACT.md`.

| Router prefix | Purpose | Key endpoints |
|---|---|---|
| `/health` | Liveness/readiness | `GET /health` |
| `/runs` | Run registry + status | `GET /runs`, `GET /runs/{id}` |
| `/predictions` | Prediction ledger read | `GET /predictions`, `GET /predictions/{run_id}` |
| `/datasets` | Dataset versions | `GET /datasets`, `GET /datasets/{id}` |
| `/lineage` | Forecast ledger lineage | `GET /lineage/run/{id}`, `GET /lineage/hour` |
| `/ops` | Pipeline side-effects (**default OFF**) | `POST /ops/update-data`, `/ops/dry-run`, `/ops/formal`, `/ops/export-submission` |
| `/reports` | Submission-ready / reports | `GET /reports/submission-ready` |
| `/data-sources` | Source registry | `GET /data-sources` |

---

## 3. Local Config (§四)

| File | Role |
|---|---|
| `.env.example` | Committable template; `***` placeholder; documents `#`→`%23` |
| `.env.local.example` | Local override template (`YOUR_PASSWORD` placeholder) |
| `configs/local.mysql.yaml` | `db.url_env: EFM3_DB_URL`, `formal_requires_db`, timeouts, pool, redaction |
| `configs/local.paths.yaml` | `EFM3_DATA_ROOT`, `EFM2_5_ROOT`, output roots |
| `docker-compose.mysql.yml` | MySQL 8.0 service, healthcheck, volume; `MYSQL_ROOT_PASSWORD` from env |
| `scripts/bootstrap_local_db.py` | Spin up Docker MySQL + `--init-db` |
| `scripts/run_local_dry_run.py` | One-click local dry-run against DB |
| `scripts/run_local_shadow.py` | One-click local shadow run |

`.env.local` is git-ignored; no real credentials are committed.

---

## 4. Chain Fallback Matrix (§五)

Implemented in `common/fallback_policy.py` (`FallbackDecision` + per-failure evaluators) and documented in `docs/CHAIN_FALLBACK_MATRIX.md` (10-row matrix).

| Failure | dry_run | formal |
|---|---|---|
| DB unavailable | DEGRADED (file-store fallback, `db_enabled=false`) | **FAIL / exit 1** (no silent delivery) |
| Dataset not ready | Fallback to router / PARTIAL | FAIL (cannot ship blind) |
| Router (winter anchor) missing | Fallback to official baseline | FAIL if no usable anchor |
| Postflight check failed | PARTIAL (flagged) | FAIL |
| Export failed | N/A (skip export) | FAIL (no delivery) |
| Shadow monitoring failed | CONTINUE (non-blocking) | CONTINUE |

`map_to_exit_code()` converts each decision to the correct process exit code.

---

## 5. Auto DB Storage (§六)

All prediction results flow through `PredictionStore` (MySQL impl) — no raw CSV write bypasses the store.

| Guard | Test |
|---|---|
| Every prediction path uses the store | `tests/test_all_prediction_paths_use_store.py` |
| No direct CSV write without store | `tests/test_no_direct_prediction_csv_without_store.py` |
| Shadow never selected into export | `test_shadow_never_selected` (FilePredictionStore) |
| Exporter requires store, uses selected only | `test_exporter_signature_requires_store` |

Contract doc: `docs/PREDICTION_STORAGE_CONTRACT.md`.

---

## 6. Ops Safety (§七)

| Rule | Enforcement |
|---|---|
| Ops default-disabled | `require_ops` raises **403** when `EFM3_OPS_ENABLED=false` (no localhost bypass) |
| Formal / export gated | Require `confirm=true` **and** non-empty `reason` (`assert_confirm`) |
| Command whitelist | `subprocess_runner.ALLOWED_ACTIONS` only; `shell=False`; timeout; redacted argv |
| No arbitrary command | Unknown action → rejected before dispatch |

Tests: `test_api_ops_disabled_by_default.py`, `test_api_export_requires_confirm_reason.py`, `test_api_command_whitelist.py`.

---

## 7. Tests (§九)

| Suite | Env | Result |
|---|---|---|
| API / storage / fallback / ops | `backend/.venv` (fastapi, no pandas) | **49 passed, 16 skipped** (DB-gated skip without `EFM3_TEST_DB_URL`) |
| Legacy CLI / DB / pipeline / shadow-registry ("old command unchanged") | conda `epf-2` | **241 passed** |
| Pre-existing, out-of-scope failure | conda `epf-2` | 1 — `test_realtime_lite_candidate_registry::test_no_rt916_or_timemixer_production_candidate` (naive substring flags the selector's own *negative* disclaimer `"Never rely on RT916 or TimeMixer."`; YAML unchanged by this branch, realtime-registry area, not a regression) |

`tests/conftest.py` was hardened with a FastAPI import guard so the same suite loads under **both** interpreters (backend venv and conda `epf-2`).

---

## 8. Recommendation & Result

```
API_ONLY_RECOMMENDATION = READY_FOR_FRONTEND_INTEGRATION
API_ONLY_RESULT         = PASS
```

**Rationale:** The API-only backend is stable and independently startable; the full OpenAPI 3.0 contract is published for downstream frontend integration; legacy CLI commands are verified unchanged (241 passed); all ops endpoints are default-off with explicit confirm+reason gating; prediction storage is contract-enforced; the fallback matrix is explicit and fail-fast for `formal`; no credentials were leaked. The single failing test is pre-existing and outside this PR's scope (realtime candidate registry), and does not affect the API-only control plane.

> **Frontend note:** Not merged in this PR. Frontend teams should integrate later strictly against `docs/api/openapi.json`. PR #13 remains open as the full-platform demo branch.
