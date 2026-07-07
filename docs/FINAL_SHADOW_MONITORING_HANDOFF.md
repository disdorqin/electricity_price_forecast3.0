# 3.0 Final Shadow Monitoring Handoff

> Repository: `disdorqin/electricity_price_forecast3.0`
> Branch: `main` (SHA `b958c89`)
> Last Validated: 2026-07-07 (Post-Merge Validation v1 — PASS)
> Author: AI Co-pilot (P1-P3.5 Exploration Chain)

---

## 1. Overview

This document summarizes all experimental modules that have been integrated into the
`electricity_price_forecast3.0` codebase. **Nothing in this handoff is production.**

Every module is either:

- **Registry-only**: candidate configuration files documenting experiment conclusions.
  None replace the production champion.
- **Shadow-only**: code that runs only when explicitly enabled via CLI flags.
  Writes only to isolated `outputs/runs/{date}/*_shadow/` directories.
  Never modifies `final/`, `submission_ready.csv`, or `delivery_status`.
- **Monitoring-only**: configuration that enables observation without action.

## 2. Module Inventory

| # | Module | Type | Default | Production | Winter Policy |
|---|--------|------|---------|------------|---------------|
| P1.1 | Day-ahead shadow registry | Registry | OFF | Prohibited | N/A |
| P2.8 | Realtime canonical correction | Registry | ON | Safe (ledger fix) | N/A |
| P2.10 | DA-SGDF selector candidate | Registry | OFF | Prohibited | N/A |
| P2.11 | Realtime selector shadow adapter | Shadow | OFF | Prohibited | Winter DA-only |
| P2.13 | Winter no-go policy | Registry | ON | Policy | DA-only Nov-Feb |
| P3.2 | Extreme price shadow hook | Shadow | OFF | Prohibited | Monitoring only |
| P3.4 | Winter shadow monitoring registry | Registry | OFF | Prohibited | Observation only |
| P3.5 | Selector + P3 coexistence safety | Registry | OFF | Prohibited | Safe coexistence |

## 3. Module Detail

### P1.1 Day-Ahead Shadow Registry

- **Status**: `candidate` (shadow allowed, champion prohibited)
- **Location**: `configs/candidate_registry/dayahead_cfg05.yaml`
- **Docs**: `docs/experiments/dayahead/P1_1_DAYAHEAD_SHADOW_DECISION.md`
- **Key conclusion**: cfg05 (rich features) beats faithful 2.5 (14.68% vs 21.87%) and
  aligns with P1.1 gate review. Kept as candidate for future shadow integration.
- **Safety**: Not merged into production delivery chain. Registry only.

### P2.8 Realtime Canonical Correction

- **Status**: Safe ledger fix (integrated into main chain)
- **Location**: `common/realtime_canonical_loader.py`
- **Key conclusion**: Corrected realtime ledger DA anchor loading to use canonical
  xlsx path. Does not change model behavior or predictions.
- **Safety**: Production safe.

### P2.10 DA-SGDF Selector Candidate

- **Status**: `seasonal_candidate` (registry-only, champion prohibited)
- **Location**: `configs/candidate_registry/realtime_da_sgdf_selector.yaml`
- **Docs**: `docs/experiments/realtime_lite/P2_10_DA_SGDF_SELECTOR_HANDOFF.md`
- **Key conclusion**: Selector improves non-winter months by choosing between
  DA_anchor and SGDFNet lite. Winter months: DA_anchor dominates (P2.12/2.13).
- **Safety**: Registry only. Not in production chain.

### P2.11 Realtime Selector Shadow Adapter

- **Status**: `shadow_only` (default OFF)
- **Location**: `pipelines/realtime_da_sgdf_selector_shadow.py`
- **Config**: `configs/candidate_registry/realtime_da_sgdf_selector.yaml`
- **Docs**: `docs/experiments/realtime_lite/P2_11_DA_SGDF_SELECTOR_SHADOW_INTEGRATION.md`
- **Key conclusion**: In winter (Nov-Feb), DA_anchor is selected 97%+ of hours.
  Safe but not beneficial in winter. In non-winter months, selector can improve
  accuracy via shadow observation.
- **Behavior**: Reads DA anchor from xlsx + SGDFNet from run output. Writes
  `selector_shadow_predictions.csv` to `realtime_da_sgdf_selector_shadow/` dir.

### P2.13 Winter No-Go Policy

- **Status**: Policy (enforced in registry)
- **Location**: `configs/candidate_registry/realtime_selector_winter_policy.yaml`
- **Docs**: `docs/experiments/realtime_lite/P2_12_WINTER_HARD_WINDOW_DECISION.md`
- **Key conclusion**: Realtime selector is winter NO-GO. DA_anchor wins
  4/4 winter months (2025-11 through 2026-02). Selector must not be used in winter.
- **Behavior**: Policy gates selector activation during Nov-Feb.

### P3.2 Extreme Price Shadow Hook

- **Status**: `shadow_only` (default OFF)
- **Location**: `pipelines/extreme_price_shadow.py`
- **Config**: `configs/candidate_registry/extreme_price_shadow_winter.yaml`
- **Docs**: `docs/experiments/spike_residual/P3_EXTREME_PRICE_SHADOW_INTEGRATION.md`
- **Key conclusion**: Through P3.3 winter full-chain validation (120 days):
  - Negative hours (584): 88.36 → 75.56 sMAPE_floor50 (**-12.80 improvement**)
  - Spike hours (109): 35.81 → 35.22 (**-0.59 improvement**)
  - Normal hours (2187): 32.91 → 33.85 (**+0.94 acceptable degradation**)
  - Applied: 466 corrections, **0 cap hits, 0 rollbacks**
  - False positive rate: 35.6%
  - **Recommendation: WINTER_SHADOW_MONITORING_READY**
- **Safety**: Shadow-only post-step in `main.py`. Wrapped in try/except.
  Never writes final/ or submission_ready.csv.

### P3.4 Winter Extreme Shadow Monitoring Registry

- **Status**: `monitoring` (observation only)
- **Location**: `configs/candidate_registry/extreme_price_shadow_winter_monitoring.yaml`
- **Docs**: `docs/experiments/spike_residual/P3_3_WINTER_SHADOW_MONITORING_DECISION.md`
- **Behavior**: Enables controlled observation of P3 corrections in winter without
  affecting production outputs.

### P3.5 Selector + P3 Coexistence Safety

- **Status**: `coexistence_safe` (verified coexistence)
- **Location**: `configs/candidate_registry/system_shadow_coexistence.yaml`
- **Docs**: `docs/experiments/system_shadow/P3_5_SELECTOR_P3_COEXISTENCE_DECISION.md`
- **Key conclusion**: P3 and selector can coexist safely. Both are shadow-only,
  write to separate directories, share no runtime state.
  **0 overlap hours observed** in winter test window (selector chose DA 97%+,
  P3 corrections applied in different hours). No operational conflict by design.
- **Verified**: 20 winter dates, 480 hours, P3.5 PASS.

## 4. Architecture Safety

```
main.py
  ├── _dispatch_pipeline()     ← main chain (ledger_full, etc.)
  │                              writes final/ and submission_ready.csv
  │                              produces exit_code and delivery_status
  │
  ├── [P3.2] extreme_price_shadow  ← try/except, ONLY when --enable-extreme-price-shadow
  │     └── writes to outputs/runs/{date}/extreme_price_shadow/
  │
  └── [P2.11] realtime_selector_shadow  ← ONLY when --enable-realtime-da-sgdf-selector-shadow
        └── writes to outputs/runs/{date}/realtime_da_sgdf_selector_shadow/
```

**Key architectural properties:**
- Both shadow hooks run **after** `_dispatch_pipeline()` returns
- Shadow return values are **never** used to modify exit_code or delivery_status
- Both hooks are wrapped in `try/except` — any exception is logged, main chain unaffected
- Shadow modules never read/write `final/` paths
- Shadow modules never read/write `submission_ready.csv`

## 5. Caveats

- The realtime prediction ledger on this repo covers only **32 days** (2026-01-25 to
  2026-02-25). The full 120-day winter ledger used in P3.3 validation was computed
  on the `electricity_forecast_deep_sgdf_delta` repo.
- P3.3 winter metrics were computed via `replay_from_ledger` mode, not full chain.
  Actual production metrics may differ.
- P3 spike classifier has inherently low precision (P3 P-phase showed P=0.118).
  Current spike corrections are conservative.
- No risk pack is available — P3 shadow operates in degraded feature mode.
- P2.11 selector module API differs from P3 shadow API (selector uses
  `target_date=str`, not SimpleNamespace). See `SHADOW_OPERATION_COMMANDS.md`.
