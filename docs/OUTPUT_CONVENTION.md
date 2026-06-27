# Output Convention

## Overview

The pipeline generates two categories of outputs:

1. **Persistent ledger** (`outputs/ledger/`) — cross-date accumulated prediction and actual value storage
2. **Daily run artifacts** (`outputs/runs/{date}/`) — per-date model predictions, weights, fused results, and final deliverables

Both are under `outputs/` which is `.gitignore`d and never committed to Git.

---

## Ledger Storage

```
outputs/ledger/
  dayahead/
    prediction/
      prediction_ledger.parquet   ← Parquet format (primary)
      prediction_ledger.csv       ← CSV format (inspection)
    actual/
      actual_ledger.parquet
      actual_ledger.csv
  realtime/
    prediction/
      prediction_ledger.parquet
      prediction_ledger.csv
    actual/
      actual_ledger.parquet
      actual_ledger.csv
```

- Ledger rows are keyed by `(task, business_day, hour_business)`.
- Duplicate entries are automatically deduplicated on append.
- Ledger is the source of truth for weight learning (`ledger_weight` reads D-30 to D-1 from ledger).

---

## Daily Run Directory

```
outputs/runs/{YYYY-MM-DD}/
  run_manifest.json                         ← Full run metadata (all stages)
    ├── ledger_predict: status, models, rows, model_runtime_config
    ├── ledger_weight: status, training_rows, day_gate range, weight_dir
    ├── ledger_fuse: status, fused_rows, fuse_dir
    ├── ledger_classifier: status, corrections_applied
    └── final_outputs: status, submission_ready_rows

  dayahead/
    prediction/
      all_model_predictions_long.csv        ← All models concatenated (72 rows)
      lightgbm_predictions.csv              ← Per-model (24 rows each)
      timemixer_predictions.csv
      timesfm_predictions.csv
    weight/
      weights.csv                           ← Learned weights per (task, period, model)
      dynamic_weight_trace.csv              ← Day-by-day weight evolution
      candidate_metrics.csv                 ← Per-model metrics on training window
      coverage_report.csv                   ← Prediction coverage by model
    fuse/
      fused_predictions.csv                 ← Weighted fusion result (24 rows)
      fused_debug.csv                       ← Per-model contribution debug info
    final/
      dayahead_final_predictions.csv        ← Final DA output (24 rows)

  realtime/
    prediction/
      all_model_predictions_long.csv        ← All models concatenated (96 rows)
      timesfm_predictions.csv               ← Per-model (24 rows each)
      sgdfnet_predictions.csv
      timemixer_predictions.csv
      rt916_predictions.csv
    weight/
      weights.csv                           ← Learned weights
      dynamic_weight_trace.csv
      candidate_metrics.csv
      coverage_report.csv
    fuse/
      fused_predictions.csv                 ← Weighted fusion result (24 rows)
      fused_debug.csv
    final/
      realtime_final_predictions.csv        ← Pre-classifier output (24 rows)
      realtime_final_predictions_corrected.csv ← Post-classifier output (24 rows)
      classifier_report.json                ← Classifier run metadata

  final/
    dayahead_final_predictions.csv          ← Copy of DA final
    realtime_final_predictions.csv          ← Copy of RT final (pre-classifier)
    realtime_final_predictions_corrected.csv ← Copy of RT final (corrected)
    submission_ready.csv                    ← Merged DA+RT final (24 rows)
```

---

## Range Daily Run Directory

```
outputs/runs/range_{YYYY-MM-DD}_to_{YYYY-MM-DD}/
  range_manifest.json               ← Range-level manifest (all days)
  range_summary.csv                 ← CSV summary of all days in range
```

### `range_manifest.json`

```json
{
  "pipeline": "ledger_full_range",
  "start_date": "2026-02-24",
  "end_date": "2026-02-28",
  "total_days": 5,
  "completed_days": 5,
  "failed_days": 0,
  "skipped_days": 0,
  "status": "complete",
  "daily_results": [
    {
      "date": "2026-02-24",
      "status": "complete",
      "submission_ready_path": "outputs/runs/2026-02-24/final/submission_ready.csv",
      "warnings_count": 0,
      "errors_count": 0
    }
  ]
}
```

### `range_summary.csv`

```
date,status,submission_ready_exists,submission_ready_rows,errors_count,warnings_count
2026-02-24,complete,True,24,0,0
2026-02-25,complete,True,24,0,0
...
```

---

## File Naming Conventions

| Directory Name | Content | Notes |
|---------------|---------|-------|
| `prediction/` | Raw model predictions (per-model CSVs + long table) | Cache key for rerun |
| `weight/` | Learned fusion weights, trace, metrics | Regenerated each run |
| `fuse/` | Fused (weighted) predictions + debug info | **Not `fused/`** |
| `final/` | Final deliverables including `submission_ready.csv` | What gets submitted |

---

## Key Output Files

### `submission_ready.csv`

The final deliverable — 24 rows, one per hour:

```
business_day,ds,hour_business,period,dayahead_price,realtime_price
2026-02-24,2026-02-24 01:00:00,1,1_8,343.1948,344.6605
...
2026-02-24,2026-02-25 00:00:00,24,17_24,348.7663,334.345
```

- `hour_business`: 1..24 (hour 24 = D+1 00:00)
- `dayahead_price`: fused dayahead prediction
- `realtime_price`: post-classifier realtime prediction (may include -80.00 corrections)
- No `_x`/`_y` suffix columns

### `run_manifest.json`

Complete metadata for all pipeline stages including model status, row counts, warnings, errors, runtime config, and timestamps.

### `weights.csv`

Learned BGEW weights per `(task, period, model)`:

```
task,period,model_name,weight
dayahead,1_8,lightgbm,0.108293
dayahead,1_8,timemixer,0.065361
dayahead,1_8,timesfm,0.826346
```

- Weights within a `(task, period)` sum to 1.0.
- Periods: `1_8`, `9_16`, `17_24`.

### `dynamic_weight_trace.csv`

Day-by-day evolution of BGEW weights across the 30-day training window:
- `age_days`: 1 (yesterday) to 30 (30 days ago)
- `day_gate`: learning rate per day (0.3-0.85)
- `loss`, `normalized_loss`: per-model per-day loss
- `weight_after`: weight after each day's update

### `fused_predictions.csv`

Weighted fusion output:
```
task,business_day,ds,hour_business,period,y_fused
dayahead,2026-02-24,2026-02-24 01:00:00,1,1_8,343.1948
```

- `y_fused` = weighted sum of all model predictions for that hour.

### `classifier_report.json`

Classifier metadata:
```json
{
  "target_date": "2026-02-24",
  "method": "classifier_bridge",
  "success": true,
  "fallback_used": false,
  "n_corrections": 4,
  "corrected_hours": [
    {"hour_business": 5, "ds": "2026-02-24 05:00:00", "before": -63.9575, "after": -80.0},
    {"hour_business": 6, "ds": "2026-02-24 06:00:00", "before": -68.1796, "after": -80.0}
  ]
}
```
- `corrected_hours` is an array of objects, each with `hour_business`, `ds`, `before`, `after`.
- Empty list `[]` if no corrections were applied.

---

## Other Output Directories

| Directory | Created by | Description |
|-----------|-----------|-------------|
| `outputs/smoke/` | `ledger_smoke` | Smoke test outputs (lightweight) |
| `outputs/repro_check/` | `scripts/check_reproducibility.py` | Reproducibility verification artifacts |
| `outputs/unified_runs/` | Old pipeline (legacy) | Old unified output format (unused) |
| `outputs/audit_30day_*` | `scripts/audit_30day_backfill.py` | 30-day backfill audit report |

---

## Caching Behavior

- **`prediction/`** is cached: if per-model CSVs exist, `ledger_predict` skips model inference (cache HIT).
- **`weight/`**, **`fuse/`**, **`classifier/`**, **`final/`** are not cached: each run regenerates them.
- Use `--force` on `ledger_full` or `ledger_predict` to clear prediction cache and force rerun.
