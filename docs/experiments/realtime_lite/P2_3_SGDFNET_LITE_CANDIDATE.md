# P2.3 SGDFNet Lite Candidate

## Performance Summary

| Metric | Value |
|--------|-----:|
| Overall sMAPE_floor50 | **20.20** |
| Baseline (DA anchor) | 26.95 |
| Absolute improvement | **+6.75pp** |
| Relative improvement | **+25.0%** |
| Completed days | **363/363 (100%)** |
| Avg runtime | **40s/day** |
| Backend | **CPU-only** |
| GPU dependency | None |
| Failures | 0 |

## Monthly Breakdown (10 test windows)

| Month | DA_anchor | SGDFNet | Delta | Days |
|-------|:--------:|:-------:|:----:|:---:|
| 2025-03 | 31.86 | 22.89 | -8.97 | 31 |
| 2025-04 | 28.98 | 16.44 | -12.54 | 30 |
| 2025-05 | 26.89 | 17.21 | -9.68 | 31 |
| 2025-06 | 24.43 | 16.82 | -7.61 | 30 |
| 2025-09 | 19.57 | 13.75 | -5.82 | 30 |
| 2025-10 | 19.73 | 16.69 | -3.04 | 31 |
| 2026-03 | 31.79 | 27.73 | -4.06 | 31 |
| 2026-04 | 27.08 | 18.87 | -8.21 | 30 |
| 2026-05 | 25.67 | 20.30 | -5.37 | 31 |
| 2026-06 | 33.46 | 31.29 | -2.17 | 29 |

**SGDFNet beats DA anchor on ALL 10 windows.** No exceptions.

## Scene Breakdown

| Scene | DA_anchor | SGDFNet | Delta | N hours |
|-------|:--------:|:-------:|:----:|:------:|
| Spike hours | 23.41 | 19.26 | -4.15 | 5359 |
| Negative hours | 24.97 | 11.04 | -13.92 | 1438 |
| Normal hours | 72.43 | 56.65 | -15.79 | 499 |

SGDFNet outperforms DA anchor across all market conditions, with the
largest gains on negative and normal hours.

## Production Feasibility

| Check | Result |
|-------|--------|
| CPU-only | ✅ PASS |
| Complete coverage | ✅ PASS |
| Stable runtime | ✅ PASS |
| No GPU dependency | ✅ PASS |
| Consistent vs DA anchor | ✅ PASS |
| Fits batch window | ✅ PASS |
| Retry/cache friendly | ✅ PASS |

## Recommendation

P2.3 recommends **CANDIDATE** status for SGDFNet lite. It is the only
P2.2 model that is production-viable. It does not replace the 3.0
champion and requires a future shadow adapter before runtime integration.
