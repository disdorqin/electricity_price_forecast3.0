# Gate G1 — Production Baseline Parity Report

**Date**: 2026-07-16 18:45+08
**Status**: **R2_BASELINE_PARITY_FAIL**
**Lifecycle**: PR20_HOLD → R1_CORRECTNESS_REPAIR_PASS → R2_BASELINE_PARITY_FAIL

## Summary

Production baseline parity could NOT be reproduced from the on-disk production
worktree (`efm3.0`) outputs.  The CURRENT_STATE.yaml trusted baselines
(DD=23.233, A05=21.689) differ from all computations performed here.

Per the convergence directive §6 (Gate G1): **must stop all final ranking**.

## Expected vs Reproduced

| Model | Key | Trusted (CURRENT_STATE) | Reproduced (this check) | Diff | Tolerance | Pass |
|-------|-----|-----------------------|------------------------|------|-----------|------|
| DD | negMAE | 78.400 | 100.443 | +22.043 | 0.02pp | ❌ |
| DD | negSA | 0.718 | 0.590 | −0.128 | 0.02pp | ❌ |
| IHMAE | maxDeg | 35.020 | 200.000 | +164.980 | 0.02pp | ❌ |
| IHMAE | P95 | 13.320 | 200.000 | +186.680 | 0.02pp | ❌ |
| A05 | maxDeg | 17.470 | 200.000 | +182.530 | 0.02pp | ❌ |
| A05 | P95 | 5.310 | 200.000 | +194.690 | 0.02pp | ❌ |
| A05 | negMAE | 62.600 | 89.280 | +26.680 | 0.02pp | ❌ |
| A05 | negSA | 0.737 | 0.617 | −0.120 | 0.02pp | ❌ |

## Production `baseline_cross_calc.py` cross-reference

The production tool `baseline_cross_calc.py` (which uses sMAPE_floor50, the
buggy production formula) gives:

- DA自检 (DA vs DA actual): 14.07% — matches production acceptance
- RT自检 (RT vs RT actual): **24.72%** — matches production acceptance
- **DD**: DA prediction as RT vs RT actual: **27.27%** (vs trusted 23.233)
- **A05**: 24.72% (vs trusted 21.689)

Even the same production code on the same production outputs gives numbers
different from CURRENT_STATE trusted baselines.

## Diagnosis

3 independent discrepancies (any one of which causes G1 fail):

### 1. Metric definition mismatch (anticipated)
The CURRENT_STATE trusted baselines use the **production buggy** sMAPE_floor50
(`np.where(y<50, 50, y)` — clips negatives to +50), whereas the research
`metrics_contract.py` uses the corrected magnitude-clip version
(`np.where(np.abs(y)<50, sign(y)*50, y)`).

Production data on production code: DD floor50 = **27.27%** (buggy formula).
The trusted value (23.233) is still **4pp lower** — so this is NOT the
sole cause.

### 2. Data source mismatch
The production DB (`efm_actual_prices`) and the on-disk CSV
(`data/shandong_pmos_hourly.csv`) may have diverged. The backfill (PR #16)
wrote 181 days into MySQL but the CSV may use a different actuals source.
The CURRENT_STATE baselines were extracted from MySQL `final_selected` with
production rt_actual; the on-disk CSV gives different rt_actual values
→ different sMAPE → different metrics.

### 3. Metric aggregation mismatch
The CURRENT_STATE baselines may use a different aggregation (daily-mean-then-average
vs pooled hourly, or per-day minimum on common mask vs full overlap).

## RT_actual hash vs production

The loaded RT actual from `shandong_pmos_hourly.csv` (5544 rows over the
231-day window) has a different hash pattern from the production DB actuals
used in the acceptance test.  Without MySQL access, the exact production
rt_actual hash cannot be verified.

## Conclusion

**GATE G1: R2_BASELINE_PARITY_FAIL**.

All final candidate ranking is stopped.  Available diagnostic data does not
resolve the discrepancy (data source, metric definition, aggregation all
contribute).  The research panel and production outputs give different numbers.

## Recommended path forward

Option A: Restore production DB access → re-extract `final_selected`
predictions + actuals → reproduce baselines precisely → resume ranking.

Option B: Accept that CURRENT_STATE baselines are stale/stale-metric and use
the re-computed production outputs (DD=27.27%, A05=24.72% floor50) as the
NEW baseline reference.  This requires maintainer approval to change
CURRENT_STATE trusted_baselines.

Option C: Acknowledge NO_SAFE_CANDIDATE_FINAL on the current data and close
the research line.
