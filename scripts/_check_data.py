"""Quick data check to find a valid smoke test date."""
import pandas as pd
from pathlib import Path

data_path = Path("data/shandong_pmos_hourly.xlsx")
print(f"Data file: {data_path} (exists={data_path.exists()})")

df = pd.read_excel(data_path)
print(f"Shape: {df.shape}")
print(f"Columns: {list(df.columns)[:20]}")

# Find timestamp
for c in ["时刻", "ds", "timestamp", "time"]:
    if c in df.columns:
        df["_ts"] = pd.to_datetime(df[c], errors="coerce")
        print(f"Timestamp column: '{c}'")
        break

if "_ts" in df.columns:
    print(f"Date range: {df['_ts'].min()} -> {df['_ts'].max()}")

    # Find latest complete business day
    # Business day D spans D 01:00 to D+1 00:00
    latest = df["_ts"].max()
    print(f"Latest timestamp: {latest}")

    # Look for a day with 24 complete hours
    # Go back a few days from latest
    from datetime import timedelta
    for offset in range(1, 5):
        candidate = (latest - timedelta(days=offset)).strftime("%Y-%m-%d")
        day_start = pd.Timestamp(candidate) + timedelta(hours=1)
        day_end = pd.Timestamp(candidate) + timedelta(days=1)
        mask = (df["_ts"] >= day_start) & (df["_ts"] <= day_end)
        n = mask.sum()
        print(f"  {candidate}: {n} hours (need 24)")

# Check actual price columns
for col in ["日前电价", "日前出清电价", "day_ahead_clearing_price", "实时电价", "realtime_price"]:
    if col in df.columns:
        non_null = df[col].notna().sum()
        print(f"  Column '{col}': {non_null} non-null values")
