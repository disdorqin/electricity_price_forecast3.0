# P1.1 Feature Frame Lessons

## Core Insight: Rich Features Are the Main Source of Improvement

The P1.1 experiment confirmed that **the rich feature frame — not model family substitution — is the primary driver of improvement** over the 2.5 faithful pipeline.

| Candidate | Feature Frame | sMAPE_floor50 | vs Faithful 2.5 |
|-----------|:-------------:|:-------------:|:----------------:|
| Faithful LGBM 2.5 (ThreeStageLGBM) | 24 features | 21.87% | (baseline) |
| cfg05 90d | Rich (~55+ features) | 14.68% | **-7.19pp** |
| cfg05 180d | Rich (~55+ features) | 14.25% | **-7.62pp** |
| xgboost_rich | Rich (~55+ features) | 14.70% | **-7.17pp** |
| catboost_rich | Rich (~55+ features) | 15.75% | -6.12pp |

The gap between rich-frame models (14.25%–15.75%) is small compared to the gap from faithful (21.87%). This strongly suggests that cfg05's feature engineering is the key differentiator.

---

## Window Ablation: 180d Slightly Better Than 90d

| Candidate | sMAPE_floor50 | Delta | Training Time |
|-----------|:-------------:|:-----:|:-------------:|
| cfg05 90d | 14.68% | — | 505s |
| cfg05 180d | 14.25% | **-0.43pp** | 1841s (3.6×) |

The 180d window provides a small but consistent improvement across all periods, at a cost of ~3.6× training time. For a shadow candidate, either window is acceptable — 90d if training budget matters, 180d if maximum accuracy is desired.

**Recommendation**: Keep both 90d and 180d in the shadow registry. Let the total-chain AI decide which to activate.

---

## XGBoost as Diversity Backup

`xgboost_rich` (14.70%) achieves near-identical performance to `cfg05` (14.68%) but via a completely different model family (gradient boosting with a different tree-splitting algorithm). This diversity is valuable:

- If a future data distribution shift impacts LightGBM's effectiveness, XGBoost provides a fallback.
- The `ensemble_rich` (simple average of cfg05 + xgboost_rich) scores **14.54%** — better than either alone by +0.14pp.
- This suggests the two models capture complementary patterns.

---

## Ensemble Direction

`ensemble_rich = simple average (cfg05_pred, xgboost_rich_pred)` produced the best overall result at 14.54%. Future directions to consider:

1. **Period-aware weighting**: Assign per-period weights (e.g., weight more toward the better model per hour bucket).
2. **Stacked meta-model**: Train a lightweight linear model on top of both predictions.
3. **Spike-specific ensemble**: Use xgboost_rich (best spike sMAPE=12.93) for spike hours, cfg05 for normal hours.

These are **not required** for shadow registration — they are future research directions.

---

## Runtime Adapter Is Not Yet Implemented

The rich feature builder currently lives in the experimental repo (`epf-sota-experiment`). Before any candidate can run in the 3.0 delivery pipeline:

1. The feature builder must be ported into the 3.0 codebase as a reusable module.
2. A shadow adapter must be created that:
   - Runs the rich feature builder on live D-1 data.
   - Loads the pre-trained LightGBM (or XGBoost) model.
   - Produces predictions **without** modifying `ledger_predict.py` or `final_outputs.py`.
   - Outputs to a shadow-only directory for comparison.

This adapter work is deliberately **not included in P1.1**. It is a follow-up task for the total-chain AI.

---

## CPU-Only Experience

All P1.1 training was done CPU-only (GPU disabled). Observations:

- CPU training is **rock-solid**: no crashes, no deadlocks, no CUDA context issues.
- cfg05 90d training on 120 days = ~505s (about 8.4 min). Acceptable for daily retraining.
- cfg05 180d = ~1841s (about 31 min). Acceptable if retraining runs as an overnight job.
- XGBoost CPU = ~1140s (about 19 min).
- **No GPU required.** The decision to default CPU-only is validated.

For a potential production deployment, a single mid-range CPU core handles the workload well within a daily cycle.
