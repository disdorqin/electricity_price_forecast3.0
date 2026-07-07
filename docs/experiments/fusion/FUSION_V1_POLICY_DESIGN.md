# EFM3 Fusion Chain v1 — Policy Design

## Overview

The EFM3 Fusion Chain v1 is a **rule-based, lightweight fusion policy graph** designed for shadow backtest evaluation. It synthesizes three experimental lines (P1 day-ahead, P2 realtime selector, P3 extreme price correction) into a unified fusion prediction for evaluation against the 3.0 official baseline and the 2.5 baseline.

## Core Design Principle

> **No training, no GPU — pure replay + vectorized policy evaluation.**

This is NOT a production fusion engine. It is a **fusion shadow backtest / replay evaluation** that:

1. Reads existing predictions from ledgers and shadow outputs
2. Applies deterministic policy rules to construct alternative predictions
3. Computes metrics for comparison
4. Generates diagnostic reports

## Policy Graph

```
                    ┌─────────────────────────┐
                    │  Input Predictions       │
                    │  (SGDFNet / DA Anchor)   │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  Month Check            │
                    │  [11,12,1,2]?           │
                    └──────┬──────────┬───────┘
                           │          │
                     YES   │          │  NO
                           │          │
              ┌────────────▼──┐   ┌───▼────────────┐
              │ Winter Policy │   │ Non-Winter      │
              │ DA Anchor     │   │ SGDFNet Baseline│
              │ (selector=off)│   │ (selector=on)   │
              └───────┬───────┘   └───┬────────────┘
                      │              │
                      └──────┬───────┘
                             │
                    ┌────────▼────────┐
                    │ P3 Available?   │
                    │ & Confidence    │
                    │ >= threshold?   │
                    └──────┬──────────┘
                           │
              ┌────────────▼──────────┐
              │ Apply P3 Correction   │
              │ (negative/spike)      │
              └────────────┬──────────┘
                           │
                    ┌──────▼──────────┐
                    │ fusion_shadow_  │
                    │ pred output     │
                    └─────────────────┘
```

## Variants

| Variant | Description | Winter Behavior | P3 Overlay |
|---------|-------------|-----------------|------------|
| official_baseline | SGDFNet prediction (primary realtime model) | Same | No |
| da_anchor | Day-ahead price only | Same | No |
| sgdfnet_only | SGDFNet prediction | Same | No |
| realtime_selector_shadow | P2.11 DA-SGDF selector | Selector applied if available | No |
| p3_extreme_shadow | P3 correction overlay | Yes | Yes (>= 0.7) |
| winter_da_only_policy | DA anchor in winter, SGDFNet otherwise | DA anchor forced | No |
| selector_then_p3_overlay | Selector first, then P3 overlay | Both applied | Yes |
| p3_then_selector_overlay | P3 first, then selector fill | Both applied | Yes |
| conservative_fusion_v1 | Winter DA + P3 high-confidence overlay | DA anchor forced | Yes (>= 0.9, capped) |
| oracle_upper_bound | Per-hour best pick (ANALYSIS ONLY) | Uses actual | Uses actual |

## Data Sources

| Source | Path | Purpose |
|--------|------|---------|
| XLSX data | `data/shandong_pmos_hourly.xlsx` | Actual prices (y_true) and DA anchor (日前电价, 实时电价) |
| SGDFNet predictions | `outputs/runs/{date}/realtime/prediction/sgdfnet_predictions.csv` | Primary realtime model predictions |
| P3 shadow | `outputs/runs/{date}/extreme_price_shadow/shadow_predictions.csv` | P3 extreme price corrections |
| Selector shadow | `outputs/runs/{date}/realtime_da_sgdf_selector_shadow/selector_shadow_predictions.csv` | P2.11 selector shadow |

## Output Files

All outputs go to:
- `outputs/fusion_shadow_v1/` — intermediate / working outputs
- `exports/efm3_candidates/fusion_chain/fusion_v1_first_big_run/` — final deliverable

See `FUSION_V1_FIRST_BIG_RUN_REPORT.md` for full results.

## Contract

- **Default-off**: `fusion.enabled: false` in config
- **No final contamination**: Never writes to `final/`, `submission_ready.csv`, or champion
- **Canonical hour mapping**: `01:00→1, ..., 23:00→23, 00:00→24`
- **No target-day actual leakage**: Actuals loaded ONLY for metric computation
- **Oracle isolated**: `oracle_upper_bound` flagged `ANALYSIS_ONLY`
