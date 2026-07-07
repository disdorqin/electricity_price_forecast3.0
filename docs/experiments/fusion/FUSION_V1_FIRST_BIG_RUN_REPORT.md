# EFM3 Fusion Chain v1 — First Big Run Report

## 1. Run Scope

| Item          | Value |
| ------------- | ----- |
| repo          | disdorqin/electricity_price_forecast3.0 |
| branch        | agent/fusion-chain-v1-shadow-backtest |
| main base SHA | `48fe9b9bb9fb0d0adc5cfa40974f53b9f8d51579` |
| mode          | shadow_backtest (REPLAY ONLY — no model training) |
| months        | 2025-03,04,05,06,09,10,11,12, 2026-01,02,03,04,05,06 |
| days          | 425 |
| hours         | 10,200 |
| runtime       | 12.5s |

## 2. Fusion Policy

- **Base policy**: SGDFNet prediction (primary realtime model) as official baseline
- **Winter policy** (months 11, 12, 1, 2): Force DA-only default, selector not allowed, P3 corrections permitted as overlay
- **Selector policy**: P2.11 DA-SGDF selector — switch to SGDFNet when DA anchor shows large gap; conservative: negative prices and 17-24 period stay on DA anchor
- **P3 overlay policy**: Only applied when P3 confidence >= 0.7; conservative variant uses >= 0.9 with capped magnitude (±80)
- **Fallback policy**: Missing SGDFNet → DA anchor; missing selector/P3 → skip overlay, keep base

## 3. Leaderboard

| Rank | Variant | Overall | vs Official | Winter | Non-winter | Negative | Spike | Normal | Decision |
| ---- | ------: | ------: | ----------: | -----: | ---------: | -------: | ----: | -----: | -------- |
| 1 | oracle_upper_bound                       |    18.74 |            - |    21.77 |        17.54 |     14.81 |  14.62 |   39.00 | ANALYSIS_ONLY |
| 2 | da_anchor                                |    20.77 |        -0.25 |    24.36 |        19.35 |     18.16 |  15.58 |   43.27 |  |
| 3 | winter_da_only_policy                    |    20.93 |        -0.10 |    24.36 |        19.57 |     16.68 |  16.41 |   43.38 |  |
| 4 | conservative_fusion_v1                   |    20.93 |        -0.10 |    24.36 |        19.57 |     16.68 |  16.41 |   43.38 | DIAGNOSTIC_ONLY |
| 5 | official_baseline                        |    21.02 |            - |    24.70 |        19.57 |     15.39 |  16.92 |   43.77 |  |
| 6 | sgdfnet_only                             |    21.02 |        +0.00 |    24.70 |        19.57 |     15.39 |  16.92 |   43.77 |  |
| 7 | p3_extreme_shadow                        |    21.04 |        +0.01 |    24.75 |        19.57 |     15.37 |  16.95 |   43.77 |  |
| 8 | selector_then_p3_overlay                 |    21.21 |        +0.19 |    25.37 |        19.57 |     16.49 |  16.79 |   43.93 |  |
| 9 | p3_then_selector_overlay                 |    21.21 |        +0.19 |    25.37 |        19.57 |     16.49 |  16.79 |   43.93 |  |
| 10 | realtime_selector_shadow                 |    21.21 |        +0.19 |    25.37 |        19.57 |     16.59 |  16.76 |   43.93 |  |

**Key finding**: DA anchor (20.77) consistently beats SGDFNet baseline (21.02) by 0.25pp. The conservative_fusion_v1 (winter DA + high-confidence P3 overlay) improves by 0.10pp but degrades negative hours.

## 4. Monthly Metrics

| Month | Official | DA | Selector | P3 | Conservative_v1 | Winner |
| ----- | -------: | -: | -------: | -: | ------------: | ------ |
| 2025-03 | 21.95 | 20.56 | 21.95 | 21.95 | 21.95 | da_anchor |
| 2025-04 | 22.86 | 22.02 | 22.86 | 22.86 | 22.86 | da_anchor |
| 2025-05 | 18.51 | 18.35 | 18.51 | 18.51 | 18.51 | da_anchor |
| 2025-06 | 23.36 | 21.79 | 23.36 | 23.36 | 23.36 | da_anchor |
| 2025-09 | 16.01 | 15.72 | 16.01 | 16.01 | 16.01 | da_anchor |
| 2025-10 | 15.87 | 16.38 | 15.87 | 15.87 | 15.87 | official_baseline |
| 2025-11 | 22.42 | 22.20 | 23.46 | 22.94 | 22.20 | da_anchor |
| 2025-12 | 25.58 | 25.23 | 26.01 | 25.46 | 25.23 | da_anchor |
| 2026-01 | 22.46 | 21.95 | 22.52 | 22.32 | 21.95 | da_anchor |
| 2026-02 | 28.74 | 28.48 | 29.93 | 28.81 | 28.48 | da_anchor |
| 2026-03 | 10.93 | 10.89 | 10.93 | 10.93 | 10.93 | da_anchor |
| 2026-04 | 21.82 | 21.59 | 21.82 | 21.82 | 21.82 | da_anchor |
| 2026-05 | 17.99 | 18.55 | 17.99 | 17.99 | 17.99 | official_baseline |
| 2026-06 | 22.92 | 23.32 | 22.92 | 22.92 | 22.92 | official_baseline |

**Monthly winner**: DA anchor wins 10/14 months. The conclusion is consistent: **DA anchor is the best single realtime prediction source** for this dataset.

## 5. Scene Metrics

| Scene | Official | DA | Selector | P3 | Conservative_v1 | Effect |
| ----- | -------: | -: | -------: | -: | ------------: | ------ |
| Winter | 24.70 | 24.36 | 25.37 | 24.75 | 24.36 | DA anchor improves winter by 0.34pp |
| Non-winter | 19.57 | 19.35 | 19.57 | 19.57 | 19.57 | Marginal |
| Negative | 15.39 | 18.16 | 16.59 | 15.37 | 16.68 | DA anchor degrades negative hours |
| Spike | 16.92 | 15.58 | 16.76 | 16.95 | 16.41 | DA anchor improves spike by 1.34pp |
| Normal | 43.77 | 43.27 | 43.93 | 43.77 | 43.38 | DA anchor improves normal hours |
| Period 1-8 | 22.69 | 22.15 | 22.84 | 22.70 | 22.23 | DA anchor best in early hours |
| Period 9-16 | 19.16 | 19.04 | 19.36 | 19.17 | 19.12 | DA anchor best mid-day |
| Period 17-24 | 21.19 | 21.06 | 21.40 | 21.21 | 21.37 | DA anchor best at late hours |
| Low DA error | 15.33 | 14.86 | 15.50 | 15.33 | 15.07 | DA anchor 0.47pp better |
| High DA error | 27.59 | 27.50 | 27.82 | 27.62 | 27.58 | DA anchor 0.09pp better |

## 6. Oracle Gap

| Metric | Best Real Fusion (DA Anchor) | Oracle Upper Bound | Gap |
| ------ | ---------------------------: | -----------------: | --: |
| Overall sMAPE | 20.77 | 18.74 | 2.03 |

The oracle gap of 2.03pp represents the maximum headroom from perfect per-hour variant selection. It is a diagnostic bound only and **must not** be used as a real prediction target.

## 7. Runtime

| Step | Time |
| ---- | ---: |
| Load actuals (xlsx) | ~1.5s |
| Load SGDFNet predictions (425 days) | ~6.0s |
| Load P3 shadow (117 days) | ~2.0s |
| Load selector shadow (119 days) | ~1.5s |
| Merge & compute metrics (10 variants) | ~1.0s |
| Serialize outputs (13 files) | ~0.5s |
| **Total** | **12.5s** |

## 8. Leakage Audit

| Check | Result |
| ----- | ------ |
| Target-day actual as feature | PASS — actuals loaded ONLY for metric computation |
| D14 realtime actual used | PASS — all predictions pre-computed (replay mode) |
| Future rolling error used | PASS — no rolling/adaptive component |
| Actual used for policy selection | PASS — policy uses month, hour, pre-computed confidence only |
| Oracle isolated analysis only | PASS — oracle_upper_bound flagged ANALYSIS_ONLY |
| Hour business canonical | PASS — 01:00→1 through 00:00→24 |
| Bad samples filtered | PASS — ALL hours evaluated equally |
| All failures reported | PASS — failure_cases.md documents top failures |
| **Overall** | **FUSION_V1_LEAKAGE: PASS** |

## 9. No Final Contamination

| Check | Result |
| -------------------------- | ------ |
| final/ untouched | PASS — never written to final/ |
| submission_ready untouched | PASS — never written submission_ready.csv |
| champion unchanged | PASS — champion registry not modified |
| delivery_status unchanged | PASS — delivery_status not set |
| exit_code unchanged | PASS — exit_code not modified |
| main.py default-off | PASS — fusion_v1 defaults to enabled: false |

## 10. Failure Cases

Top-10 worst days (conservative_fusion_v1):

| Day | Official | Fusion | Delta | Suspected Cause |
| --- | -------: | -----: | ----: | --------------- |
| 2026-06-20 | 42.94 | 42.94 | 0.00 | No correction applied |
| 2026-05-09 | 36.32 | 36.32 | 0.00 | No correction applied |
| 2025-06-04 | 35.58 | 35.58 | 0.00 | No correction applied |
| 2025-09-26 | 35.35 | 35.35 | 0.00 | No correction applied |
| 2025-06-02 | 34.90 | 34.90 | 0.00 | No correction applied |
| 2026-01-04 | 34.87 | 34.55 | -0.32 | Winter DA anchor slight help |
| 2025-09-27 | 33.93 | 33.93 | 0.00 | No correction applied |
| 2025-12-17 | 32.04 | 31.75 | -0.29 | Winter DA anchor slight help |
| 2025-06-03 | 31.94 | 31.94 | 0.00 | No correction applied |
| 2025-06-14 | 31.91 | 31.91 | 0.00 | No correction applied |

Note: On most datasets, conservative_fusion_v1 produces the same prediction as official_baseline (non-winter months outside P3 coverage). The worst days are dominated by intrinsic SGDFNet prediction errors that no policy variant could correct.

## 11. Code Changes

| File | Status | Notes |
| ---- | ------ | ----- |
| `pipelines/fusion_shadow_v1.py` | NEW | Core fusion pipeline (rule-based, replay-only) |
| `configs/fusion_shadow_v1.yaml` | NEW | Fusion config (default: enabled=false) |
| `scripts/run_fusion_shadow_v1.py` | NEW | Run orchestrator |
| `scripts/analyze_fusion_shadow_v1.py` | NEW | Post-run analysis |
| `tests/test_fusion_shadow_v1_contract.py` | NEW | Contract verification tests |
| `tests/test_fusion_shadow_v1_no_final_contamination.py` | NEW | No-final-contamination tests |
| `docs/experiments/fusion/FUSION_V1_POLICY_DESIGN.md` | NEW | Policy design document |
| `docs/experiments/fusion/FUSION_V1_FIRST_BIG_RUN_REPORT.md` | NEW | This report |

## 12. Recommendation

**FUSION_V1_RECOMMENDATION: DIAGNOSTIC_ONLY**

The fusion_v1 does not meet the SHADOW_MONITORING_READY threshold (improvement 0.10pp < 0.20pp). Key findings:

1. **DA anchor (20.77) beats SGDFNet (21.02)** overall by 0.25pp — the simplest baseline is the best
2. **Winter DA-only improves winter** by 0.34pp (24.70 → 24.36) — winter months are the hardest
3. **Conservative fusion_v1** (winter DA + P3 high-confidence overlay) shows 0.10pp improvement but degrades **negative hours** (15.39 → 16.68)
4. **P3 extreme shadow** (21.04) is essentially neutral — neither helps nor hurts overall
5. **Selector shadows** (21.21) degrade performance in winter months
6. **Oracle gap** (2.03pp) shows headroom exists but requires smarter per-hour selection

## 13. Final Verdict

**FUSION_V1_RESULT: PASS**

The evaluation runs cleanly, all 13 output files are generated, leakage audit passes, no final contamination, and runtime (12.5s) is well under the 30-minute budget. The pipeline is ready for diagnostic monitoring.

### Actions

1. **Retain P3 winter monitoring** — P3 shadow is neutral overall but may have isolated benefits
2. **Realtime selector stays DIAGNOSTIC_ONLY** — selector degrades performance; investigate root cause
3. **Fusion_v1 not merged into runtime** — improvement is too small (0.10pp) to justify runtime integration
4. **Recommend investigating**: Why does DA anchor beat SGDFNet? Is SGDFNet overfitting or is the realtime fusion calibration off?
5. **Oracle gap (2.03pp)** suggests meaningful headroom if per-hour variant selection can be improved
