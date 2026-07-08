# EFM3 DB Chain Code Review — Branch vs Main

## Summary

Branch: `agent/final-archive-release-notes`
Base main: `15bd7af`
Diff: **43 files changed, 6777 insertions**

## File Categories

| Category | Files | Lines |
|----------|-------|-------|
| DB schema / migrations | 4 `db/*` | ~530 |
| DB repository / models | 6 `common/db/*` | ~552 |
| Data ingestion | 8 `common/data_ingestion/*` | ~1204 |
| Prediction store | 1 `common/prediction_store.py` | ~698 |
| Full chain + seasonal router | 3 `pipelines/*` | ~1383 |
| Data update pipeline | 1 `pipelines/data_update_pipeline.py` | ~199 |
| CLI / main.py hooks | 2 `cli/parser.py` + `main.py` | ~95 |
| Docs | 7 `docs/*` | ~557 |
| Tests | 5 `tests/*` | ~1055 |
| Config | 4 `configs/*` + `.gitignore` | ~69 |

## Audit Results

| # | Check | Result | Note |
|---|-------|--------|------|
| 1 | Old command `main.py YYYY-MM-DD` unchanged | ✅ PASS | All ledger pipelines preserved |
| 2 | CLI new flags default-off | ✅ PASS | All 7+ flags have `default=False` |
| 3 | Formal without DB fails fast | ✅ PASS | `formal_requires_db: true` enforced |
| 4 | Dry-run without DB falls back to file | ✅ PASS | `FilePredictionStore` fallback |
| 5 | No shadow to final | ✅ PASS | `shadow_not_final` postflight check enforced |
| 6 | No champion replacement | ✅ PASS | Only mentioned in prohibition context |
| 7 | No submission unless `--export-submission` | ✅ PASS | Exporter checks `is_formal` |
| 8 | No password committed in code/docs | ✅ PASS | **1 found, FIXED** — hardcoded in data_update_pipeline.py; now uses URL parser |
| 9 | No data/models/outputs committed | ✅ PASS | `.gitignore` updated |
| 10 | No RT916/TimeMixer online dependency | ✅ PASS | Not imported |
| 11 | No target-day actual leakage | ✅ PASS | D14 cutoff tracked; actuals only for metrics |
| 12 | Dataset not READY → formal fails | ✅ PASS | Orchestrator checks dataset status |

## Fixes Applied During Review

| File | Issue | Fix |
|------|-------|-----|
| `pipelines/data_update_pipeline.py` | Hardcoded DB credential in source (example `SuperSecret123#`) | Replaced with DB URL parser from `--db-url` arg |

## Verdict

**CODE_REVIEW_RESULT: PASS** (1 issue found and fixed)
