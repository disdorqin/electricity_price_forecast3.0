"""
Build FULL_HISTORY_CANONICAL_PANEL from discovered 2022+ history.

Source of truth (actuals + exogenous):
  <sibling>/deep_model_for_electricity/data/preprocessed_data.csv
  columns: times, da_price(DA actual), rt_price(RT actual),
           *_forecast / *_actual exogenous, and *_lag_*h past lags.

V3.1-R1 correctness fixes (vs V3.1):
  - business_day / hour_business derived via utils.business_day
    (D+1 00:00 -> business_day D, hour_business 24). The old
    `business_day = times.date` rule produced hour-0 / shifted-day rows.
  - REMOVED `legal_oos_da_prediction = da_actual` (target-day leakage, defect #1).
    The legal DA proxy is produced later by a rolling-origin OOS DA model
    (see v31_lib.build_oos_da) and is NEVER stored in this panel.
  - ds (wall-clock timestamp) added via timestamp_from_business for round-trip.
  - Past lags (rt_price_lag_*, da_price_lag_*, bidding_space_forecast_lag_*) are
    included as LEGAL features (values strictly in the past at RT time).

Discipline:
  - DATA_AS_OF_DATE = max(business_day with complete 24h rt_actual)
  - No *_actual of the target day is ever turned into a prediction column here.
"""
import os, json, hashlib, datetime
import pandas as pd
import numpy as np

# --- robust path resolution (repo is sibling of deep_model_for_electricity) ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))            # .../tools/research
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))           # .../electricity_forecast_model3.0-research
DATA_SIBLING = os.path.dirname(REPO_ROOT)                         # .../其他资料
SRC = os.path.join(DATA_SIBLING, "deep_model_for_electricity", "data", "preprocessed_data.csv")
OUTDIR = os.path.join(REPO_ROOT, "data_audit")
os.makedirs(OUTDIR, exist_ok=True)
PANEL = os.path.join(OUTDIR, "FULL_HISTORY_CANONICAL_PANEL.parquet")
VERDICT = os.path.join(OUTDIR, "FULL_HISTORY_CANONICAL_VERDICT.json")

# business-day mapping must come from the canonical util (not times.date)
import sys
sys.path.insert(0, REPO_ROOT)
from utils.business_day import (
    business_day_from_timestamp,
    hour_business_from_timestamp,
    timestamp_from_business,
)

print("[1] loading source:", SRC)
df = pd.read_csv(SRC)
print("   raw rows:", len(df), "cols:", len(df.columns))
df["times"] = pd.to_datetime(df["times"])
df = df.sort_values("times").reset_index(drop=True)

# ---- canonical business-day mapping (3.0 convention) ----
df["business_day"] = df["times"].apply(business_day_from_timestamp)
df["hour_business"] = df["times"].apply(hour_business_from_timestamp)
# wall-clock ds (round-trip partner of business_day/hour_business)
df["ds"] = df.apply(
    lambda r: timestamp_from_business(r["business_day"], int(r["hour_business"])),
    axis=1,
)

# ---- actuals (NEVER reused as predictions) ----
out = pd.DataFrame()
out["business_day"] = df["business_day"]
out["hour_business"] = df["hour_business"].astype(int)
out["ds"] = df["ds"]
out["rt_actual"] = df["rt_price"].astype(float)
out["da_actual"] = df["da_price"].astype(float)

# ---- exogenous actuals + forecasts (legal: all are PAST at RT time when used as history) ----
exo_actual = ["local_plant_actual","tie_line_load_actual","wind_actual","solar_actual",
              "nuclear_actual","self_supply_actual","test_unit_actual",
              "direct_dispatch_actual","bidding_space_actual","renewable_actual"]
exo_fc = ["local_plant_forecast","tie_line_load_forecast","wind_forecast","solar_forecast",
          "nuclear_forecast","self_supply_forecast","test_unit_forecast",
          "direct_dispatch_forecast","bidding_space_forecast","renewable_forecast"]
for c in exo_actual + exo_fc:
    if c in df.columns:
        out[c] = df[c].astype(float)

# ---- legal PAST lags (values strictly in the past at RT time) ----
lag_cols = [c for c in df.columns if ("_lag_" in c) and (c not in out.columns)]
for c in lag_cols:
    out[c] = df[c].astype(float)

# ---- NO da_oos_pred / legal_oos_da_prediction column here (defect #1 fix) ----
# The legal DA proxy is produced by v31_lib.build_oos_da() under rolling origin.

out["data_lineage"] = "deep_model_for_electricity/preprocessed_data.csv"
out["oos_type"] = "STRICT_REPLAY_OOS"

# ---- validation ----
print("[2] validating panel integrity")
days = out["business_day"].unique()
hours_per_day = out.groupby("business_day")["hour_business"].nunique()
bad_days = hours_per_day[hours_per_day != 24]
dup = out.duplicated(subset=["business_day","hour_business"]).sum()
rt_missing = out["rt_actual"].isna().sum()
da_missing = out["da_actual"].isna().sum()

# hour 24 mapping check: D+1 00:00 must map to business_day D, hour 24
sample = df.iloc[0]
ts0 = sample["times"]
bd0 = business_day_from_timestamp(ts0); hb0 = hour_business_from_timestamp(ts0)
midnight_rows = df[df["times"].dt.hour == 0]
mapping_ok = True
for _, r in midnight_rows.iterrows():
    if not (business_day_from_timestamp(r["times"]) == (r["times"] - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            and hour_business_from_timestamp(r["times"]) == 24):
        mapping_ok = False
        break

data_start = sorted(days)[0]
data_as_of = sorted(days)[-1]
# last day with a FULL 24h of non-null rt_actual (excludes partial trailing day)
full_rt_days = out.groupby("business_day").apply(
    lambda g: len(g) == 24 and g["rt_actual"].notna().all()
)
last_complete_rt = full_rt_days[full_rt_days].index.max()

print("   days:", len(days), "bad_days(!=24h):", len(bad_days),
      "dups:", dup, "rt_missing:", rt_missing, "da_missing:", da_missing)
print("   DATA_START_DATE:", data_start, "DATA_AS_OF_DATE(complete 24h rt):", last_complete_rt)
print("   hour24 mapping OK:", mapping_ok, "| sample", ts0, "->", bd0, "h", hb0)
print("   lag cols included:", lag_cols)

# ---- save panel ----
out.to_parquet(PANEL, index=False)
print("[3] wrote panel:", PANEL, "shape", out.shape)

# ---- verdict ----
verdict = {
    "generated": datetime.datetime.now().isoformat(),
    "source": SRC,
    "source_sha256_head16": hashlib.sha256(open(SRC,"rb").read()).hexdigest()[:16],
    "data_start_date": str(data_start),
    "data_as_of_date": str(last_complete_rt),
    "total_days": int(len(days)),
    "total_hours": int(len(out)),
    "bad_days_not_24h": int(len(bad_days)),
    "duplicate_rows": int(dup),
    "rt_actual_missing": int(rt_missing),
    "da_actual_missing": int(da_missing),
    "hour24_mapping_ok": bool(mapping_ok),
    "hour_business_range": [int(out["hour_business"].min()), int(out["hour_business"].max())],
    "actual_columns": ["rt_actual","da_actual"],
    "exogenous_actual_columns": [c for c in exo_actual if c in out.columns],
    "exogenous_forecast_columns": [c for c in exo_fc if c in out.columns],
    "legal_past_lag_columns": lag_cols,
    "da_oos_pred_column": "NOT_STORED_HERE (built by v31_lib.build_oos_da under rolling origin)",
    "leakage_check": "da_actual is an ACTUAL only; never used as a prediction; "
                     "legal DA proxy = rolling-origin OOS DA model output (da_oos_pred)",
    "verdict": "FULL_HISTORY_PANEL_BUILT_V31R1",
}
with open(VERDICT, "w", encoding="utf-8") as f:
    json.dump(verdict, f, ensure_ascii=False, indent=2)
print("[4] wrote verdict:", VERDICT)
print("DONE")
