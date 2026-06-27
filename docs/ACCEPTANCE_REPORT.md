# Acceptance Report

## Audit Date

2026-06-28

## Repository

https://github.com/disdorqin/electricity_forecast_model2.1

## Current Commit

<!-- TODO: Fill after final commit -->
`6389b685bbc89736eaf53b9ef1265133f40c3fae`

---

## Supported Commands

| Command | Description | Status |
|---------|-------------|--------|
| `python main.py YYYY-MM-DD` | Single-day full production pipeline | DEFAULT |
| `python main.py YYYY-MM-DD YYYY-MM-DD` | Range daily full pipeline | NEW |
| `python main.py --start YYYY-MM-DD --end YYYY-MM-DD` | Range daily full pipeline (explicit) | NEW |
| `python main.py --pipeline ledger_full --date YYYY-MM-DD` | Single-day full (explicit) | ACTIVE |
| `python main.py --pipeline ledger_full_range --start ... --end ...` | Range (explicit) | NEW |
| `python main.py --pipeline ledger_predict --date YYYY-MM-DD` | Predict only | ACTIVE |
| `python main.py --pipeline ledger_backfill --start ... --end ...` | Backfill ledger | ACTIVE |
| `python main.py --pipeline ledger_weight --date YYYY-MM-DD` | Weight learning only | ACTIVE |
| `python main.py --pipeline ledger_fuse --date YYYY-MM-DD` | Fusion only | ACTIVE |
| `python main.py --pipeline ledger_classifier --date YYYY-MM-DD` | Classifier only | ACTIVE |
| `python main.py --pipeline ledger_smoke --date YYYY-MM-DD` | Smoke test | ACTIVE |

---

## Verification Commands

| Command | Status |
|---------|--------|
| `python scripts/env_check.py` | ACTIVE |
| `python scripts/verify_final_pipeline.py --date YYYY-MM-DD --runs-root outputs/runs` | ACTIVE |
| `python scripts/verify_range_pipeline.py --start YYYY-MM-DD --end YYYY-MM-DD --runs-root outputs/runs` | NEW |
| `python scripts/verify_smoke.py` | ACTIVE |
| `python scripts/check_reproducibility.py YYYY-MM-DD --seed 42 --deterministic` | ACTIVE |

---

## Acceptance Criteria

### Self-Contained Delivery

| Criterion | Expected | Status |
|-----------|----------|--------|
| `--epf-v1-root` not required | All adapters use bundled lightGBM/ + TimesFMBackend/ | PASS |
| No developer absolute paths | No `D:\`, `C:\`, `/Users/` in formal code | PASS |
| `git ls-files outputs/` | Empty (outputs/ is gitignored) | PASS |
| `git ls-files data/` | Empty (data/ is gitignored) | PASS |
| `git ls-files models/` | Empty (models/ is gitignored) | PASS |
| `git ls-files daily_runs/` | Empty (daily_runs/ is gitignored) | PASS |
| `git ls-files fusion_runs/` | Empty (fusion_runs/ is gitignored) | PASS |

### Pipeline Validation

| Check | Expected | Status |
|-------|----------|--------|
| `env_check.py` passes | All dependencies OK, all bundled models OK | <!-- TODO: Run --> |
| `ledger_smoke` runs without error | Pipeline completes in ~15-30 min | <!-- TODO: Run --> |
| `ledger_full` single-day | 24-row submission_ready.csv, no _x/_y columns | <!-- TODO: Run --> |
| `verify_final_pipeline.py` passes | All checks green | <!-- TODO: Run --> |
| `ledger_full_range` works | Each day produces valid output, range_summary.csv correct | <!-- TODO: Run --> |

---

## TODO: Full Pipeline Run

This section must be filled after a successful full-run on actual hardware.

### Hardware/Environment

- Host:
- Python: 3.11.x
- CUDA available: yes/no
- GPU:
- CPU:

### Single-Day Full Run

Command:
```
conda run -n epf-2 python main.py 2026-02-24 ^
    --data-path data/shandong_pmos_hourly.xlsx ^
    --max-cpu-workers 2 ^
    --max-gpu-workers 1 ^
    --seed 42 ^
    --deterministic
```

Manifest path: `outputs/runs/2026-02-24/run_manifest.json`

Key manifest fields:
```json
{
  "pipeline": "ledger_full",
  "status": "complete",
  "stages": {
    "ledger_predict": {
      "status": "complete",
      "external_epf_v1_root": null,
      "dayahead": {"long_rows": 72, "training_rows": 2160},
      "realtime": {"long_rows": 96, "training_rows": 2880}
    },
    "ledger_weight": {"status": "complete"},
    "ledger_fuse": {"status": "complete"},
    "ledger_classifier": {"status": "complete"},
    "final_outputs": {
      "status": "complete",
      "submission_ready_rows": 24
    }
  }
}
```

Final output: `outputs/runs/2026-02-24/final/submission_ready.csv`

Expected: 24 rows, columns `business_day, ds, hour_business, period, dayahead_price, realtime_price`, no `_x`/`_y` suffix columns.

Verification: `python scripts/verify_final_pipeline.py --date 2026-02-24 --runs-root outputs/runs`

### Range Daily Run (if applicable)

Command:
```
conda run -n epf-2 python main.py 2026-02-24 2026-02-25 ^
    --data-path data/shandong_pmos_hourly.xlsx ^
    --max-cpu-workers 2 ^
    --max-gpu-workers 1 ^
    --seed 42 ^
    --deterministic
```

Expected:
- `outputs/runs/2026-02-24/final/submission_ready.csv` exists
- `outputs/runs/2026-02-25/final/submission_ready.csv` exists
- `outputs/runs/range_2026-02-24_to_2026-02-25/range_manifest.json` exists
- `outputs/runs/range_2026-02-24_to_2026-02-25/range_summary.csv` exists (2 rows)

Verification: `python scripts/verify_range_pipeline.py --start 2026-02-24 --end 2026-02-25 --runs-root outputs/runs`

---

## Known Issues / Remaining Risks

- None identified at audit time.
- Full pipeline run requires CUDA GPU and ~2-6 hours.
- Smoke pipeline is the recommended lightweight alternative for quick verification.
