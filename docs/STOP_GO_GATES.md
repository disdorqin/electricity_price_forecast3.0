# Shadow Monitoring — Stop / Go Gates

> Formal entry and exit criteria for shadow module monitoring.

---

## GO: Enter Shadow Monitoring

All conditions must be met before shadow monitoring can begin:

| # | Condition | Check | Module |
|---|-----------|-------|--------|
| 1 | Post-Merge Validation PASS | ✅ Verified (v1: b958c89) | System |
| 2 | Default-off: no shadow output without flags | ✅ Triple-verified | P3, P2.11 |
| 3 | Explicit enable: shadow writes only to shadow dir | ✅ Triple-verified | P3, P2.11 |
| 4 | submission_ready.csv never written by shadow | ✅ Architecture + test verified | All |
| 5 | final/ never written by shadow | ✅ Architecture + test verified | All |
| 6 | exit_code unchanged by shadow | ✅ try/except verified | P3, P2.11 |
| 7 | delivery_status unchanged by shadow | ✅ Post-dispatch execution | P3, P2.11 |
| 8 | Registry tests pass (or known failures documented) | ✅ 83/86 pass (3 known) | All |
| 9 | Winter no-go policy active | ✅ Nov-Feb DA-only enforced | P2.13 |
| 10 | Coexistence validated | ✅ P3.5 PASS | P3 + P2.11 |

**Current status: ALL GO conditions met.**

---

## STOP: Exit Shadow Monitoring

Any single condition triggers immediate STOP:

### Critical (Immediate Stop)

| # | Condition | Threshold | Action |
|---|-----------|-----------|--------|
| S1 | Default run generates shadow output | Any shadow dir without flag | Emergency fix required |
| S2 | Shadow writes to `final/` | Any file | Emergency fix required |
| S3 | Shadow writes `submission_ready.csv` | Any file | Emergency fix required |
| S4 | Shadow changes `exit_code` | Non-zero from shadow failure | Emergency fix required |
| S5 | Shadow changes `delivery_status` | Delivery status reflects shadow | Emergency fix required |

### Warning (Investigate, May Stop)

| # | Condition | Threshold | Action |
|---|-----------|-----------|--------|
| W1 | P3 normal degradation | delta < -2.0 sMAPE (monthly average) | Review P3 config |
| W2 | P3 false positive rate | > 50% (monthly average) | Tighten classifier threshold |
| W3 | Selector winter SGDFNet share | > 10% in Nov-Feb | Review winter policy |
| W4 | P3 negative improvement lost | delta < -2.0 sMAPE (monthly) | Retrain classifiers |
| W5 | Rollback rate surge | > 5% of applied corrections | Investigate guard logic |
| W6 | Cap-hit rate surge | > 1% of applied corrections | Review CAP_ABS setting |

---

## Monitoring Dashboard Commands

```bash
# Check P3 shadow output for a date
python -c "import pandas as pd; df=pd.read_csv('outputs/runs/Y-m-d/extreme_price_shadow/shadow_predictions.csv'); print('applied:', df['applied'].sum(), '| caps:', df['correction_amount'].abs().ge(350).sum())"

# Check selector output for a date
python -c "import pandas as pd; df=pd.read_csv('outputs/runs/Y-m-d/realtime_da_sgdf_selector_shadow/selector_shadow_predictions.csv'); print('DA hours:', (df['selected_model']=='DA_anchor').sum(), '| SGD hours:', (df['selected_model']=='SGDFNet').sum())"

# Quick contamination check
test -f outputs/runs/Y-m-d/submission_ready.csv && echo "CONTAMINATED" || echo "clean"
```

## Suggested Monitoring Cadence

| Interval | Action |
|----------|--------|
| Daily | Check shadow dirs exist with expected names (manual, first week) |
| Weekly | Quick contamination check (S1-S5) |
| Monthly | Review W1-W6 thresholds, compare to baseline |
| Quarterly | Full re-validation (all tests, default-off check) |

## Escalation

| Issue | Contact |
|-------|---------|
| Final contamination (S1-S5) | Stop immediately, revert last PR |
| Performance drift (W1-W6) | Open issue, schedule review |
| New model integration | Must pass full shadow coexistence validation |
