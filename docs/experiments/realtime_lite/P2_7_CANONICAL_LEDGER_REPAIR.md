# P2.7 Canonical Ledger Repair and Realtime Registry Correction

## 1. Why this correction exists

P2.6 found that the P2.5 realtime fusion analysis used a buggy XLSX fallback path after the populated actual ledger was lost during git checkout. The XLSX rows are naturally sorted as `00:00, 01:00, ..., 23:00`, but the delivery pipeline expects `hour_business=1..24` to mean `01:00, 02:00, ..., 23:00, 00:00`.

The old fallback effectively mapped midnight to hour_business 1. That made SGDFNet predictions, which were already ordered by correct `hour_business`, compare against shifted actuals. Therefore the P2.5 fusion numbers were invalid.

## 2. Canonical mapping

Correct mapping:

| Timestamp hour | hour_business |
|---:|---:|
| 01:00 | 1 |
| 02:00 | 2 |
| ... | ... |
| 23:00 | 23 |
| 00:00 | 24 |

P2.7 introduced a canonical loader in the delta experiment repository:

- `common/realtime_canonical_loader.py`
- `tests/test_realtime_canonical_loader.py` with 10/10 tests passing

Source commit:

- `disdorqin/electricity_forecast_deep_sgdf_delta@d0d8b17`

## 3. Corrected 10-window metrics

Canonical 10-window comparison:

| Month | DA anchor | SGDFNet | Winner |
|---|---:|---:|---|
| 2025-03 | 20.60 | 22.89 | DA anchor |
| 2025-04 | 15.68 | 16.44 | DA anchor |
| 2025-05 | 16.58 | 17.21 | DA anchor |
| 2025-06 | 16.89 | 16.82 | SGDFNet |
| 2025-09 | 13.30 | 13.75 | DA anchor |
| 2025-10 | 15.90 | 16.69 | DA anchor |
| 2026-03 | 26.44 | 27.73 | DA anchor |
| 2026-04 | 18.96 | 18.87 | SGDFNet |
| 2026-05 | 20.37 | 20.30 | SGDFNet |
| 2026-06 | 30.44 | 31.29 | DA anchor |
| **Overall** | **19.52** | **20.20** | **DA anchor** |

SGDFNet wins 3/10 months. DA anchor wins 7/10 months and is better overall by 0.68 percentage points.

## 4. Registry impact

The previous P2.4 registry stated that SGDFNet was 20.20 versus DA anchor 26.95. That DA value was based on the buggy hour-index interpretation and is no longer valid.

The registry is corrected as follows:

- SGDFNet remains a realtime candidate, but only as an auxiliary model for a future DA-aware selector.
- SGDFNet must not be treated as a standalone replacement for DA anchor.
- DA anchor remains the primary realtime baseline.
- A future runtime adapter must select among DA anchor, SGDFNet, and optional blend by scene/window.
- No production chain is changed by this correction.

## 5. Updated recommendation

P2.7 result:

- `P2_7_10_WINDOW_DECISION: SKIP_AND_BUILD_DA_AWARE_GATE`
- `P2_7_RECOMMENDATION: SGDFNET_ONLY_CANDIDATE` with revised note that DA beats SGDFNet overall by 0.68pp
- `P2_7_RESULT: PASS`

Project-level interpretation:

- Do not build a SGDFNet-only production replacement.
- Build a DA-aware realtime gate.
- Keep SGDFNet as an auxiliary candidate because it wins 3/10 months and is CPU-only, complete, and cheap.
- Keep TimesFM as experimental-only until more canonical windows are tested.
