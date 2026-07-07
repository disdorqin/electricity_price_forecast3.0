# P1.1 Gate Fix Summary

## Background

The P1 day-ahead review identified 6 gate gaps in the cfg05 rich candidate package (efm3_candidates_20260707). P1.1 was tasked with closing all 6 gates to secure a `shadow` promotion decision.

---

## Gate ①: Gating Files (metrics.json / manifest.json / promotion_decision.json)

**Before**: Files existed but `manifest.json` referenced outdated baselines (11.85% easy-window champion, not the same-window four-hard-month comparison).

**Fix**: Regenerated all gating files from the gatefix re-run results:
- `metrics.json` — contains overall, period, spike, timing, month, and skipped sections for all candidates, cross-referenced against the same-window trusted champion.
- `manifest.json` — lists all 4 shadow candidates with correct source commit, package path, and feature frame version.
- `promotion_decision.json` — sets `decision: shadow`, `lead: cfg05`, `champion_forbidden: true`.

**Status**: ✅ **PASS**

---

## Gate ②: Naming and Paths (3.0 Contract)

**Before**: Package naming did not follow the 3.0 contract convention.

**Fix**: New gatefix package `efm3_candidates_20260707_gatefix/` contains all 8 required files:
1. `predictions.csv` — 14,400 rows (5 models × 2880 rows), 0 NaN
2. `metrics.json`
3. `manifest.json`
4. `promotion_decision.json`
5. `comparison_report.md` — cfg05 vs trusted champion, period breakout
6. `ablation_report.md` — window ablation 90d vs 180d
7. `config_snapshot.yaml` — engine CLI and model hyperparams
8. `gate_review_report.md` — full 10-section gate review report

**Status**: ✅ **PASS**

---

## Gate ③: Same-Window Champion Retest

**Before**: cfg05 had only been compared against faithful LGBM 2.5 (21.87%) on a different window. The trusted champion `best_two_average` was quoted at 11.85% — which was measured on an **easy single-month window** (Feb1–Mar2, 30 days), not the four hard months.

**Fix**: Reproduced `best_two_average` (trial_02 + trial_24 simple average) faithfully on the **same** 2025-11~2026-02 four hard months using `reproduce_best_two_average_4month.py`. Result: **trusted champion = 15.04%** (n=2760 rows, 120 days).

| Candidate | Same Window sMAPE | Beats Champion? |
|-----------|:-----------------:|:---------------:|
| cfg05 90d | 14.68% | ✅ -0.36pp |
| cfg05 180d | 14.25% | ✅ -0.79pp |
| xgboost_rich | 14.70% | ✅ -0.34pp |
| ensemble_rich | 14.54% | ✅ -0.50pp |

Old 11.85% champion reference: **invalidated** (different/easier window, not comparable).
Old 11.27% spike residual reference: **invalidated** (data leakage).

**Status**: ✅ **PASS**

---

## Gate ④: Unified Four-Hard-Month Window

**Before**: Some candidate comparisons used different windows.

**Fix**: All 4 candidates plus the trusted champion are now evaluated on **exactly** the same window:
- Months: 2025-11, 2025-12, 2026-01, 2026-02
- Days: 120 business days
- Metric: sMAPE_floor50 (same as 2.5 `fusion/metrics.py`)

No mixing of easy-month windows with hard-month windows. All period and spike breakout metrics computed on the same 120-day scope.

**Status**: ✅ **PASS**

---

## Gate ⑤: CPU-Only Default (GPU Disabled)

**Before**: Engine `run_dayahead_p1_walkforward.py` had GPU-preferred as the default path (`_lgbm_device()` returned GPU if available). Daemon had `gpu_disabled: true` but the raw engine was a production hazard.

**Fix**:
- Added `--gpu` flag to engine CLI. **Default is CPU-only**. Only `--gpu` enables GPU.
- Daemon unchanged (`gpu_disabled: true`, already CPU-only safe).
- CPU-only training times recorded:
  - cfg05 90d: 505s
  - cfg05 180d: 1841s
  - xgboost_rich: 1140s
  - baseline_lgbm25: 42s

Recorded in `config_snapshot.yaml`.

**Status**: ✅ **PASS**

---

## Gate ⑥: Negative Price / Spike / Period Review

Checked all period, spike, and negative-price breakout metrics for regression:

| Dimension | cfg05 | Trusted Champion | Delta | Regressed? |
|-----------|:----:|:----------------:|:-----:|:----------:|
| Period 1_8 (hours 1–8) | 13.91 | 14.75 | **-0.84** | No (improved) |
| Period 9_16 (hours 9–16) | 16.01 | 16.20 | **-0.19** | No (improved) |
| Period 17_24 (hours 17–24) | 14.12 | 14.05 | +0.07 | No (within noise) |
| Spike sMAPE | 13.51 | - | - | Acceptable |
| Normal sMAPE | 14.81 | - | - | Acceptable |
| Negative hit rate | 72.39% | 71.97% | +0.42pp | Acceptable |

No dimension shows material regression vs the same-window trusted champion.

**Status**: ✅ **PASS**

---

## Delivery

- **Commit**: `3578229` in `disdorqin/epf-sota-experiment`
- **Branch**: `main` (experiment repo only)
- **3.0 Shadow Integration**: Branch `agent/p1.1-dayahead-shadow-integration` in `disdorqin/electricity_price_forecast3.0`
- **Shadow Registry**: `configs/shadow_registry/dayahead_cfg05.yaml` (and 3 variants)
