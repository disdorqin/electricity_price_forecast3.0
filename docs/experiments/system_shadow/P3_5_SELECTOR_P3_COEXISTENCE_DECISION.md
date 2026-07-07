# P3.5 Selector + P3 Coexistence Decision

## 1. Summary

P3.5 validates whether the two current shadow modules can coexist on the current 3.0 codebase:

- `realtime_da_sgdf_selector_shadow`
- `extreme_price_shadow`

Scope:

- 20 winter dates
- 480 total hours
- 5 dates per winter month
- mode: `replay_from_ledger`
- test branch: `test/p3.5-coexistence`

Decision:

- `P3_5_RECOMMENDATION: COEXISTENCE_SAFE`
- `P3_5_RESULT: PASS`

This means both modules can be monitored together as default-off shadows. It does not mean production replacement or champion promotion.

## 2. Shadow execution

| Module | Result | Notes |
|---|---:|---|
| P3 extreme_price_shadow | 6/20 OK, 14 degraded | Degraded dates lacked enough history for classifier training |
| Selector shadow | 20/20 complete | DA anchor and SGDFNet predictions available |
| Both simultaneous | safe | Separate dirs, no shared state, no runtime conflict |

## 3. Contamination audit

| Check | Result |
|---|---|
| `submission_ready.csv` written | 0/20 |
| `final/` written | 0/20 |
| exit_code affected | no |
| delivery_status changed | no |
| default-off verified | pass |

## 4. Overlap analysis

| Metric | Value |
|---|---:|
| Selector SGDFNet hours | 14 / 480 |
| Selector DA hours | 466 / 480 |
| P3 corrected hours | 30 |
| Overlap hours | 0 |
| Conflict hours | 0 |

There were no observed overlap hours. This is expected for the sampled winter dates because the selector stays DA-dominant in winter, while P3 applies corrections mainly in late January and February.

## 5. Interpretation

The result supports shadow monitoring coexistence. It does not support production promotion.

Important limits:

- The test branch was not pushed to remote.
- The mode was `replay_from_ledger`, not formal delivery production.
- P3 degraded on 14/20 dates due to insufficient history.
- Since overlap hours were zero, same-hour dual-action conflict remains theoretically low risk but not empirically observed.

## 6. Project decision

Allowed:

- Run both shadows together for diagnostic monitoring.
- Include both in shadow monitoring reports.
- Keep both default-off.

Forbidden:

- Production replacement.
- Champion promotion.
- Writing either result to `submission_ready.csv`.
- Final output overwrite.

## 7. Next step

Run 3.0 Post-Merge Validation v1 on current main. If it passes, create the final Shadow Monitoring Pack and stop/go gates.
