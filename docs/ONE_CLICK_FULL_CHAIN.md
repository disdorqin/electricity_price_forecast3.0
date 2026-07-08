# EFM3 3.0 One-Click Full Chain

## Overview

The one-click full chain runs the complete prediction pipeline from data
validation to final submission export, with all intermediates stored in
MySQL. This mirrors the 2.5 "one command" experience but with DB ledger.

## Commands

### Initialize Database

```bash
python main.py --init-db --db-url mysql+pymysql://USER:PASS@HOST:3306/efm3
```

This creates all 10 tables in the `efm3` database. Idempotent — safe to
re-run.

### Dry Run (File Ledger)

```bash
python main.py YYYY-MM-DD --chain seasonal_da_router
```

Runs the full pipeline using file-based prediction store. All outputs go
to `outputs/prediction_store/{run_id}/`. No DB required.

### Dry Run (DB Enabled)

```bash
python main.py YYYY-MM-DD --mode dry_run --use-db --chain seasonal_da_router
```

Runs the full pipeline with MySQL ledger. Predictions written to DB.
Dry run mode does NOT export submission_ready.csv to final/ directory.

### Shadow Run

```bash
python main.py YYYY-MM-DD --mode shadow --use-db --chain seasonal_da_router
```

Like dry run, but enables P3 shadow diagnostics. Still no submission
export to final/.

### Formal Run (Full Export)

```bash
python main.py YYYY-MM-DD --mode formal --use-db --chain seasonal_da_router --export-submission
```

Full production run:
1. All predictions → MySQL
2. Seasonal DA router decides final selection
3. Postflight checks (all 8) → efm_postflight_checks
4. Submission CSV → final/submission_ready.csv
5. Delivery record → efm_delivery_outputs

### Environment Variable

```bash
export EFM3_DB_URL="mysql+pymysql://USER:PASS@HOST:3306/efm3"
python main.py YYYY-MM-DD --mode formal --use-db --chain seasonal_da_router --export-submission
```

## Pipeline Steps

| Step | What Happens | DB Table |
|------|-------------|----------|
| 1 | Generate run_id (`efm3_{date}_{sha}_{ts}`) | efm_runs (insert) |
| 2 | Validate input data exists | — |
| 3 | Feature snapshot (read xlsx) | efm_feature_snapshots |
| 4 | Day-ahead prediction (replay from ledger) | efm_predictions |
| 5 | Realtime prediction (replay from runs) | efm_predictions |
| 6 | Seasonal DA router decision | efm_fusion_decisions |
| 7 | Final selection | efm_predictions (is_selected=true) |
| 8 | Postflight checks | efm_postflight_checks |
| 9 | Export submission_ready.csv | efm_delivery_outputs |
| 10 | Update run status | efm_runs (COMPLETE) |

## Safety Guarantees

- Formal mode **requires** a working DB connection
- Shadow predictions are NEVER selected as final
- Postflight verifies 24 rows, no NaN, no duplicates
- submission_ready.csv is only written in formal mode with --export-submission
- Old `main.py YYYY-MM-DD` command unchanged
