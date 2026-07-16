# EFM3 V3.1-R1 Correctness-Repair Research Report

> Generated: 2026-07-16  |  Branch: `research/v3.1-model-upgrade`  |  PR #20 status: **DRAFT / RESEARCH ONLY / promotion_allowed=false**

> ## ⚠️ INVALIDATED_BY_V3.1_R1
>
> The prior PR #20 headline metrics — **DD plain sMAPE 64.84**, **legal-oracle plain 41.39 / floor50 145.71**, and the **A–F candidate ranking** — are **INVALIDATED**. They were produced by a replay that contained 8 defects (target-day DA leakage, business-day mapping error, metric-formula errors, Track D/F/C leakage & index errors, evaluation support inconsistency, and missing contract tests). All 8 are fixed in V3.1-R1. The figures below are from the **corrected** full 2022–2026 rolling-origin replay.

## 1. Scope & Decision (Forecast Availability Contract)

This patch predicts **Real-Time (RT) prices**. The production RT circuit issues the RT forecast for day D *before* day D's DA clearing price is published (confirmed by the leak-free production number RT ≈ 27.4%). Therefore **the target-day DA clearing price is NOT visible at RT prediction time**.

Consequence: `legal_oos_da_prediction` in V3.1 was a literal copy of `da_actual` (target-day leakage, defect #1) and is **removed**. The only legal DA proxy is `da_oos_pred`, output of a genuine **rolling-origin day-ahead model** trained on PAST `da_actual` only. `da_actual` is an ACTUAL, never renamed into a prediction.

## 2. The 8 Defects Fixed (V3.1 → V3.1-R1)

| # | Defect | V3.1-R1 fix |
|---|---|---|
| 1 | `legal_oos_da_prediction` copied `da_actual` | removed; legal proxy = rolling-origin OOS DA model (`da_oos_pred`) |
| 2 | `business_day = times.date` mapping error | reuse `utils.business_day` (D+1 00:00 → business_day D, hour 24) |
| 3 | plain sMAPE / floor50 formula errors | single canonical `fusion.metrics.plain_smape` / `smape_floor50` |
| 4 | Track D `pd.qcut(full_history)` leakage | bins fit per rolling TRAIN window only |
| 5 | Track F residual-as-input + train/infer mismatch | strict K-fold OOF two-stage (base → residual → final = base+resid) |
| 6 | Track C local/global index errors | relative→absolute index fix; assembled `C_seasonal_full` |
| 7 | B/C/DD evaluation-support inconsistency | every candidate reports coverage_rows / coverage_ratio / common mask |
| 8 | no contract tests | 8 `tests/research/test_v31_*.py` (35 checks) + `run_mini_replay.py` (14 checks) |

## 3. Methodology

- **Rolling-origin replay**: retrain every 90 days; predict each target day using only data strictly before the prediction time (STRICT_REPLAY_OOS).
- **Legal DA proxy**: `da_oos_pred` from a rolling-origin L2 LightGBM DA model (CPU-only; GPU disabled on this host).
- **Unified metrics**: `plain_smape = 100·|y−ŷ| / ((|y|+|ŷ|)/2)`; `smape_floor50` with denominator floored at 50 (tail-weighted).
- **Unified evaluation support**: each candidate reports coverage; the final ranking uses ONE full-coverage common mask. Intrinsically-partial candidates (per-season C_*, B_midday 9-16-only) are excluded from that mask by design.
- **Legal Oracle**: EX_POST_ACTUAL_AWARE_UPPER_BOUND — per-row min-loss selection among candidates; invariants assert selected==one candidate, oracle loss ≤ each candidate loss, row count == common mask.

## 4. Full Replay — Calibration Baselines

| Candidate | plain sMAPE | floor50 sMAPE | cov.ratio | Δ vs DD (plain) | 9-16 plain |
| --- | --- | --- | --- | --- | --- |
| A05_huber | 57.02 | 56.43 | 0.94 | -13.15 | 95.91 |
| A05_med | 45.99 | 42.40 | 0.94 | -2.12 | 73.01 |
| A05_q05 | 76.66 | 73.33 | 0.94 | -32.78 | 104.97 |
| A05_q95 | 60.66 | 58.94 | 0.94 | -16.78 | 91.55 |
| DD | 43.82 | 40.92 | 1.00 | 0.00 | 68.73 |
| NEGW | 45.74 | 41.96 | 0.94 | -1.87 | 72.44 |

**Common-mask ranking (full-coverage candidates):**

| Candidate | n_common | plain sMAPE | floor50 sMAPE |
| --- | --- | --- | --- |
| DD | 34672.00 | 43.87 | 40.92 |
| NEGW | 34672.00 | 45.74 | 41.96 |
| A05_med | 34672.00 | 45.99 | 42.40 |
| A05_huber | 34672.00 | 57.02 | 56.43 |
| A05_q95 | 34672.00 | 60.66 | 58.94 |
| A05_q05 | 34672.00 | 76.66 | 73.33 |

**Legal Oracle (calibration):** plain sMAPE = **16.40**, floor50 = 13.44, 9-16 bucket = 24.74, n_rows = 34672, invariant_pass = True.

## 5. Full Replay — New Candidate Tracks A–F

| Candidate | plain sMAPE | floor50 sMAPE | cov.ratio | Δ vs DD (plain) | 9-16 plain |
| --- | --- | --- | --- | --- | --- |
| A_QRA | 46.84 | 43.88 | 0.94 | -2.96 | 75.99 |
| A_q05 | 76.66 | 73.33 | 0.94 | -32.78 | 104.97 |
| A_q50 | 41.90 | 38.95 | 0.94 | 1.97 | 65.75 |
| A_q95 | 60.66 | 58.94 | 0.94 | -16.78 | 91.55 |
| B_midday | 74.39 | 65.67 | 0.31 | -5.56 | 74.39 |
| B_midday_full | 45.56 | 41.96 | 1.00 | -1.75 | 73.97 |
| C_seasonal_full | 47.67 | 43.93 | 0.92 | -3.63 | 75.41 |
| C_shoulder | 46.39 | 42.43 | 0.48 | -3.17 | 75.12 |
| C_summer | 40.39 | 38.09 | 0.23 | -2.87 | 71.42 |
| C_winter | 58.28 | 53.52 | 0.21 | -5.48 | 80.33 |
| DD | 43.82 | 40.92 | 1.00 | 0.00 | 68.73 |
| D_anchor | 45.67 | 41.93 | 0.94 | -1.79 | 72.12 |
| E_fair | 69.45 | 68.68 | 0.94 | -25.58 | 115.28 |
| E_huber | 57.02 | 56.43 | 0.94 | -13.15 | 95.91 |
| F_regime | 46.77 | 43.25 | 0.94 | -2.90 | 73.72 |

**Common-mask ranking (full-coverage candidates):**

| Candidate | n_common | plain sMAPE | floor50 sMAPE |
| --- | --- | --- | --- |
| A_q50 | 33893.00 | 41.96 | 38.98 |
| DD | 33893.00 | 44.04 | 41.06 |
| D_anchor | 33893.00 | 45.74 | 41.96 |
| B_midday_full | 33893.00 | 45.80 | 42.06 |
| F_regime | 33893.00 | 46.79 | 43.25 |
| A_QRA | 33893.00 | 46.98 | 43.98 |
| C_seasonal_full | 33893.00 | 47.67 | 43.94 |
| E_huber | 33893.00 | 57.39 | 56.80 |
| A_q95 | 33893.00 | 61.04 | 59.29 |
| E_fair | 33893.00 | 69.65 | 68.89 |
| A_q05 | 33893.00 | 76.92 | 73.62 |

**Legal Oracle (new candidates):** plain sMAPE = **13.34**, floor50 = 10.61, 9-16 bucket = 19.35, n_rows = 33893, invariant_pass = True.

## 6. Oracle Invariants

- Calibration oracle: eq_selected==candidate = True, loss ≤ each candidate = True, pass = True.
- New-candidate oracle: eq_selected==candidate = True, loss ≤ each candidate = True, pass = True.

## 7. Conclusion

All 8 V3.1 defects are corrected and guarded by contract tests. The corrected full 2022–2026 rolling-origin replay re-establishes the candidate metrics on a leak-free, business-day-consistent, metric-consistent basis. The legal Oracle remains an EX-POST upper bound (not deployable) and is used only to bound the best achievable per-row loss. The headline figures above **replace** the INVALIDATED_BY_V3.1_R1 numbers; they must not be compared to the old 64.84 / 41.39 / 145.71 values.

## 8. Reproduction

```bash
python tools/research/build_full_history_panel.py
python tools/research/full_history_replay.py      # calibration
python tools/research/new_candidates_replay.py    # tracks A-F
python -m pytest tests/research/ -v               # 35 contract checks
python tools/research/generate_v31_report.py     # this report
```
