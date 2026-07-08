# EFM3 Fusion v1.1 Finalization Report

## Branch / commit

| Item | Value |
|---|---|
| source branch | `agent/fusion-chain-v1.1-targeted-policy` |
| source finalization commit | `952caff0f32fc66d8d26e6796c0f62570ab67fda` |
| PR type | registry/docs/tests only |

## Code audit

| Check | Result |
|---|---|
| main.py modified | no |
| parser modified | no |
| default-off | pass |
| final write | no |
| submission_ready write | no |
| champion replacement | no |
| target-day leakage | no |
| RT916 / TimeMixer | no |
| oracle isolated | yes |

## v1.1 decision

| Metric | Value |
|---|---:|
| official validation | 25.84 |
| best fusion validation | 25.64 |
| delta | -0.20 |
| DA anchor validation | 25.59 |
| selector validation | 25.99 |
| runtime | 6.7s |
| total tests | 60 passed |

## True improvement source

The improvement source is winter DA anchor policy. The result should be described as a seasonal DA policy router, not a complex model fusion system.

P3 overlay has low effective coverage in the fusion run. Selector is worse than official on validation. DA anchor alone is stronger than the fusion variant.

## Final comparison

| Variant | Overall | Winter | Non-winter | Runtime | Decision |
|---|---:|---:|---:|---:|---|
| official_baseline | 24.22 | 27.37 | 22.62 | 5.3s | baseline |
| da_anchor | 24.02 | 27.07 | 22.44 | 5.3s | best real |
| conservative_fusion_v1 | 24.05 | 27.07 | 22.62 | 5.3s | shadow monitoring ready |
| oracle_upper_bound | 21.67 | 24.20 | 20.28 | 5.3s | analysis only |

2.5 baseline status: `unavailable_or_cached_only`.

## Recommendation

`FUSION_FINAL_RECOMMENDATION: SHADOW_MONITORING_READY`

## Final verdict

`FUSION_FINAL_RESULT: PASS`

## Restrictions

This does not authorize production deployment, champion replacement, final output overwrite, or `submission_ready.csv` writes.
