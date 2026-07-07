# P2.12 Winter Hard-Window Shadow Stress Decision

## 1. Summary

P2.12 tested the realtime DA-SGDF selector over the winter hard-window months:

- 2025-11
- 2025-12
- 2026-01
- 2026-02

A total of 120 winter days were tested. Actuals were available. The source experiment is:

- Repository: `disdorqin/electricity_forecast_deep_sgdf_delta`
- Commit: `b8360647b14757443121b389af719249c78e80cd`
- Package: `exports/efm3_candidates/realtime_winter_shadow/p2_12_winter_stress`

Result:

- `P2_12_RECOMMENDATION: WINTER_NO_GO`
- `P2_12_RESULT: PASS`

This is a valid completed stress test. The no-go means the selector should not be promoted for winter use; it does not mean the experiment failed.

## 2. Winter metrics

| Month | DA anchor | SGDFNet | Selector shadow | Winner |
|---|---:|---:|---:|---|
| 2025-11 | 15.86 | 17.34 | 15.63 | DA anchor |
| 2025-12 | 22.84 | 24.29 | 22.28 | DA anchor |
| 2026-01 | 31.98 | 34.05 | 32.41 | DA anchor |
| 2026-02 | 26.70 | 27.63 | 26.55 | DA anchor |

DA anchor wins all four winter months. SGDFNet wins 0/4 months.

## 3. Selector behavior

| Month | DA hours | SGDFNet hours | SGDFNet % |
|---|---:|---:|---:|
| 2025-11 | 706 | 14 | 1.9% |
| 2025-12 | 725 | 19 | 2.6% |
| 2026-01 | 705 | 39 | 5.2% |
| 2026-02 | 658 | 14 | 2.1% |

The selector is safe and conservative. It does not over-switch to SGDFNet. However, winter metrics show DA anchor remains the better default, so selector use in winter should be diagnostic-only.

## 4. Safety result

P2.12 confirmed:

- no final output writes
- no submission_ready.csv writes
- no champion replacement
- no delivery_status modification
- no exit_code modification
- selector switching rate remains low

## 5. P3 note

P3 shadow was not available in the P2.12 simulation environment. Therefore P2.12 does not validate P3 winter behavior. P3 winter full-chain validation remains a separate required task.

## 6. Project decision

Policy after P2.12:

- Winter months 11/12/1/2: DA-only default.
- Realtime DA-SGDF selector: diagnostic-only in winter.
- Non-winter: selector may remain default-off shadow candidate.
- No production/champion promotion.
- No SGDFNet-only replacement.
- No RT916/TimeMixer online critical path.

## 7. Next required task

Run P3 winter extreme shadow full-chain validation separately, because P3 was not active in this simulation.
