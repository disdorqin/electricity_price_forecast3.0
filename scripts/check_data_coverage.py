import pandas as pd
from pathlib import Path

p = Path("data/shandong_pmos_hourly.xlsx")
df = pd.read_excel(p)
ts_col = None
for c in ["时刻", "ds", "timestamp", "time", "datetime"]:
    if c in df.columns:
        ts_col = c
        break
if ts_col is None:
    raise SystemExit(f"No timestamp column. columns={list(df.columns)}")

df["ds"] = pd.to_datetime(df[ts_col], errors="coerce")
print("rows:", len(df))
print("min ds:", df["ds"].min())
print("max ds:", df["ds"].max())
print("columns:", list(df.columns))

required_start = pd.Timestamp("2026-01-25 01:00:00")
required_end = pd.Timestamp("2026-02-25 00:00:00")
ok = df["ds"].min() <= required_start and df["ds"].max() >= required_end
print("coverage_ok:", ok)
if not ok:
    raise SystemExit("Data coverage not enough for 2026-02-24 formal run.")
