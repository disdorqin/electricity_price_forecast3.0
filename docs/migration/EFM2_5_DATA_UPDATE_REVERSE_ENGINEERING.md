# 2.5 Data Update Mechanism — Reverse Engineering

## Entrypoints

| Entry | Trigger | Description |
|-------|---------|-------------|
| `sync_data.py` | Direct call or `--pipeline sync_dataset` | Central data sync engine with 3 sources |
| `main.py --sync-data-before-run` | Inline with forecast run | Sync before ledger_full pipeline |
| `main.py --pipeline sync_dataset` | Standalone sync | Same sync engine, independent run |

## Data Sources (ordered by priority)

| Source | Implementation | When Used |
|--------|---------------|-----------|
| **Database** | `fetch_web_grid_data()` — queries `epf_market_data` table with 23 columns | Default auto mode, if .env configured |
| **HTTP** | `_download_latest_available_excel()` — tries `http://qiniu.dirx.com.cn/workspace/eprice_forecast/shandong_pmos_hourly_20220101_{YYYYMMDD}.xlsx` | DB unavailable; scans 60 days back |
| **Local** | `_latest_local_dataset_source()` — scans `data/` for xlsx/csv | DB + HTTP both unavailable |

## File Inventory

| File | Role | In 3.0? |
|------|------|----------|
| `data/shandong_pmos_hourly.xlsx` | Canonical input (primary) | ✅ Same path |
| `data/shandong_pmos_hourly.csv` | CSV mirror | ✅ Same path |
| `outputs/ledger/*/prediction/prediction_ledger.parquet` | Accumulated predictions | ✅ Same path |
| `outputs/ledger/*/actual/actual_ledger.parquet` | Accumulated actuals | ✅ Same path |
| `outputs/runs/{date}/final/submission_ready.csv` | Final output | ✅ Same path |
| `outputs/data_sync/sync_manifest.json` | Sync report | 🔄 Will use DB |

## Path Conventions (Windows)

- All paths relative to project root
- Data paths: `data/` directory
- Ledger paths: `outputs/ledger/{task}/{type}/`
- Run paths: `outputs/runs/{YYYY-MM-DD}/{task}/{stage}/`

## Required Daily Inputs

| Input | Source | Cutoff | Columns |
|-------|--------|--------|---------|
| DA clearing price | `日前电价` from xlsx | D-1 00:00 | 1 price col |
| RT price | `实时电价` from xlsx | D-1 14:00 | 1 price col |
| 21 features | Remaining columns from xlsx | D-1 14:00 | 21 feature cols |

## Output Ledgers

| Ledger | Format | Key | Content |
|--------|--------|-----|---------|
| Prediction ledger | parquet | (task, model, forecast_date, target_day, business_day, hour_business) | y_pred |
| Actual ledger | parquet | (task, target_day, business_day, hour_business) | y_true |

## Risks / Differences from 3.0

| Risk | Impact | Mitigation |
|------|--------|------------|
| 2.5 has no DB schema for source files | Manual tracking | 3.0 has efm_source_files table |
| 2.5 uses .env for DB creds | Password exposure | 3.0 uses --db-url / EFM3_DB_URL env var |
| 2.5 has no canonical hour mapping enforcement | Silent bugs | 3.0 enforces canonical mapping in schema |
| 2.5 sync can silently skip | Missed updates | 3.0 data_update_runs table tracks every run |

## Mapping to 3.0 DB Tables

| 2.5 Concept | 3.0 DB Table |
|-------------|--------------|
| Data source config | `efm_data_sources` |
| Input data xlsx/csv | `efm_source_files` |
| Sync run record | `efm_data_update_runs` |
| Hourly market prices | `efm_market_data_hourly` |
| Dataset readiness | `efm_dataset_versions` |
| Actual prices | `efm_actual_prices` |
| Predictions | `efm_predictions` |
| Sync events | `efm_run_events` |
