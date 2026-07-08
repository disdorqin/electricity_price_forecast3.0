# Fusion v1.1 Acceptance Decision

## Summary

Fusion v1.1 reaches `SHADOW_MONITORING_READY` with a 0.20pp validation improvement over official baseline: 25.84 to 25.64.

This meets the threshold, but it is a borderline result and must not be overstated.

## Key metrics

| Metric | Value | Interpretation |
|---|---:|---|
| Official baseline validation | 25.84 | baseline |
| Conservative fusion v1 validation | 25.64 | -0.20pp |
| DA anchor validation | 25.59 | stronger than fusion |
| Realtime selector validation | 25.99 | worse than official |
| P3 identical-to-original hours | 87% | overlay rarely changes output |
| Runtime | 6.7s | lightweight replay |

## Honest assessment

Fusion v1.1 is better understood as a seasonal DA policy router rather than a complex fusion system.

The true improvement source is the winter DA anchor policy. P3 and selector overlays do not materially drive the gain in this run.

## Decision

`FUSION_V1_1_RECOMMENDATION: SHADOW_MONITORING_READY`

`FUSION_V1_1_RESULT: PASS`

Allowed:

- default-off shadow monitoring
- documentation of seasonal DA router behavior
- diagnostic tracking of P3 and selector in parallel

Forbidden:

- production replacement
- champion promotion
- final output overwrite
- `submission_ready.csv` write
- claiming complex fusion success
