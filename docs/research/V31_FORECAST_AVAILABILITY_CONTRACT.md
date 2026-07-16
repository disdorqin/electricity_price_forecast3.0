# EFM3 V3.1-R1 — Forecast Availability Contract (RT price prediction)

> Status: **BINDING for all V3.1-R1 research replay code.**
> Violations of this contract invalidate any metric derived from the offending column.

## 1. Forecasting context

This research patch predicts **Real-Time (RT) electricity prices**.

The EFM3 production RT circuit (`pipelines/full_chain_orchestrator.py`,
`realtime_chain.py`) issues the RT forecast for operating day **D** at the
**day-ahead RT gate**, i.e. *before* day D's DA clearing price is published.
This is confirmed by the production leak-free number (RT ≈ 27.4% sMAPE) which
was obtained **only after removing the `da_anchor` (target-day DA) read** that
previously leaked the target-day DA into the RT selector.

Therefore the research replay adopts the **strict, leak-free convention**:

```
TARGET-DAY DA CLEARING PRICE IS NOT VISIBLE AT RT PREDICTION TIME.
```

This is the production-consistent choice. (An alternative deployment that
predicts *today's* RT after DA has cleared would make the target-day DA visible;
that mode is explicitly NOT used here — see §6.)

## 2. Decision record

| Question | Answer |
|---|---|
| Is `legal_oos_da_prediction` a real OOS DA model output? | **NO** — in V3.1 it was a literal copy of `da_actual` (see §3). INVALID. |
| Is `da_actual` (target day) visible at RT prediction time? | **NO** (per §1). |
| Branch taken | **DA NOT VISIBLE → build a genuine rolling-origin day-ahead (DA) model.** |
| Column used as DD baseline / DA feature | `da_oos_pred` (output of the OOS DA model, §4). |
| Was the prior "da_actual is leakage" statement correct? | **YES, under this convention.** It is NOT retracted. |

> We do **not** rename `da_actual` → `da_clearing_price_known_at_rt`, because that
> name is only legal under the *DA-visible* mode, which this replay does not use.
> If a future deployment proves DA-visible, that column may be introduced under
> its own contract — but it must never be fabricated by copying `da_actual`.

## 3. Why `legal_oos_da_prediction` was invalid (defect #1)

```python
# tools/research/build_full_history_panel.py  (V3.1, REMOVED)
out["legal_oos_da_prediction"] = out["da_actual"]   # <-- literal copy, target-day leakage
```

This column equalled `da_actual` for the **same (business_day, hour)** row it was
later used to predict. Using it as the DD baseline or as an RT-model feature is
**target-day leakage**. It has been removed from the panel.

## 4. Legal DA source: `da_oos_pred`

A genuine day-ahead model is trained under **rolling origin** (retrain every
`RETRAIN_DAYS` days; predict each target day using only data strictly before the
prediction time). Features are prediction-time legal (exogenous `*_forecast`,
calendar, past DA lags). Target = `da_actual` **of past days only** (never the
target day). Output: `da_oos_pred[business_day, hour]` — the OOS DA forecast for
that day. This column is the ONLY legal DA proxy and is used as:

- the **DD baseline** (`DD = da_oos_pred`), and
- the **DA feature** fed to every RT candidate model.

## 5. Visibility matrix (per row, at RT prediction time)

| Column | Role | Visible at RT prediction time? | Legal as RT feature? |
|---|---|---|---|
| `rt_actual` (target day) | evaluation target | **NO** | NO (target only) |
| `da_actual` (target day) | DA actual | **NO** | NO (leakage) |
| `da_oos_pred` (target day) | OOS DA forecast | YES (model output) | YES (this is the legal proxy) |
| `*_forecast` (target day) | exogenous forecasts | YES (issued before RT) | YES |
| `*_actual` (target day, excl. da/rt) | exogenous actuals | **NO** | NO (would be leakage) |
| `*_actual` / `da_actual` (strictly PAST days) | history | YES | YES (lags) |
| `da_price_lag_24h` etc. (source) | past DA/RT lags | YES | YES |

Rules:
- **Never rename `da_actual` into a prediction.** A prediction column must come
  from a model trained under OOS, not from an `*_actual` column.
- `*_actual` columns (other than past-day lags used as history) are **invisible**
  at RT prediction time and must not enter RT-model features for the target day.

## 6. Contract fields (every research script MUST respect)

- `forecast_issued_at` = the train cutoff timestamp (latest data used).
- `target_day` = the business_day being predicted.
- `data_cutoff` = max timestamp of data available at forecast_issued_at.
- target-day `da_actual` visible? → **NO**.
- target-day `rt_actual` visible? → **NO**.
- visible `*_forecast` → all exogenous `*_forecast` for the target day.
- invisible `*_actual` → `da_actual`, `rt_actual`, and all other `*_actual` of
  the target day.

## 7. Enforcement

- `test_v31_availability_contract.py` asserts the panel no longer contains
  `legal_oos_da_prediction` and that no `*_actual` of the target day is used as
  an RT feature.
- `test_v31_rolling_preprocess.py` asserts the panel is built with
  `utils.business_day` and that hour-24 mapping is correct
  (`D+1 00:00 → business_day D, hour_business 24`).
