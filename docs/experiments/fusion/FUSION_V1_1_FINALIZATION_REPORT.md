# EFM3 Fusion v1.1 Finalization Report

## 1. Branch / PR

| Item    | Value |
| ------- | ----- |
| branch  | `agent/fusion-chain-v1.1-targeted-policy` |
| commit  | `421ed8234f46eea657bcf340429f96b2e96a45f6` |
| PR      | Not created — registry-only / docs-only / tests-only |
| PR type | **Registry-only + docs + tests** (no runtime pipeline changes to main) |

## 2. Code Audit

| Check | Result |
| ------------------------- | ------ |
| default-off | ✅ PASS — config `fusion.enabled: false` |
| no final write | ✅ PASS — no `outputs/final/` writes |
| no submission_ready write | ✅ PASS — no `submission_ready.csv` generation |
| no champion replacement | ✅ PASS — no champion registry modification |
| no target-day leakage | ✅ PASS — `y_true` not used in any v1.1 policy builder |
| no RT916/TimeMixer | ✅ PASS — not imported, not loaded |
| oracle isolated | ✅ PASS — `oracle_upper_bound` labeled `ANALYSIS_ONLY` |
| main.py not modified | ✅ PASS — 0 lines changed by v1.1 |
| cli/parser.py not modified | ✅ PASS — 0 lines changed by v1.1 |
| no outputs/runs/data/models | ✅ PASS — gitignored |
| no `git add -A` | ✅ PASS — targeted `git add` only |

## 3. v1.1 Decision

| Metric | Value |
| ---------------------- | --------- |
| official validation | 25.84 |
| best fusion validation | 25.64 (conservative_fusion_v1) |
| delta | **-0.20pp** |
| DA anchor validation | 25.59 (stronger than fusion) |
| selector validation | 25.99 (worse than official) |
| fusion 14-month overall | 20.93 (-0.10pp vs official) |
| runtime | 6.7s (full), 5.3s (comparison sample) |
| tests | **27 + 33 = 60 passed** |

## 4. True Improvement Source

| Aspect | Assessment |
| ------ | ---------- |
| **Winter DA anchor policy** | The ONLY real source of improvement. DA anchor improves winter by 0.34pp (24.70 → 24.36). Non-winter is identical (19.57). |
| **P3 actual effect** | Negligible. 87% of hours have `shadow_corrected_pred == original_pred`. The remaining 13% have zero or near-zero correction amounts. P3 does not contribute to the 0.20pp gain. |
| **Selector actual effect** | Negative. Selector (25.99) is WORSE than official (25.84) on validation. Never recommended for production. |
| **Why not complex fusion** | All 5 v1.1 policy variants produce identical results to `conservative_fusion_v1`. There is no measurable benefit from any P3/selector interaction logic. The system is, in practice, a seasonal DA policy router. |

## 5. 2.5 / 3.0 / Fusion Comparison

| Variant | Overall | Winter | Non-winter | Runtime | Decision |
| ------- | ------: | -----: | ---------: | ------: | -------- |
| official_baseline (3.0) | 24.22 | 27.37 | 22.62 | 5.3s | — |
| da_anchor (3.0) | **24.02** | **27.07** | **22.44** | 5.3s | **Best real** |
| conservative_fusion_v1 | 24.05 | 27.07 | 22.62 | 5.3s | SHADOW_MONITORING_READY |
| v1_1_minimal_patch | 24.05 | 27.07 | 22.62 | 5.3s | Identical to v1 |
| oracle_upper_bound | 21.67 | 24.20 | 20.28 | 5.3s | ANALYSIS_ONLY |

**2.5 baseline**: `unavailable_or_cached_only` — the 2.5 repo is archived and its prediction ledger only covers 2026-01-25 to 2026-02-25 (32 days). Direct comparison requires unarchiving the 2.5 repo or extracting cached 2.5 outputs from a separate run. The existing cached ledger data does not overlap with the v1.1 comparison sample.

## 6. Files Added / Modified

| File | Status | Notes |
| ---- | ------ | ----- |
| `configs/candidate_registry/fusion_shadow_v1_1.yaml` | NEW | Candidate registry with full contract |
| `docs/experiments/fusion/FUSION_V1_1_ACCEPTANCE_DECISION.md` | NEW | Honest acceptance decision |
| `docs/experiments/fusion/FUSION_V1_1_SEASONAL_DA_POLICY_ROUTER.md` | NEW | Seasonal DA router definition |
| `docs/experiments/fusion/FUSION_V1_1_2_5_COMPARISON_PLAN.md` | NEW | 2.5 comparison plan |
| `tests/test_fusion_shadow_v1_1_registry.py` | NEW | 17 registry tests |
| `tests/test_fusion_shadow_v1_1_policy_router_docs.py` | NEW | 16 docs tests |
| `scripts/run_fusion_v1_1_comparison.py` | NEW | Final comparison runner |
| `pipelines/fusion_shadow_v1.py` | MODIFIED | +5 v1.1 policy builders |
| `configs/fusion_shadow_v1_1.yaml` | NEW | v1.1 config (default off) |
| `scripts/run_fusion_shadow_v1_1.py` | NEW | v1.1 run orchestrator |
| `tests/test_fusion_shadow_v1_1_policy.py` | NEW | 10 policy tests |
| `configs/fusion_shadow_v1.yaml` | UNCHANGED | v1 config kept as-is |
| `tests/test_fusion_shadow_v1_contract.py` | MODIFIED | +period column for v1.1 compat |
| `main.py` | UNCHANGED | Not touched by v1.1 |
| `cli/parser.py` | UNCHANGED | Not touched by v1.1 |

## 7. Tests

| Test File | Tests | Result |
| --------- | ----: | ------ |
| `test_fusion_shadow_v1_1_policy.py` | 10 | ✅ ALL PASS |
| `test_fusion_shadow_v1_1_registry.py` | 17 | ✅ ALL PASS |
| `test_fusion_shadow_v1_1_policy_router_docs.py` | 16 | ✅ ALL PASS |
| `test_fusion_shadow_v1_contract.py` | 12 | ✅ ALL PASS |
| `test_fusion_shadow_v1_no_final_contamination.py` | 5 | ✅ ALL PASS |
| **Total** | **60** | ✅ **ALL PASS** |

## 8. Recommendation

**FUSION_FINAL_RECOMMENDATION: SHADOW_MONITORING_READY**

The seasonal DA policy router (Fusion v1.1) provides a 0.20pp validation improvement
over the official baseline. The improvement is honest, comes from a simple and
explainable rule (Winter DA anchor), and passes all leakage and contamination audits.

However, the following must be clearly understood:
- This is **not** a complex fusion system — it is a **seasonal DA policy router**
- DA anchor alone (25.59) beats the full fusion (25.64) on validation
- The 0.20pp improvement is borderline — it exactly meets the threshold
- P3 and selector overlays add no measurable value at current thresholds

## 9. Final Verdict

**FUSION_FINAL_RESULT: PASS**

Fusion v1.1 is accepted as a **seasonal DA policy router** for shadow monitoring.
It is NOT accepted as a model fusion system, NOT accepted for production,
and NOT accepted for champion replacement.
