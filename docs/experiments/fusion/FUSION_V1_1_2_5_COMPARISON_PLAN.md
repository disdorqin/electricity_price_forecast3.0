# EFM3 Seasonal DA Router vs 2.5 Baseline — Comparison Plan

## Objective

Compare the Seasonal DA Policy Router (Fusion v1.1) against the 2.5 stable
delivery chain and the 3.0 official baseline. This does NOT need to be a
full backtest — a dry-run sanity check + key windows is sufficient.

## Comparison Variants

1. **2.5 stable baseline** — Cached or previously validated official 2.5 predictions
2. **3.0 official baseline** — SGDFNet realtime prediction (3.0 main)
3. **3.0 DA anchor** — Day-ahead clearing price only
4. **3.0 seasonal DA router** — Winter DA anchor, non-winter official
5. **Fusion v1.1 conservative** — Full v1.1 pipeline (identical to DA router)

## Test Windows

### A. Sanity Day

**2026-07-03**: Single-day check that all variants can run, produce output,
pass leakage audit, and not contaminate final/submission.

### B. Winter Hard Window

| Month | Days | Rationale |
|-------|-----:|-----------|
| 2025-11 | ≥5 | Early winter — heating season ramp |
| 2025-12 | ≥5 | Peak winter — highest volatility |
| 2026-01 | ≥5 | Mid-winter — extreme price risk |
| 2026-02 | ≥5 | Late winter — transition |

### C. Validation Sample

| Month | Days | Rationale |
|-------|-----:|-----------|
| 2026-03 | ≥5 | Spring transition |
| 2026-04 | ≥5 | Shoulder month |
| 2026-05 | ≥5 | Pre-summer |
| 2026-06 | ≥5 | Summer start |

## Metrics

- sMAPE_floor50 (overall, winter, non-winter, negative, spike, normal)
- Coverage (hours with valid predictions)
- Runtime
- Default-off compliance
- No submission contamination

## 2.5 Baseline Status

If the 2.5 stable chain cannot be run directly (e.g., archived repo, locked
branch), use the **previously validated 2.5 official baseline** recorded in
the 2.5 delivery reports. Explicitly note:

```
2_5_status: unavailable_or_cached_only
```

## Execution Plan

1. Run sanity day (2026-07-03) with all variants
2. If sanity passes, extend to winter window + val sample
3. Generate comparison reports
4. If any variant shows leakage or contamination, flag as FAIL

## Expected Outcome

Based on Fusion v1.1 findings:
- DA anchor and seasonal router will be close or equal
- Fusion v1.1 conservative will be identical to seasonal router
- 2.5 baseline may differ due to different model architecture (v2.5
  uses 7 models + Ledger dynamic weights; 3.0 uses SGDFNet primary)
- The comparison serves as documentation, not as a competition
