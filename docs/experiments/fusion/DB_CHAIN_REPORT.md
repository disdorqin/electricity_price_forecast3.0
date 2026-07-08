# EFM3 3.0 DB Ledger + One-Click Chain Report

## 1. Scope

| Item       | Value |
| ---------- | ----- |
| branch     | `agent/final-archive-release-notes` |
| SHA        | `383313e` |
| PRs        | Single PR (merged A+B+C+D) for efficiency |
| DB backend | MySQL 8.0 (Docker: mysql-local) |
| fallback   | FilePredictionStore (CSV to `outputs/prediction_store/`) |

## 2. Schema

| Table | Status | Purpose |
| ----- | ------ | ------- |
| `efm_runs` | ✅ | Run metadata, status, delivery tracking |
| `efm_actual_prices` | ✅ | Ground truth actual prices |
| `efm_feature_snapshots` | ✅ | Input features (JSON) |
| `efm_predictions` | ✅ | ALL predictions (upsert key: run_id+date+hour+stage) |
| `efm_fusion_decisions` | ✅ | Seasonal DA router decisions |
| `efm_postflight_checks` | ✅ | 8 quality gates |
| `efm_delivery_outputs` | ✅ | Export file records |
| `efm_model_registry` | ✅ | Model metadata |
| `efm_run_events` | ✅ | Event log |
| `efm_artifacts` | ✅ | File artifact tracking |

## 3. Chain

| Step | DB table | Status |
| ------------------- | -------------------------------------- | ------ |
| run create | `efm_runs` | ✅ |
| feature snapshot | `efm_feature_snapshots` | ✅ |
| dayahead prediction | `efm_predictions` | ✅ |
| realtime prediction | `efm_predictions` | ✅ |
| shadow prediction | `efm_predictions` (`is_shadow=true`) | ✅ |
| seasonal router | `efm_fusion_decisions` + `efm_predictions` | ✅ |
| final selected | `efm_predictions` (`is_selected=true`) | ✅ |
| postflight | `efm_postflight_checks` | ✅ |
| export | `efm_delivery_outputs` | ✅ |

## 4. CLI

| Command | Result |
| ------- | ------ |
| `python main.py --init-db --db-url mysql+pymysql://...` | ✅ Initializes 10 tables |
| `python main.py YYYY-MM-DD --use-db --mode dry_run` | ✅ Dry-run with DB |
| `python main.py YYYY-MM-DD --use-db --mode formal --chain seasonal_da_router --export-submission` | ✅ Full production |
| `python main.py YYYY-MM-DD` | ✅ Old command unchanged |
| `export EFM3_DB_URL=...; python main.py YYYY-MM-DD --use-db --mode formal` | ✅ Env var support |

## 5. Tests

| Test | Tests | Result |
| ---- | ----: | ------ |
| `test_db_schema_contract.py` | 30 | ✅ ALL PASS |
| `test_seasonal_da_router.py` | 20 | ✅ ALL PASS |
| `test_cli_db_flags.py` | 13 | ✅ ALL PASS |
| **DB-ledger smoke** (manual) | Create run, events, update | ✅ PASS |
| **Total new** | **63** | ✅ **ALL PASS** |

## 6. Safety

| Check | Result |
| -------------------------------- | ------ |
| formal requires DB | ✅ `formal_requires_db: true` enforced |
| dry_run can fallback | ✅ FilePredictionStore fallback |
| no shadow to final | ✅ `shadow_not_final` postflight check |
| no target-day leakage | ✅ Not used as prediction feature |
| no champion replacement | ✅ Not modified |
| no submission unless export flag | ✅ `--export-submission` required |
| old command unchanged | ✅ Default pipeline unchanged |
| all flags default-off | ✅ `--use-db`, `--init-db` default False |

## 7. Recommendation

**DB_CHAIN_RECOMMENDATION: READY_FOR_DB_DRY_RUN**

## 8. Final Verdict

**DB_CHAIN_RESULT: PASS**

EFM3 3.0 now supports:
- MySQL ledger as primary prediction store (10 tables, full upsert)
- Seasonal DA policy router with DB-backed decisions
- One-click full pipeline (11 steps)
- Postflight quality gates (8 checks including shadow_not_final)
- Formal export from DB with delivery tracking
- File fallback when DB is unavailable
- Full backward compatibility with old commands
