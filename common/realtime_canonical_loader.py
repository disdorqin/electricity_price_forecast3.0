"""Canonical loader for realtime actual and DA anchor prices.

Corrects the xlsx midnight hour-index shift:
  xlsx raw sorted by ds: [00:00, 01:00, ..., 23:00]
  expected hb 1..24:      [01:00, 02:00, ..., 23:00, 00:00]

Mapping rule:
  ds_hour = 0 (midnight) -> hour_business = 24
  ds_hour = 1             -> hour_business = 1
  ...
  ds_hour = 23            -> hour_business = 23
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


def _load_xlsx(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    df["ds"] = pd.to_datetime(df["时刻"])
    return df


def _extract_day_array(
    df: pd.DataFrame, day_str: str, column_keyword: str,
) -> Optional[np.ndarray]:
    """Extract 24-hour array ordered by hour_business 1..24.

    hour_business 1  = 01:00
    hour_business 2  = 02:00
    ...
    hour_business 23 = 23:00
    hour_business 24 = 00:00 (midnight, same business_day)
    """
    d = pd.Timestamp(day_str)
    day_data = df[df["ds"].dt.date == d.date()].copy()
    if len(day_data) == 0:
        return None
    cols = [c for c in day_data.columns if column_keyword in c]
    if not cols:
        return None
    # Ensure sorted by ds so we have [00:00, 01:00, ..., 23:00]
    day_data = day_data.sort_values("ds")
    vals = day_data[cols[0]].values
    if len(vals) != 24:
        return None
    # Shift: midnight (index 0) → last position (hour_business 24)
    # [00:00, 01:00, ..., 23:00] → [01:00, 02:00, ..., 23:00, 00:00]
    return np.concatenate([vals[1:], vals[:1]])


def load_realtime_actual_canonical(
    xlsx_path: Path, day_str: str,
) -> Optional[np.ndarray]:
    """Return 24-hour realtime actual prices ordered by hour_business 1..24."""
    df = getattr(load_realtime_actual_canonical, "_cached_df", None)
    if df is None:
        df = _load_xlsx(xlsx_path)
        load_realtime_actual_canonical._cached_df = df
    return _extract_day_array(df, day_str, "实时电价")


def load_dayahead_anchor_canonical(
    xlsx_path: Path, day_str: str,
) -> Optional[np.ndarray]:
    """Return 24-hour DA anchor prices ordered by hour_business 1..24."""
    df = getattr(load_dayahead_anchor_canonical, "_cached_df", None)
    if df is None:
        df = _load_xlsx(xlsx_path)
        load_dayahead_anchor_canonical._cached_df = df
    return _extract_day_array(df, day_str, "日前电价")


def validate_hour_business_mapping(
    xlsx_path: Path, day_str: str,
) -> dict:
    """Validate hour_business mapping for a single day."""
    df = _load_xlsx(xlsx_path)
    d = pd.Timestamp(day_str)
    day_data = df[df["ds"].dt.date == d.date()].sort_values("ds")
    result = {
        "day": day_str,
        "n_rows": len(day_data),
        "ds_range": f"{day_data['ds'].iloc[0]} -> {day_data['ds'].iloc[-1]}",
        "hours": {},
        "valid": True,
        "errors": [],
    }
    if len(day_data) != 24:
        result["valid"] = False
        result["errors"].append(f"Expected 24 rows, got {len(day_data)}")
        return result

    # Check mapping
    expected = {
        24: 0,  # hb 24 -> hour 0 (midnight)
        1: 1,   # hb 1  -> hour 1
        2: 2,
        3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8,
        9: 9, 10: 10, 11: 11, 12: 12, 13: 13,
        14: 14, 15: 15, 16: 16, 17: 17, 18: 18,
        19: 19, 20: 20, 21: 21, 22: 22, 23: 23,
    }
    for idx, (_, row) in enumerate(day_data.iterrows()):
        h = row["ds"].hour  # 0..23
        hb = h if h != 0 else 24
        result["hours"][f"xlsx_idx={idx}"] = {
            "ds": str(row["ds"]),
            "hour": h,
            "expected_hb": hb,
        }
        if expected.get(hb) != h:
            result["errors"].append(
                f"xlsx idx {idx}: ds={row['ds']} hour={h} -> hb={hb}, "
                f"expected hb mapping broken"
            )
    result["valid"] = len(result["errors"]) == 0
    return result


def canonical_smape_floor50(
    y_true: np.ndarray, y_pred: np.ndarray, floor: float = 50.0,
) -> float:
    """sMAPE with floor=50, matching 2.5 metrics.

    Both y_true and y_pred must be ordered by hour_business 1..24.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    valid = ~(np.isnan(y_true) | np.isnan(y_pred))
    if valid.sum() == 0:
        return float("nan")
    y_true = np.maximum(y_true[valid], floor)
    y_pred = np.maximum(y_pred[valid], floor)
    denom = np.abs(y_true) + np.abs(y_pred)
    mask = denom > 0
    if mask.sum() == 0:
        return float("nan")
    return float(np.mean(200 * np.abs(y_true[mask] - y_pred[mask]) / denom[mask]))


def build_canonical_ledger(
    xlsx_path: Path, output_dir: Path, months: list[str],
) -> dict:
    """Build canonical actual and DA anchor CSVs for given months."""
    output_dir.mkdir(parents=True, exist_ok=True)
    df = _load_xlsx(xlsx_path)

    actual_rows = []
    da_rows = []
    stats = {}

    for month in months:
        y, m = month.split("-")
        d0 = pd.Timestamp(year=int(y), month=int(m), day=1)
        d1 = d0 + pd.offsets.MonthEnd(1)
        month_days = [d.strftime("%Y-%m-%d") for d in pd.date_range(d0, d1, freq="D")]
        month_ok = 0
        month_err = 0

        for day_str in month_days:
            act = _extract_day_array(df, day_str, "实时电价")
            da = _extract_day_array(df, day_str, "日前电价")
            if act is None or da is None:
                month_err += 1
                continue
            month_ok += 1
            for hb in range(1, 25):
                actual_rows.append({
                    "target_day": day_str, "business_day": day_str,
                    "hour_business": hb,
                    "period": "1_8" if hb <= 8 else "9_16" if hb <= 16 else "17_24",
                    "y_true": act[hb - 1],
                })
                da_rows.append({
                    "target_day": day_str, "business_day": day_str,
                    "hour_business": hb,
                    "period": "1_8" if hb <= 8 else "9_16" if hb <= 16 else "17_24",
                    "da_anchor": da[hb - 1],
                })
        stats[month] = {"ok": month_ok, "err": month_err, "total": len(month_days)}

    pd.DataFrame(actual_rows).to_csv(
        output_dir / "realtime_actual_canonical.csv", index=False, encoding="utf-8")
    pd.DataFrame(da_rows).to_csv(
        output_dir / "dayahead_anchor_canonical.csv", index=False, encoding="utf-8")

    return stats
