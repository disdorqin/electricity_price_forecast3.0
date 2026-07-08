# EFM3 Final Release Notes

## Status

| Check | Result |
| ----- | ------ |
| **FINAL_SEAL_RECOMMENDATION** | **READY_FOR_SHADOW_MONITORING** |
| **FINAL_SEAL_RESULT** | **PASS** |
| Main SHA | `15bd7af` |
| Final tests | 105 PASS (12 groups) |
| Registry audit | 7/7 compliant |
| Default-off smoke | PASS (3 days) |
| Explicit shadow smoke | PASS (2 days) |
| Safety grep | 0 violations |

## Main Modules

| Module | Final Status |
| ----------------------- | -------------------------- |
| P1 Day-ahead shadow registry | Shadow registry |
| P2 Realtime selector shadow | Seasonal / Diagnostic only |
| P2 Winter no-go policy | DA-only default |
| P3 Extreme price shadow | Winter shadow monitoring |
| P3 Selector coexistence registry | Safe |
| Fusion v1.1 | Seasonal DA Policy Router (shadow monitoring) |

## Key Finding

Fusion v1.1 is not a complex fusion engine. It is a **Seasonal DA Policy Router**:

```python
if month in (11, 12, 1, 2):
    use_da_anchor()
else:
    use_official_baseline()
```

Every fusion variant evaluated ultimately converges to this same simple rule.
The P3 extreme shadow and selector overlays do not contribute measurable
improvement at current production thresholds.

## Safety Guarantees

| Safeguard | Status |
| --------- | ------ |
| Default-off | ✅ All shadow flags default to `False` |
| No production replacement | ✅ All registries enforce `production_replacement_allowed: false` |
| No champion replacement | ✅ All registries enforce `champion_allowed: false` |
| No final overwrite | ✅ `modifies_final_outputs: false` across all registries |
| No submission_ready write | ✅ `writes_submission_ready: false` across all registries |
| No target-day actual leakage | ✅ `y_true` never used as feature in any policy builder |
| No RT916/TimeMixer online dependency | ✅ Not imported in any shadow pipeline |

## Metrics Summary

| Metric | Value | Note |
| ------ | ----- | ---- |
| Official validation SMAPE | 25.84 | SGDFNet baseline |
| Fusion v1.1 validation SMAPE | 25.64 | Conservative_fusion_v1 |
| Delta vs official | **-0.20pp** | Meets threshold — marginal |
| DA anchor validation SMAPE | **25.59** | Stronger than fusion |
| Selector validation SMAPE | 25.99 | Worse than official |
| Fusion v1.1 runtime | 6.7s | Full 14 months, 12 variants |
| Final Seal tests | **105 PASS** | 12 groups |
| Fusion test suite | **60 PASS** | v1 + v1.1 + registry + docs |

## Conclusion

The EFM3 main branch has completed all evaluations and is ready for
shadow monitoring. No experimental line should be promoted to production
without passing the shadow monitoring go gates defined in
`EFM3_SHADOW_MONITORING_NEXT_STEPS.md`.
