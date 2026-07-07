# Failure Cases
## 3 Pytest Failures (Known, No Runtime Impact)
1. seasonal_candidate vs candidate assertion (2 tests)
2. baseline_smape_floor50 KeyError (1 test)
Root cause: P2.8 registry YAML update not synchronized with test assertions.
Impact: NONE — YAML content is correct.

## Shadow Behavior (All Expected)
- P3 external-ledger dates: degraded (correct)
- SEL no-DA-anchor dates: FAILED_NO_DA (correct)
- SEL DA-only dates: COMPLETE (correct)
