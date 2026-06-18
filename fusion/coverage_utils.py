from __future__ import annotations

from pathlib import Path

import pandas as pd


def latest_timestamp_from_xlsx(data_path: str | Path) -> pd.Timestamp:
    df = pd.read_excel(data_path, usecols=["时刻"])
    ts = pd.to_datetime(df["时刻"], errors="coerce").dropna()
    if ts.empty:
        raise ValueError(f"No valid timestamps found in {data_path}")
    return ts.max()


def latest_complete_target_day(data_path: str | Path) -> pd.Timestamp:
    latest_ts = latest_timestamp_from_xlsx(data_path)
    latest_day = latest_ts.normalize()
    if latest_ts.hour == 0:
        return latest_day - pd.Timedelta(days=1)
    return latest_day


def latest_cross_model_safe_target_day(data_path: str | Path) -> pd.Timestamp:
    latest_ts = latest_timestamp_from_xlsx(data_path)
    latest_day = latest_ts.normalize()
    if latest_ts.hour == 0:
        # Fusion must satisfy the strictest model family. With a file ending at
        # next-day 00:00 only, some realtime pipelines still cannot form the last
        # full target day because they require the full target-day label block.
        # Keep the shared intersection across dayahead + realtime exporters.
        return latest_day - pd.Timedelta(days=3)
    if latest_ts.hour == 23:
        return latest_day
    return latest_day - pd.Timedelta(days=1)
