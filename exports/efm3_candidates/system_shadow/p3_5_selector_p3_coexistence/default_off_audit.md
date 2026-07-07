# Default-Off Audit

## Verification Method
After cleaning all stale shadow dirs from previous runs, verified that running without flags produces no shadow output.

## Results (4 sampled dates, 1 per winter month)
| Date | `extreme_price_shadow/` | `realtime_da_sgdf_selector_shadow/` | `submission_ready.csv` | `final/` |
|------|:---:|:---:|:---:|:---:|
| 2025-11-15 | ❌ absent | ❌ absent | ❌ absent | ❌ absent |
| 2025-12-15 | ❌ absent | ❌ absent | ❌ absent | ❌ absent |
| 2026-01-15 | ❌ absent | ❌ absent | ❌ absent | ❌ absent |
| 2026-02-15 | ❌ absent | ❌ absent | ❌ absent | ❌ absent |

## Conclusion
**DEFAULT_OFF_VERIFIED**: All 4 sampled dates produce no shadow output without flags. Only explicit `--enable-extreme-price-shadow` and `--enable-realtime-da-sgdf-selector-shadow` flags activate shadow modules.
