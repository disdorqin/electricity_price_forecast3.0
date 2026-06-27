import pandas as pd
from pathlib import Path

root = Path("outputs/smoke")

# Check dayahead long table
da = pd.read_csv(root / "runs/2026-02-24/dayahead/prediction/all_model_predictions_long.csv")
rt = pd.read_csv(root / "runs/2026-02-24/realtime/prediction/all_model_predictions_long.csv")

print("=== DAYAHEAD ===")
print("rows:", len(da))
print("models:", sorted(da["model_name"].unique()))
print("business_day:", da["business_day"].unique())
print("hour range:", da["hour_business"].min(), "-", da["hour_business"].max())
print("y_pred NaN count:", da["y_pred"].isna().sum())
g = da.groupby("model_name").size()
print("per model:", g.to_dict())

print("\n=== REALTIME ===")
print("rows:", len(rt))
print("models:", sorted(rt["model_name"].unique()))
print("business_day:", rt["business_day"].unique())
print("hour range:", rt["hour_business"].min(), "-", rt["hour_business"].max())
print("y_pred NaN count:", rt["y_pred"].isna().sum())
g = rt.groupby("model_name").size()
print("per model:", g.to_dict())

# Check hour 24 ds
if "ds" in da.columns:
    h24 = da[da["hour_business"] == 24]
    if len(h24):
        print(f"\nhour 24 ds values: {h24['ds'].unique()}")
else:
    print("\n(no ds column in CSV, checking parquet)")
    pda = pd.read_parquet(root / "ledger/dayahead/prediction/prediction_ledger.parquet")
    h24 = pda[pda["hour_business"] == 24]
    if len(h24):
        print(f"hour 24 ds values: {h24['ds'].unique()}")

# Check smoke report
import json
report = root / "runs/2026-02-24/smoke_report.json"
if report.exists():
    with open(report) as f:
        r = json.load(f)
    print(f"\nsmoke_report status: {r.get('smoke_status')}")
else:
    print("\nno smoke_report.json found")
