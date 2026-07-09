# Shandong PMOS Hourly CSV — Ledger Backfill

## 1. Purpose

The EFM3 production ledger (`efm3` MySQL DB) only ingested **2026-01-25 →
2026-02-25** (32 days) of Shandong price data through the normal daily
pipeline, even though the raw source file covers **2022-01-01 → 2026-07-01**.
This left a data-ingestion gap: the Jan–Jun 2026 formal simulation could only
read 32 days of actuals.

This backfill imports the full **Jan–Jun 2026** window from
`data/shandong_pmos_hourly.csv` into the ledger so the day-ahead / real-time
actuals are available for every business day in the window.

## 2. Source file

| Attribute | Value |
| --- | --- |
| Path | `data/shandong_pmos_hourly.csv` |
| Encoding | **GBK** (auto-detected; UTF-8 tried first, GBK as fallback) |
| Rows | 39,408 (hourly) |
| Date coverage | 2022-01-01 → 2026-07-01 |
| Chinese columns | `时刻` (timestamp), `日前电价` (day-ahead), `实时电价` (real-time) |

The backfill window is **2026-01-01 → 2026-06-30** (181 days).

## 3. Target tables & mapping

| Source column | Target table | Target column / `data_type` |
| --- | --- | --- |
| `日前电价` | `efm_market_data_hourly` | `data_type='da_price'`, `value` |
| `日前电价` | `efm_actual_prices` | `da_anchor` |
| `实时电价` | `efm_market_data_hourly` | `data_type='rt_price'`, `value` |
| `实时电价` | `efm_actual_prices` | `rt_actual` |
| `日前电价` | `efm_predictions` | `stage='da_anchor'` (lineage ledger) |

Supporting tables:

- `efm_data_sources` — one row: `shandong_pmos_hourly_csv`.
- `efm_source_files` — one row with `file_sha256`, `import_status='IMPORTED'`.

### Hour canonical mapping

`00:00 → 24`, `01:00 → 1`, … `23:00 → 23` (matches `efm3` convention).

### Missing values

Hours missing within an otherwise-complete 24h day are **linearly interpolated**
between the nearest present neighbours and flagged in `quality_flags`
(`interpolated=true`). For the Jan–Jun window only **70** cells were
interpolated and **0** days were incomplete.

## 4. How to run

```bash
# Dry-run (parse + validate, no DB writes)
python tools/db_ops/backfill_shandong_pmos_csv.py \
    --csv-path data/shandong_pmos_hourly.csv \
    --start-date 2026-01-01 --end-date 2026-06-30 \
    --db-url "$EFM3_DB_URL" --encoding gbk --dry-run

# Commit
python tools/db_ops/backfill_shandong_pmos_csv.py \
    --csv-path data/shandong_pmos_hourly.csv \
    --start-date 2026-01-01 --end-date 2026-06-30 \
    --db-url "$EFM3_DB_URL" --encoding gbk --commit
```

The operation is **idempotent** — all writes use `ON DUPLICATE KEY UPDATE`
(unique keys: `efm_market_data_hourly(market,data_type,trade_date,hour_business)`,
`efm_actual_prices(target_date,hour_business)`, `efm_predictions(run_id,target_date,hour_business,stage)`).

## 5. Audit expectations (Jan–Jun 2026)

| Table | Expected rows |
| --- | --- |
| `efm_market_data_hourly` (`da_price`) | 181 × 24 = **4,344** |
| `efm_market_data_hourly` (`rt_price`) | 181 × 24 = **4,344** |
| `efm_actual_prices` | 181 × 24 = **4,344** |
| `efm_predictions` (`stage='da_anchor'`) | 181 × 24 = **4,344** |

Run `tools/db_ops/inspect_shandong_pmos_csv.py` for a pre-import preview, and
the inline audit (writes `outputs/db_backfill_preview/jan_jun_backfill_audit.md`)
for a post-import verification.

## 6. Integration with the prediction chain

After backfill, two chain changes let the day-ahead anchor flow into the
router even when the local ledger CSV lacks a date:

1. **`pipelines/full_chain_orchestrator._step_dayahead_prediction`** — reads the
   local day-ahead ledger CSV first; for any missing hours it falls back to
   `efm_market_data_hourly` (`data_type='da_price'`) and writes them as
   `stage='da_anchor'` predictions for the current run.
2. **`pipelines/seasonal_da_router`** — non-winter (Mar–Jun) now falls back to
   `da_anchor` after `official_baseline` (realtime) and `sgdfnet` are absent, so
   the day-ahead clearing price serves as the benchmark forecast against the
   real-time actual.

The `da_anchor` rows written by the backfill script itself use a separate
`run_id` (`backfill_da_anchor_20260101_20260630`, `mode='dry_run'`) purely for
**lineage/audit** and are excluded from `formal_sim` metric queries.

## 7. Tests

- `tests/test_backfill_shandong_pmos_csv.py` — pure helpers (URL decode, encoding
  sniff, column candidates, hour mapping, interpolation).
- `tests/test_backfill_window_parsing.py` — GBK CSV → 24h daily records, 00:00
  → hour 24, gap interpolation.
- `tests/test_seasonal_da_router_da_anchor_fallback.py` — non-winter `da_anchor`
  fallback.
- `tests/test_full_chain_da_anchor_fallback.py` — orchestrator DB fallback.
- `tests/test_backfill_db_integration.py` — end-to-end backfill against a
  throwaway test DB (env-gated on `EFM3_TEST_DB_URL`).
