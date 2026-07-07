# P3.3 Winter Extreme Price Shadow Monitoring Decision

## 1. Summary

P3.3 tested the `extreme_price_shadow` controlled shadow over four winter hard-window months:

- 2025-11
- 2025-12
- 2026-01
- 2026-02

Scope:

- 120 winter days
- mode: `replay_from_ledger`
- actual available: 120/120

Decision:

- `P3_3_RECOMMENDATION: WINTER_SHADOW_MONITORING_READY`
- `P3_3_RESULT: PASS`

This means P3 may enter default-off winter shadow monitoring. It does not mean production replacement, champion promotion, or submission output use.

## 2. Monthly metrics

| Month | Original | Corrected | Delta | Negative delta | Spike delta | Normal delta |
|---|---:|---:|---:|---:|---:|---:|
| 2025-11 | 27.13 | 26.47 | -0.66 | -8.47 | -2.36 | +2.03 |
| 2025-12 | 33.32 | 32.16 | -1.16 | -9.15 | 0.00 | +0.45 |
| 2026-01 | 45.20 | 43.21 | -1.99 | -10.50 | 0.00 | +0.93 |
| 2026-02 | 65.99 | 56.44 | -9.55 | -23.07 | 0.00 | +0.34 |

## 3. Scene-level conclusion

| Scene | Original | Corrected | Delta | Interpretation |
|---|---:|---:|---:|---|
| Negative | 88.36 | 75.56 | -12.80 | strong benefit |
| Spike | 35.81 | 35.22 | -0.59 | slight benefit |
| Normal | 32.91 | 33.85 | +0.94 | acceptable under shadow only |

P3 has its clearest value in negative-price hours. Spike benefit is weak but non-negative. Normal hours are slightly harmed, which blocks production promotion but is acceptable for default-off monitoring.

## 4. Correction behavior

| Metric | Value |
|---|---:|
| Applied corrections | 466 |
| Rollbacks | 0 |
| Cap hits | 0 |
| False positives | 166 |
| False positive rate | 35.6% |
| Missed negative hours | 58 |
| Missed spike hours | 23 |

The false positive rate is high enough to require continued monitoring and threshold tuning. No cap-hit or rollback anomaly was observed.

## 5. Safety

P3.3 reported:

- default-off verified
- no final output contamination
- no `submission_ready.csv` writes
- no champion replacement
- no delivery status modification
- no exit code modification

## 6. Known limits

- The run mode was `replay_from_ledger`, not full production chain.
- Selector + P3 coexistence was not tested because the selector module was unavailable on the branch used by the P3.3 run.
- The source commit for this P3.3 artifact package was not provided in the report and should be backfilled later.
- Normal hours worsen slightly and false positives are material, so production promotion is blocked.

## 7. Project decision

Allowed:

- default-off winter shadow monitoring
- manual diagnostic runs
- threshold tuning experiments in shadow mode

Forbidden:

- production replacement
- champion promotion
- final output overwrite
- `submission_ready.csv` writes
- default enablement

## 8. Next required task

Run P3.4 selector + P3 coexistence validation on the current 3.0 main, because P2.13 and P2.11 are now merged and the previous P3.3 run did not test overlap/conflict with the selector shadow module.
