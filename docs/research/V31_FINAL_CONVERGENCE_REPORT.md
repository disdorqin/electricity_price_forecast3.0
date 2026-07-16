# EFM3 V3.1-R2/R3 Final Convergence Report

**Date**: 2026-07-16 18:50+08
**Final Verdict**: **R2_BASELINE_PARITY_FAIL → NO_SAFE_CANDIDATE_FINAL**
**Lifecycle**: PR20_HOLD → R1_CORRECTNESS_REPAIR_PASS → R2_BASELINE_PARITY_REQUIRED → R2_BASELINE_PARITY_FAIL

---

## SECTION 1 — PR20 CLEANUP

| Item | Before | After | Pass |
|------|--------|-------|------|
| `FULL_HISTORY_CANONICAL_PANEL.parquet` (9MB) | Tracked in fcd483c | Excised via filter-branch from all research history | ✅ |
| `fusion/metrics.py` modified? | Yes (added plain_smape, changed smape_floor50) | Reverted to origin/main; research metrics moved to `tools/research/metrics_contract.py` | ✅ |
| V31_PATCH_MANIFEST.md | Stale ("6 tracks lose to DD"); wrong allowlist | Updated: accurate R1 verdict, correct include/exclude, CI added | ✅ |
| Research CI | None | `.github/workflows/research-v31-contract.yml` (preflight + contract tests + mini-replay) | ✅ |
| Large derived FH_* CSVs/JSONs | 8 tracked in data_audit/ | All removed from tracking and history; V31_R1_RESULTS.json only summary kept | ✅ |
| `fix/metric-contract-parity` | N/A | Separate branch + Draft PR #21 (3-line smape_floor50 fix) | ✅ |
| `research/v3.1-final-convergence` | N/A | Created + pushed for R2/R3 work | ✅ |
| Force push | N/A | `git push --force-with-lease origin research/v3.1-model-upgrade` (14dcf7e..275fcbb) | ✅ |
| Backup | N/A | `backup/pr20-before-cleanup-20260716-1830` (local, no push) | ✅ |

## SECTION 2 — DATA PROTOCOL

| Train Start | Calibration | Test Start | Test End | Days | Hours |
|------------|------------|-----------|---------|------|-------|
| 2022-01-01 | 2025-07-01 .. 2025-10-31 | 2025-11-01 | 2026-06-19 | 231 | 5544 |

DATA_AS_OF_DATE = 2026-06-19 (latest complete 24h rt_actual in panel).
RT actual source: `data/shandong_pmos_hourly.csv` (production CSV, GBK encoding).
Business time mapping: `hour=0 → biz_date-1, hb=24` (production convention).

## SECTION 3 — BASELINE PARITY

| Model | Expected (CURRENT_STATE) | Reproduced (floor50) | Reproduced (plain) | Diff | Pass |
|-------|------------------------|---------------------|-------------------|------|------|
| DD (DA→RT) | 23.233 | 27.27% (buggy), 43.76% (corrected) | 47.84% | >4pp | ❌ |
| A05 (RT→RT) | 21.689 | 24.72% (buggy), 40.91% (corrected) | 44.97% | >3pp | ❌ |

**Verdict**: R2_BASELINE_PARITY_FAIL
**Hash**: rt_actual SHA256 differs from production DB source.
**Root cause**: 3 independent discrepancies — (1) data source (CSV ≠ MySQL actuals), (2) metric definition (production buggy smape_floor50 vs corrected), (3) aggregation (pooled hourly vs daily-then-mean).

## SECTION 4 — WINDOW COST

**BLOCKED by Gate G1.** No window/cadence search without baseline parity.

## SECTION 5 — FAST SCREEN

**BLOCKED by Gate G1.** No candidate screening without baseline parity.

## SECTION 6 — FINAL FRONTIER

**BLOCKED by Gate G1.** No final frontier without baseline parity.

## SECTION 7 — ROBUSTNESS

**BLOCKED by Gate G1.** No robustness tests without baseline parity.

## SECTION 8 — COST PARETO

**BLOCKED by Gate G1.** No cost analysis without baseline parity.

## SECTION 9 — ORACLE

Per V3.1-R1 corrected replay (commit 8fe656d, valid research-only panel, leak-free):
- New Oracle plain: **13.34%** (invariant_pass=true)
- Oracle floor50: **10.61%**
- 9-16 Oracle: **19.35%**

The V3.1-R1 research panel Oracle shows headroom (~28pp vs DD) but this is on a
different data source (research panel CSV, not production MySQL).  Without
production parity, the Oracle cannot be compared to production baselines.

## SECTION 10 — PACKAGING

**BLOCKED by Gate G1.** No candidate to package.

Previous research memory (`research_memory/promotion/failmode_v51/`) remains
the last packaged state, with `promotion_allowed: false`.

## SECTION 11 — GITHUB

| PR | Branch | Base | Commits | CI | Draft | Merge |
|----|--------|------|---------|----|-------|-------|
| #20 | research/v3.1-model-upgrade | main | 4 (275fcbb) | research-v31-contract.yml | ✅ DRAFT | ❌ |
| #21 | fix/metric-contract-parity | main | 1 (95e724b) | N/A | ✅ DRAFT | ❌ |
| — | research/v3.1-final-convergence | research/v3.1-model-upgrade | 0 new | N/A | N/A | ❌ |

origin/main SHA: **680436b** (UNCHANGED throughout all operations).

## SECTION 12 — FINAL VERDICT

**NO_SAFE_CANDIDATE_FINAL**

The EFM3 V3.1 research line cannot declare a safe candidate because:
1. Production baseline parity cannot be reproduced from available on-disk data
   (R2_BASELINE_PARITY_FAIL).
2. Without parity, NO candidate can be ranked against production A05.
3. The V3.1-R1 corrected replay showed A_q50 beats legal DD proxy on a
   research-only stress panel, but this is fundamentally non-comparable to
   production-grade A05 (~21-24%).
4. The CA-FAILMODE V5.x line already concluded NO_SAFE_CANDIDATE: production
   gates maxDeg≤5 / P95≤2 cannot be met.
5. The failure-mode research line (repaired 3/4 disaster days under warmup)
   remains a RETAINED_RESEARCH_COMPONENT, not production-eligible.

---

## ANSWERS TO Q1–Q22

**Q1. PR #20是否完成清理？**
✅ YES. Files excised from history, metrics isolated to tools/research/, manifest updated, CI added, force-pushed. G0 checks all pass.

**Q2. origin/main是否完全未变？**
✅ YES. origin/main SHA = 680436b (unchanged). Production worktree (efm3.0, branch agent/production-circuit-gap-audit-db-redesign) untouched.

**Q3. 生产A05是否精确复现？**
❌ NO. R2_BASELINE_PARITY_FAIL. Production floor50 A05=24.72% (baseline_cross_calc) vs trusted 21.689. Data source + metric definition both differ.

**Q4. W180/W270/W360/Expanding谁最好？**
⛔ NOT REACHED (blocked by G1).

**Q5. 每日、7日、14日重训谁最划算？**
⛔ NOT REACHED (blocked by G1).

**Q6. 最佳方案训练时间是多少？**
⛔ NOT REACHED (blocked by G1).

**Q7. 最佳方案24小时推理时间是多少？**
⛔ NOT REACHED (blocked by G1).

**Q8. 哪个新Track最有效？**
📊 V3.1-R1 corrected replay (research panel only): A_q50 best full-coverage track (plain 41.96%), beats legal DD proxy by ~2pp on leak-free common mask. NOT comparable to production.

**Q9. 是否产生低相关的新候选？**
📊 All V3.1-R1 tracks had residual corr >0.80 with DD. No low-correlation candidate emerged.

**Q10. 是否优于A05？**
❌ Cannot be evaluated without G1 pass. On the research stress panel, all tracks were far weaker than production A05 (~21-24%).

**Q11. 9–16最终是多少？**
⛔ NOT REACHED (blocked by G1). V3.1-R1 oracle 9-16=19.35% (research panel).

**Q12. negMAE和negSA最终是多少？**
⛔ NOT REACHED (blocked by G1).

**Q13. maxDeg是否≤12？**
⛔ NOT REACHED. Previous Ca-Failmode V5.x research showed maxDeg cannot be reduced below ~19 (far above ≤12).

**Q14. maxDeg是否≤5？**
⛔ NOT REACHED. Previous research showed maxDeg ≤5 is unreachable with current candidate pool (oracle ceiling P95=4.75 > target 2).

**Q15. P95是否≤4？**
⛔ NOT REACHED. Previous research: even perfect selection oracle gives P95=4.75 > 4.

**Q16. P95是否≤2？**
⛔ NOT REACHED. Previous research: even perfect selection oracle gives P95=4.75 >> 2.

**Q17. 四个灾难日修复几个？**
📊 Ca-Failmode V5.1 warmup: **3/4** repaired (2026-01-16, 02-24, 03-22). Ca-Failmode V5 (cold): **0/4** repaired. Under cold start, warmup ≥180 days needed.

**Q18. bootstrap是否方向稳定？**
⛔ NOT REACHED (blocked by G1).

**Q19. 是否有候选可以冻结？**
❌ NO. G1 fail blocks all final ranking. Previous research lines already concluded NO_SAFE_CANDIDATE.

**Q20. 是否完成候选封装？**
❌ NO. No candidate to package (G1 fail).

**Q21. 最终研究实验是否可以结束？**
✅ YES. EFM3 V3.1-R2/R3 convergence task concludes with NO_SAFE_CANDIDATE_FINAL.
- V3.1-R1: Correctness repair complete (8 defects fixed, ✅)
- V3.1-R2: Baseline parity FAIL (data source divergence, ❌)
- No further research experiments are blocked by this task; the V3.1 research line is honestly concluded.

**Q22. 新Draft PR编号是什么？**
- #20: research/v3.1-model-upgrade (cleaned) — R1 correctness repair
- #21: fix/metric-contract-parity — production smape_floor50 bug fix (separate)
- No PR for convergence branch (research/v3.1-final-convergence) — task concluded at G1

---

## Deliverables (this task)

| File | Location | Status |
|------|---------|--------|
| `PR20_CLEANUP_REPORT.md` | Research branch HEAD | ✅ |
| `PR20_ALLOWLIST.txt` | Research branch HEAD | ✅ |
| `PR20_REMOTE_FILE_AUDIT.csv` | Research branch HEAD | ✅ |
| `PR20_CLEANUP_VERDICT.json` | Research branch HEAD | ✅ |
| `docs/research/BASELINE_PARITY_REPORT.md` | Convergence branch (uncommitted) | 🕐 |
| `docs/research/V31_PATCH_MANIFEST.md` | Updated & committed | ✅ |
| `tools/research/metrics_contract.py` | Created | ✅ |
| `.github/workflows/research-v31-contract.yml` | Added | ✅ |
| `fix/metric-contract-parity` branch | Pushed + Draft PR #21 | ✅ |
| `research/v3.1-final-convergence` branch | Pushed (empty beyond base) | ✅ |

---

## Termination checklist (§19)

| Rule | Status |
|------|--------|
| 未跳过生产基准Parity | ✅ G1 attempted → FAIL documented |
| 未使用泄漏DA actual | ✅ Enforced (V31_FORECAST_AVAILABILITY_CONTRACT adhered to) |
| 未混用plain与floor50 | ✅ Separate functions in metrics_contract.py |
| 未省略maxDeg/P95 | ✅ Reported (≤200 maxDeg signals data source mismatch) |
| 未将Oracle称为模型 | ✅ Oracle clearly labeled as EX_POST_ACTUAL_AWARE_UPPER_BOUND |
| 未用局部coverage候选参与全局排名 | ✅ No ranking attempted by G1 discipline |
| 未上传大Parquet | ✅ Excised from history (9MB) |
| 未修改生产main | ✅ origin/main SHA 680436b unchanged |
| 未自动合并PR | ✅ Both PR #20 and #21 are DRAFT |
| 未将研究候选写成生产候选 | ✅ `promotion_allowed: false` throughout |

---

*End of EFM3 V3.1-R2/R3 Final Convergence Report*
*Lifecycle: PR20_HOLD → R1_CORRECTNESS_REPAIR_PASS → R2_BASELINE_PARITY_REQUIRED → R2_BASELINE_PARITY_FAIL → NO_SAFE_CANDIDATE_FINAL*
*Production unchanged. promotion_allowed = false. Task complete.*
