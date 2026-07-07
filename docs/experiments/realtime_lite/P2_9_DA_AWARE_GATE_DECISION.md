# P2.9 DA-aware Realtime Gate Decision

## 1. Decision summary

P2.9 evaluated DA-aware realtime gating after P2.7 fixed the canonical realtime hour mapping. The result does not justify replacing DA anchor with SGDFNet. Instead, the recommended realtime direction is a conservative DA-primary selector with SGDFNet as an auxiliary model.

Source experiment:

- Repository: `disdorqin/electricity_forecast_deep_sgdf_delta`
- Commit: `0c0b59f5c6859b85bd95f12acade3dd360209156`
- Package: `exports/efm3_candidates/realtime_da_aware_gate/p2_9_da_aware_gate`

## 2. Main metrics

| Variant | Overall sMAPE_floor50 | vs DA anchor | Decision |
|---|---:|---:|---|
| conservative_gate | 19.23 | -0.07 | candidate |
| static_blend_80DA_20SGD | 19.23 | -0.07 | reference only |
| DA_only | 19.30 | 0.00 | primary baseline |
| lightweight_tree | 19.31 | +0.01 | drop for production-critical path |
| logistic_selector | 19.91 | +0.61 | drop |
| SGDFNet_only | 19.95 | +0.65 | auxiliary only |
| TimesFM | 25.09 | +5.79 | experimental only |

The gain of conservative_gate over DA anchor is only 0.07 percentage points. This is not enough for champion or production status. It is enough to justify a default-off shadow adapter candidate.

## 3. Validation results

- Leave-one-month-out: ML gates beat baseline in 0/10 held-out months.
- Time split train 2025 -> test 2026:
  - DA = 23.58
  - logistic = 23.96
  - tree = 23.58
- Conservative gate is stable because it mostly falls back to DA anchor.

## 4. Scene findings

| Scene | DA anchor | SGDFNet | Winner |
|---|---:|---:|---|
| spike | 17.57 | 19.26 | DA anchor |
| negative | 13.12 | 11.04 | SGDFNet |
| normal | 59.55 | 56.65 | SGDFNet |
| 17_24 | 15.04 | 16.79 | DA anchor |

Interpretation:

- DA anchor remains the safest default.
- SGDFNet has useful auxiliary signal in negative and normal hours.
- DA anchor is better for spike and 17_24 periods.
- P3 risk should remain diagnostic/shadow-only until separately validated.

## 5. Registry impact

This PR adds:

- `configs/candidate_registry/realtime_da_sgdf_selector.yaml`

The selector registry is candidate-only and registry-only:

- no main.py changes
- no parser changes
- no pipeline changes
- no final output changes
- no champion replacement
- no submission_ready.csv writes

## 6. Recommendation

`P2_9_RECOMMENDATION: DA_SGDF_SELECTOR_CANDIDATE`

Project interpretation:

- Do not build a SGDFNet-only replacement.
- Do not continue RT916/TimeMixer heavy paths.
- Use DA anchor as primary realtime baseline.
- Use SGDFNet only as an auxiliary model in a conservative selector.
- Next engineering step: default-off selector shadow adapter in 3.0.
