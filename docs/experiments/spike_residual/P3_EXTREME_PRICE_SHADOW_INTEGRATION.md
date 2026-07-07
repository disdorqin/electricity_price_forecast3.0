# P3.2 Extreme Price Shadow Integration

> Controlled shadow integration of the P3 Extreme Price Correction System into
> `electricity_price_forecast3.0`. **Default OFF. Production-never. Shadow-only.**

---

## 1. Purpose

P3 validated an Extreme Price Correction System (negative-price + spike classifiers +
guard + rollback) on 32 real days of Shandong RT ledger data. Temporal-stability proxy
passed, but the hard SHADOW gate (≥ 3 months of real ledger) is **not yet met**. P3.2
therefore wires the validated engine into the 3.0 run as a **controlled shadow**: it reads
the 3.0 realtime fused predictions, runs the correction, and writes the result to an
isolated `extreme_price_shadow/` directory — never touching `submission_ready.csv`, never
replacing the original fused realtime prediction, never marked champion.

This is explicitly **not production**. It is an observation harness so owners can watch
shadow behaviour on live 3.0 runs before any promotion decision.

---

## 2. Hard safety boundaries (enforced)

| # | Rule | How enforced |
|---|------|--------------|
| 1 | Default OFF | `configs/extreme_price_shadow.yaml` → `shadow.enabled: false`; `main.py` only runs the post-step when `--enable-extreme-price-shadow` is passed. |
| 2 | No `submission_ready.csv` write | Shadow writes **only** to `outputs/runs/{date}/extreme_price_shadow/`; the final/submission writer is never called. |
| 3 | `original_pred` never replaced | Corrected values live in `shadow_corrected_pred`; `original_pred` is preserved verbatim. |
| 4 | No champion / no NORMAL-improvement claim | Reports never mark champion; they explicitly state shadow status and (without a risk pack) do not claim effectiveness. |
| 5 | Cutoff-safe (no future / no D14-after actual) | Classifiers are fit on **history only** (actuals strictly before the target day). The target day's actual is never read. |
| 6 | No skipped postflight / rollback | `ledger_full` postflight + rollback run normally; the shadow is purely additive. |
| 7 | No silent failure | All failures are logged via `logging.exception` and surfaced in a degraded shadow output + report; the main chain is never blocked. |

---

## 3. File structure (added by P3.2)

```
pipelines/extreme_price_shadow.py                 # shadow pipeline (config + feature builder + runner + reports)
configs/extreme_price_shadow.yaml                 # controlled-shadow config (default OFF)
docs/experiments/spike_residual/
  P3_EXTREME_PRICE_SHADOW_INTEGRATION.md           # this document
tests/
  test_extreme_price_shadow_contract.py           # shadow contract (24 rows / no NaN / shadow_only / schema)
  test_extreme_price_shadow_no_final_contamination.py  # no submission_ready.csv / final contamination; failure safe
  test_extreme_price_shadow_schema.py             # column schema + cap + rollback_reason presence
  run_shadow_tests.py                              # runner (pytest-compatible)
```

### Output directory

```
outputs/runs/{YYYY-MM-DD}/extreme_price_shadow/
├── shadow_predictions.csv      # 24 rows, shadow_only=true, original_pred preserved
├── shadow_report.md
├── shadow_report.json
└── rollback_report.json
```

---

## 4. Inputs (read from the 3.0 run + ledger)

- realtime fused predictions (`original_pred`) — from run output
  `outputs/runs/{date}/realtime/final/realtime_final_predictions.csv` when present, else
  recomputed from the realtime prediction ledger via expanding inverse-MAE fusion.
- per-model realtime predictions (`rt916/sgdfnet/timemixer/timesfm`) → `model_std/min/max`.
- dayahead anchor (`da_anchor`) — dayahead fused for the target day.
- `hour_business`, `period`, calendar (`ds`) — from the ledger.
- **historical** same-hour statistics (neg-rate / p50 / p90) and classifier training labels
  — computed **only** from actuals strictly before the target day.
- risk probabilities — **not yet emitted by the 3.0 run**; when absent the shadow still runs
  but the report marks `has_risk_pack: false` and does **not** claim correction effectiveness.

---

## 5. Output schema — `shadow_predictions.csv`

| Column | Type | Notes |
|--------|------|-------|
| business_day | str | target date |
| ds | str | timestamp |
| hour_business | int | 1..24 |
| period | str | `1_8` / `9_16` / `17_24` |
| original_pred | float | **preserved, never replaced** |
| shadow_corrected_pred | float | corrected value (== original_pred when not applied) |
| correction_amount | float | signed delta |
| negative_probability | float | 0..1 |
| spike_probability | float | 0..1 |
| spike_type | str | `none` / `high` / `low` |
| correction_reason | str | human-readable reason |
| confidence | float | applied correction confidence |
| applied | bool | whether correction was applied |
| rollback_reason | str | `none` when not rolled back |
| shadow_only | bool | **always True** |
| model_version | str | `extreme_price_shadow_v1` |
| run_id | str | `eps_shadow_{date}` |

Invariants: **24 rows**, `hour_business` ∈ 1..24, **no NaN**, `shadow_only == True`,
`original_pred` untouched.

---

## 6. Run commands

```bash
# Dedicated shadow-only run (reads 3.0 run/ledger, writes extreme_price_shadow/):
python main.py 2026-02-10 --pipeline extreme_price_shadow

# Controlled shadow post-step after a production pipeline (default OFF):
python main.py 2026-07-03 --enable-extreme-price-shadow
python main.py 2026-07-03 --enable-extreme-price-shadow --shadow-only

# Tests
python -m pytest tests/ -q
```

`--enable-extreme-price-shadow` is the master switch; without it the shadow never runs.
`--shadow-only` reaffirms the observation-only contract (the shadow is always shadow-only
regardless).

---

## 7. Relationship to P3

- Reuses the **validated** P3 math: negative/spike classifier feature builders, `guard_pass`,
  `evaluate_rollback`, `SimpleLogistic` (from `experimental/p3_extreme_price_correction`).
- Uses the P3 **candidate** knob set (`optimized_config`): negative + spike ON, residual OFF,
  `NEG_THRESH=0.80`, `NEG_ACT_PRED_CAP=50`.
- Divergence from P3: classifiers are **fit on history only** and predict the target day
  (strict leakage-free for a live run), rather than the P3 offline walk-forward that used
  each row's own past. This is the correct live-deployment semantics.

---

## 8. Promotion gate (still closed)

P3.2 is a CONTROLED SHADOW. Promotion to production/shadow-of-record requires the hard
SHADOW gate from P3: **≥ 3 months of real ledger** with stable shadow behaviour and an
owner sign-off. Until then this integration is observation-only.
