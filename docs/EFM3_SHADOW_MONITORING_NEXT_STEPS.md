# EFM3 Shadow Monitoring — Next Steps

## Monitoring Cadence

| Frequency | Action | Responsibility |
| --------- | ------ | -------------- |
| **Daily** | Default-off smoke: `python main.py --date TODAY --pipeline ledger_smoke` | CI / cron |
| **Daily** | Verify shadow flags are NOT enabled by default | CI check |
| **Weekly** | Shadow metrics summary: review P3/selector shadow output coverage | Engineer |
| **Monthly** | Winter/non-winter drift check: compare DA anchor vs official by month | Engineer |
| **Quarterly** | Production eligibility review: evaluate all go gates | Lead |

## Go Gates (Production Eligibility)

Only promote to production when ALL conditions are met:

| # | Gate | Criteria | Evidence |
| - | ---- | -------- | -------- |
| 1 | Live monitoring duration | ≥30 consecutive days | Shadow output timestamps |
| 2 | Default-off stability | No shadow contamination in 30 days | CI logs |
| 3 | Final/submission isolation | No stray writes to `final/` or `submission_ready.csv` | File audit |
| 4 | Validation improvement stable | Seasonal DA router still ≥0.15pp vs official on latest data | Monthly computation |
| 5 | Winter DA router stable | DA anchor still beats SGDFNet in winter months | By-month metrics |
| 6 | P3 normal degradation contained | Normal SMAPE not degraded by >0.30pp | Scene metrics |
| 7 | Selector offline | No selector-based switching in winter confirmed | Shadow report audit |
| 8 | 2.5 baseline comparison | Completed and documented | Comparison report |
| 9 | Rollback plan | Documented revert procedure | Plan document |
| 10 | Human review | Reviewed and approved by lead engineer | Sign-off |

## Stop Gates (Immediate Halt)

Stop all monitoring and escalate if ANY of these occur:

| # | Condition | Action |
| - | --------- | ------ |
| 1 | Shadow output appears in `outputs/final/` | Stop monitoring, audit pipeline, fix |
| 2 | `submission_ready.csv` modified by shadow | Stop, audit, fix |
| 3 | `champion` registry overwritten | Stop, revert, audit |
| 4 | `exit_code` changed by shadow hook | Stop, audit isolation |
| 5 | `delivery_status` changed by shadow hook | Stop, audit isolation |
| 6 | Target-day actual leakage detected | Stop, audit, fix immediately |
| 7 | Normal SMAPE degradation >0.50pp (sustained 7 days) | Pause all P3 overlays |
| 8 | Winter DA anchor policy fails to improve | Revert to official baseline |

## Data to Collect Weekly

- Shadow run success rate (P3, selector) — per day
- Correction applied count (P3) — per day
- Confidence distribution (P3, selector) — histogram
- Coverage rate (% hours with predictions) — per variant
- Runtime — per shadow invocation
- Leakage check — per invocation (automated)

## Quarterly Review Template

```markdown
## Quarterly Shadow Monitoring Review
- Period: YYYY-QQ
- Days monitored: XX
- P3 corrections applied: XX (XX%)
- Selector switches: XX (XX% of monitored hours)
- Winter DA anchor improvement: XX pp
- Normal degradation: XX pp
- Leakage incidents: 0 / XX
- Contamination incidents: 0 / XX
- Recommendation: CONTINUE_MONITORING / CONSIDER_PRODUCTION / STOP
```

## Architecture Reminder

```
Main pipeline (ledger_full)
  ├── Predict → Weight → Fuse → Classifier → Final
  ├── submission_ready.csv (production only)
  │
  ├── extreme_price_shadow (--enable-extreme-price-shadow)  → shadow dir
  ├── realtime_da_sgdf_selector (--enable-realtime-da-sgdf-selector-shadow) → shadow dir
  │
  └── Fusion v1.1 registry only — no runtime on main
```

The three shadow lines are independent, default-off, and never write to
`final/`, `submission_ready.csv`, or champion registry.
