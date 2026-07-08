# Fusion v1.1 Seasonal DA Policy Router

## Policy definition

Fusion v1.1 is a seasonal DA policy router.

```python
if month in (11, 12, 1, 2):
    prediction = da_anchor
else:
    prediction = official_baseline
```

## Why this policy

Validation results show that the winter DA anchor is the only reliable source of the v1.1 improvement.

- DA anchor validation: 25.59
- Conservative fusion validation: 25.64
- Official validation: 25.84
- Selector validation: 25.99

The selector does not drive the improvement. P3 correction is important for diagnostic negative-price monitoring, but its overlay effect in this fusion run is low because 87% of hours are unchanged from the original prediction.

## Operational role

Allowed:

- shadow monitoring
- default-off diagnostic routing
- comparison against official baseline

Forbidden:

- production deployment
- champion replacement
- `submission_ready.csv` write
- final output overwrite
- selector winter promotion
- SGDFNet-only replacement
- P3 normal correction in production

## Relationship to existing modules

- P2.13 already established winter DA-only policy for realtime selector.
- P3.4 supports winter negative-price shadow monitoring, not production overlay.
- P3.5 confirmed selector/P3 coexistence is safe under shadow monitoring.

Fusion v1.1 is therefore a documentation and monitoring policy, not a new online model stack.
