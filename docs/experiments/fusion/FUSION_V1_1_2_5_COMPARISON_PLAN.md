# Fusion v1.1 vs 2.5 Comparison Plan

## Objective

Compare the Seasonal DA Policy Router against the 2.5 stable delivery chain and the 3.0 official baseline.

## Current 2.5 status

`2_5_status: unavailable_or_cached_only`

The finalization run could not perform a clean direct 2.5 comparison. This blocks any production claim but does not block registry-only shadow monitoring documentation.

## Comparison variants

1. 2.5 stable baseline, if cached outputs are available
2. 3.0 official baseline
3. 3.0 DA anchor
4. 3.0 seasonal DA router
5. Fusion v1.1 conservative

## Required windows

- sanity day: 2026-07-03
- winter hard window: 2025-11, 2025-12, 2026-01, 2026-02
- validation months: 2026-03, 2026-04, 2026-05, 2026-06

## Required metrics

- sMAPE_floor50
- runtime
- coverage
- leakage audit
- no final contamination
- no submission contamination

## Decision rule

If 2.5 cached outputs become available, re-run this comparison before any production consideration.

Until then, Fusion v1.1 remains shadow-monitoring documentation only.
