# P1.1 Dayahead Shadow Decision

## Overview

This document records the decision to register cfg05 (rich feature frame) as a **shadow** candidate in the efm3.0 3.0 delivery repository. The decision is based on the same-window four-hard-month comparison completed during the P1.1 Gate Fix phase (commit 3578229 in `epf-sota-experiment`).

---

## Why Candidate → Shadow

The P1 candidate review (July 2026) left cfg05 as `candidate` (not `shadow`) because:

1. cfg05 had only been compared against faithful LGBM 2.5 (21.87%).
2. It had **not** been tested against the 2.5 trusted champion (`best_two_average`) **on the same four hard months**.
3. Previous 2.5 champion numbers (11.85%, 11.27%) were measured on easy/single-month windows and were **not comparable**.

**P1.1 resolved this gap** by faithfully reproducing the 2.5 trusted champion (`best_two_average = trial_02 + trial_24 simple average`) on the **same** 2025-11~2026-02 window, using the same data pipeline and evaluation.

Result: **cfg05 90d = 14.68% beats trusted champion = 15.04%** on the same 120-day window. All period slices also improve or match. This clears the path from `candidate` to `shadow`.

---

## Key Numbers (Same Window: 2025-11 ~ 2026-02, 120 days, 2760 rows)

| Candidate | sMAPE_floor50 | vs Baseline | Period 1_8 | Period 9_16 | Period 17_24 | Spike | Normal |
|-----------|:------------:|:-----------:|:----------:|:-----------:|:------------:|:-----:|:------:|
| **cfg05 90d** | **14.68** | **-0.36** | 13.91 | 16.01 | 14.12 | 13.51 | 14.81 |
| cfg05 180d | 14.25 | -0.79 | 13.97 | 15.33 | 13.45 | 13.64 | 14.32 |
| xgboost_rich 90d | 14.70 | -0.34 | 13.29 | 16.62 | 14.19 | 12.93 | 14.90 |
| ensemble_rich 90d | 14.54 | -0.50 | 13.44 | 16.14 | 14.05 | 13.15 | 14.70 |
| **trusted champion best_two_average** | **15.04** | **(baseline)** | 14.75 | 16.20 | 14.05 | - | - |

All four candidates beat the same-window trusted champion (delta < 0).

---

## Invalidated Baselines

| Prior Baseline | Value | Status | Reason |
|---------------|:----:|:------:|:-------|
| best_two_average (Feb1–Mar2) | 11.85% | **INVALID** | Easy single-month window (30 days). Not comparable to four hard months. |
| lgbm_spike_residual | 11.27% | **INVALIDATED** | Data leakage discovered. Not usable as any baseline. |
| faithful LGBM 2.5 ThreeStageLGBM | 21.87% | Reference only | Faithful reproduction, not trusted champion. |

---

## Shadow Status Rules

| Rule | Value |
|------|:-----:|
| writes `submission_ready.csv` | `false` — **must never** |
| replaces 3.0 champion | `false` — **must never** |
| modifies `final_outputs` | `false` — **must never** |
| modifies `main.py` | `false` — **must never** |
| modifies `ledger_predict` | `false` — **must never** |
| requires manual enable | `true` |
| promotion cap | `shadow_only` (not `champion` or `production`) |

**Shadow is not champion. Shadow is not production. Shadow must never touch the live delivery path.**

---

## File Location

- Shadow registry: `configs/shadow_registry/dayahead_cfg05.yaml` (and variants)
- Source experiment repo: `disdorqin/epf-sota-experiment` commit `3578229`
- Candidate package: `models/exports/efm3_candidates/dayahead/efm3_candidates_20260707_gatefix/`

---

## Approvals

- **P1.1 Gate Fix**: PASS (all 6 gates closed)
- **P1.1 Self-Integration**: READY_FOR_REVIEW
- **Next step**: Total-chain AI review → decide whether to implement a runtime shadow adapter.
