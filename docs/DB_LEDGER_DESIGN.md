# EFM3 3.0 DB Ledger Design

## Overview

MySQL is the **primary ledger** for EFM3 3.0. All predictions, decisions,
postflight checks, and delivery outputs are stored in the database.
CSV files are **export artifacts** only — derived from the DB.

## Architecture

```
main.py --date YYYY-MM-DD --use-db
    │
    ├── DbConnectionManager ──→ MySQL (efm3 database)
    │
    ├── PredictionStore (MySQLPredictionStore)
    │       ├── write_predictions()       → efm_predictions
    │       ├── write_shadow_predictions() → efm_predictions (is_shadow=true)
    │       ├── write_selected_final()     → efm_fusion_decisions + efm_predictions
    │       ├── read_predictions()         → FROM efm_predictions
    │       └── export_submission_ready()  → CSV file
    │
    ├── run_full_chain()
    │       ├── create_run()              → efm_runs
    │       ├── step events              → efm_run_events
    │       ├── seasonal DA router       → efm_fusion_decisions
    │       ├── db_postflight            → efm_postflight_checks
    │       └── export                   → efm_delivery_outputs
    │
    └── Fallback: FilePredictionStore (when DB unavailable)
                └── outputs/prediction_store/{run_id}/
```

## Tables

| Table | Purpose |
|-------|---------|
| `efm_runs` | Run metadata, status, delivery status |
| `efm_actual_prices` | Ground truth actual prices |
| `efm_feature_snapshots` | Input features (JSON) |
| `efm_predictions` | **All** predictions — every model, every stage |
| `efm_fusion_decisions` | Policy routing decisions (seasonal router) |
| `efm_postflight_checks` | Quality check results |
| `efm_delivery_outputs` | Exported file records |
| `efm_model_registry` | Model metadata registry |
| `efm_run_events` | Event log for each run |
| `efm_artifacts` | File artifact registry |

## Key Design Decisions

1. **Upsert pattern**: All insert operations use `ON DUPLICATE KEY UPDATE`
   so re-running the same run_id is safe.

2. **Shadow isolation**: Shadow predictions are tagged `is_shadow=true`.
   The postflight checker verifies NO shadow predictions leak into final
   selected outputs. `shadow_not_final` check enforces this.

3. **Canonical hour mapping**: All hour references use `hour_business`
   (1-24) where 01:00=1 ... 00:00=24.

4. **Formal mode requires DB**: `--mode formal` without `--use-db` or
   `EFM3_DB_URL` will fail. Dry-run can fall back to FilePredictionStore.

5. **Prediction stages**: Each prediction row includes a `stage` field
   identifying its role: `raw_model`, `da_anchor`, `official_baseline`,
   `selector_shadow`, `p3_shadow`, `seasonal_da_router`, `final_selected`.

6. **Run isolation**: Every prediction, decision, and event is scoped
   to a `run_id`. Runs are independent and replayable.
