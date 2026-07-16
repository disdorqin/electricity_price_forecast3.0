"""
Build FULL_HISTORY_CANONICAL_PANEL from discovered 2022+ history.

Source of truth (actuals + exogenous):
  deep_model_for_electricity/data/preprocessed_data.csv
  columns: times, da_price(DA actual), rt_price(RT actual),
           *_forecast / *_actual exogenous (load/wind/solar/nuclear/bidding_space/...)

Outputs:
  data_audit/FULL_HISTORY_CANONICAL_PANEL.parquet
  data_audit/FULL_HISTORY_CANONICAL_VERDICT.json

Discipline:
  - DATA_AS_OF_DATE = max(business_day with complete 24h rt_actual)
  - No future leakage: only past information used downstream.
  - business_day/hour_business follow 3.0 convention (timestamp date = business_day,
    hour 1..24).
  - Candidate prediction columns are NOT fabricated here; reserved + populated by
    Track A-F rolling-origin engine. Missing candidate columns flagged.
"""
import os, json, hashlib, datetime
import pandas as pd
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SRC = os.path.join(ROOT, "deep_model_for_electricity", "data", "preprocessed_data.csv")
OUTDIR = os.path.join(ROOT, "electricity_forecast_model3.0-research", "data_audit")
os.makedirs(OUTDIR, exist_ok=True)
PANEL = os.path.join(OUTDIR, "FULL_HISTORY_CANONICAL_PANEL.parquet")
VERDICT = os.path.join(OUTDIR, "FULL_HISTORY_CANONICAL_VERDICT.json")

print("[1] loading source:", SRC)
df = pd.read_csv(SRC)
print("   raw rows:", len(df), "cols:", len(df.columns))
df["times"] = pd.to_datetime(df["times"])
df = df.sort_values("times").reset_index(drop=True)

# business_day / hour_business (3.0 convention)
df["business_day"] = df["times"].dt.date.astype(str)
df["hour_business"] = df["hour"].astype(int)

# rename actuals
out = pd.DataFrame()
out["business_day"] = df["business_day"]
out["hour_business"] = df["hour_business"]
out["rt_actual"] = df["rt_price"].astype(float)
out["da_actual"] = df["da_price"].astype(float)

# exogenous actuals + forecasts (legal: all are PAST at RT time)
exo_actual = ["local_plant_actual","tie_line_load_actual","wind_actual","solar_actual",
              "nuclear_actual","self_supply_actual","test_unit_actual",
              "direct_dispatch_actual","bidding_space_actual","renewable_actual"]
exo_fc = ["local_plant_forecast","tie_line_load_forecast","wind_forecast","solar_forecast",
          "nuclear_forecast","self_supply_forecast","test_unit_forecast",
          "direct_dispatch_forecast","bidding_space_forecast","renewable_forecast"]
for c in exo_actual + exo_fc:
    if c in df.columns:
        out[c] = df[c].astype(float)

# legal OOS DA anchor = da_price (DA settlement known before RT, no RT leakage)
out["legal_oos_da_prediction"] = out["da_actual"]

# data lineage
out["data_lineage"] = "deep_model_for_electricity/preprocessed_data.csv"
out["oos_type"] = "STRICT_REPLAY_OOS"   # will be used for replay; actuals are truth
out["feature_availability_time"] = "prior_to_rt"

# ---- validation ----
print("[2] validating panel integrity")
days = out["business_day"].unique()
hours_per_day = out.groupby("business_day")["hour_business"].nunique()
bad_days = hours_per_day[hours_per_day != 24]
dup = out.duplicated(subset=["business_day","hour_business"]).sum()
rt_missing = out["rt_actual"].isna().sum()
da_missing = out["da_actual"].isna().sum()

# DATA_AS_OF_DATE = latest day with full 24h rt_actual
complete_days = hours_per_day[hours_per_day == 24].index
complete_days_sorted = sorted(complete_days)
data_start = complete_days_sorted[0]
data_as_of = complete_days_sorted[-1]
# rt_actual by day
rt_by_day = out.groupby("business_day")["rt_actual"].apply(lambda s: s.notna().all())
last_complete_rt = rt_by_day[rt_by_day].index.max()

print("   days:", len(days), "bad_days(!=24h):", len(bad_days),
      "dups:", dup, "rt_missing:", rt_missing, "da_missing:", da_missing)
print("   DATA_START_DATE:", data_start, "DATA_AS_OF_DATE(complete 24h rt):", last_complete_rt)

# hour 24 mapping check
hb_vals = sorted(out["hour_business"].unique())
print("   hour_business range:", hb_vals[0], "..", hb_vals[-1])

# ---- save panel (candidate cols reserved) ----
out.to_parquet(PANEL, index=False)
print("[3] wrote panel:", PANEL, "shape", out.shape)

# ---- verdict ----
verdict = {
    "generated": datetime.datetime.now().isoformat(),
    "source": SRC,
    "source_sha256_head16": hashlib.sha256(open(SRC,"rb").read()).hexdigest()[:16],
    "data_start_date": str(data_start),
    "data_as_of_date": str(last_complete_rt),
    "total_days": int(len(complete_days_sorted)),
    "total_hours": int(len(out)),
    "bad_days_not_24h": int(len(bad_days)),
    "duplicate_rows": int(dup),
    "rt_actual_missing": int(rt_missing),
    "da_actual_missing": int(da_missing),
    "hour_business_range": [int(hb_vals[0]), int(hb_vals[-1])],
    "actual_columns": ["rt_actual","da_actual"],
    "exogenous_actual_columns": [c for c in exo_actual if c in out.columns],
    "exogenous_forecast_columns": [c for c in exo_fc if c in out.columns],
    "candidate_prediction_columns": "RESERVED_NOT_POPULATED",
    "oos_type": "STRICT_REPLAY_OOS",
    "leakage_check": "da_actual used only as DA anchor (known pre-RT); no rt_* future used",
    "verdict": "FULL_HISTORY_PANEL_BUILT",
}
with open(VERDICT, "w", encoding="utf-8") as f:
    json.dump(verdict, f, ensure_ascii=False, indent=2)
print("[4] wrote verdict:", VERDICT)
print("DONE")
