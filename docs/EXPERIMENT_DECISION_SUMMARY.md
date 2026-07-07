# Experiment Decision Summary

> Final conclusions for every experimental module integrated into main.

---

## P1 Day-Ahead

| Module | Status | Conclusion |
|--------|--------|------------|
| P1.1 cfg05 candidate | `candidate` | cfg05 (14.68%) beats faithful 2.5 (21.87%). Shadow allowed. Champion prohibited. |
| Day-ahead champion | `champion` | Unchanged. No replacement. |

## P2 Realtime

| Module | Status | Conclusion |
|--------|--------|------------|
| P2.8 canonical correction | ✅ Merged | Safe ledger fix. Production compatible. |
| P2.10 DA-SGDF selector | `seasonal_candidate` | Non-winter improvement. Registry only. |
| P2.11 selector shadow | `shadow_only` | Safe observation in non-winter. Winter NO-GO. |
| P2.13 winter policy | `winter_no_go` | **Enforced**: selector forbidden in Nov-Feb. |
| SGDFNet lite | `seasonal_candidate` | Auxiliary model. Not primary. |
| TimesFM | `seasonal_candidate` | Experimental. Not in critical path. |
| RT916 / TimeMixer | ❌ Excluded | Not in online critical path. |

## P3 Extreme Price

| Module | Status | Conclusion |
|--------|--------|------------|
| P3.2 shadow hook | `shadow_only` | Default OFF. Shadow-only. |
| P3.3 winter validation | 120 days tested | Neg -12.80. Spike -0.59. Normal +0.94. |
| P3.4 monitoring registry | `monitoring` | Winter observation only. |

## System

| Module | Status | Conclusion |
|--------|--------|------------|
| P3.5 selector + P3 | `coexistence_safe` | Both shadow modules coexist safely. |

## Realtime Strategy (Agreed)

| Season | Strategy | Rationale |
|--------|----------|-----------|
| Winter (Nov-Feb) | **DA_anchor only** | DA_anchor wins 4/4 months (P2.12). Selector winter NO-GO. |
| Non-winter (Mar-Oct) | **DA-SGDF selector shadow** | Selector can improve. Shadow-only observation. |
| P3 shadow | **Winter monitoring** | Negative improvement significant. Default OFF. |
| SGDFNet | **Auxiliary** | Not a replacement for DA_anchor or fusion. |
| Production champion | **Unchanged** | No experiment replaces the production champion. |

## What is NOT Production

- cfg05 day-ahead (P1.1)
- DA-SGDF selector (P2.10, P2.11)
- Extreme price shadow (P3.2, P3.4)
- All shadow-only and registry-only modules

## What IS Production

- Pre-existing production champion (unchanged)
- Ledger_full pipeline chain (unchanged)
- submission_ready.csv generation (unchanged)
