"""
Emergency fallback — historical median when the normal pipeline cannot deliver.

This is a single-level fallback only: read historical data, compute per-hour
medians, and write a ``submission_ready.csv``.  It does NOT write to the
prediction ledger, avoiding contamination of future weight learning.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

FALLBACK_COLUMNS = [
    "business_day", "ds", "hour_business", "period",
    "dayahead_price", "realtime_price",
]


def try_emergency_fallback(
    target_date: str,
    data_path: str | Path,
    runs_root: str | Path,
    reason: str = "normal pipeline failed to produce valid output",
) -> dict:
    """Attempt an emergency fallback delivery using historical median prices.

    Steps
    -----
    1. Read historical data from *data_path* (``.xlsx`` or ``.csv``).
    2. Parse ``business_day`` and ``hour_business`` from the timestamp column.
    3. Compute per-hour median of dayahead and realtime prices using the most
       recent available history (prefer 7 days, then 30, then all).
    4. Write ``submission_ready.csv`` with the fixed 6-column schema.
    5. Write ``fallback_report.json`` and ``fallback_report.md``.

    Returns
    -------
    dict with keys: success, fallback_method, fallback_level, reason,
    output_path, fallback_report_json, fallback_report_md, warnings, errors.
    """
    data_path = Path(data_path)
    runs_root = Path(runs_root)
    warnings: list[str] = []
    errors: list[str] = []
    target_dt = pd.Timestamp(target_date)

    try:
        if data_path.suffix.lower() == ".csv":
            raw = pd.read_csv(data_path)
        else:
            raw = pd.read_excel(data_path)
    except Exception as exc:
        errors.append(f"cannot read data_path {data_path}: {exc}")
        return _fallback_result(False, warnings, errors, reason)

    time_col = _resolve_time_column(raw)
    if time_col is None:
        errors.append(
            f"no time column found in {data_path}. "
            f"Tried: ds, 时刻, 时间, datetime. "
            f"Columns: {list(raw.columns)}"
        )
        return _fallback_result(False, warnings, errors, reason)

    df = raw.copy()
    df["_ts"] = pd.to_datetime(df[time_col], errors="coerce")
    df = df.dropna(subset=["_ts"])

    da_col = _resolve_column(df, ["日前电价", "dayahead_price", "day_ahead_price", "da_price"])
    rt_col = _resolve_column(df, ["实时电价", "realtime_price", "real_time_price", "rt_price"])

    if da_col is None:
        for c in df.columns:
            if "dayahead" in c.lower() or "日前" in c or "da_price" in c.lower():
                da_col = c
                break
    if rt_col is None:
        for c in df.columns:
            if "realtime" in c.lower() or "实时" in c or "rt_price" in c.lower():
                rt_col = c
                break

    if da_col is None and rt_col is None:
        errors.append(
            f"neither dayahead nor realtime price column found. "
            f"Columns: {list(df.columns)}"
        )
        return _fallback_result(False, warnings, errors, reason)

    # Formal business-hour convention:
    #   ts.hour == 0 -> business_day = ts.date - 1, hour_business = 24
    #   ts.hour == 1 -> business_day = ts.date,     hour_business = 1
    #   ts.hour == 23 -> business_day = ts.date,    hour_business = 23
    bd_and_hb = df["_ts"].apply(_to_business_day_hour)
    df["business_day"] = [x[0] for x in bd_and_hb]
    df["hour_business"] = [x[1] for x in bd_and_hb]

    # Filter on business_day after mapping so D's midnight (→ D-1 hour 24)
    # is correctly included as history.
    hist = df[df["business_day"] < target_date].copy()
    if hist.empty:
        errors.append(f"no historical data before {target_date}")
        return _fallback_result(False, warnings, errors, reason)

    logger.info(f"Fallback: {len(hist)} historical rows before {target_date}")

    latest_day = hist["business_day"].max()
    max_days_available = hist["business_day"].nunique()
    logger.info(
        f"Fallback: latest history day={latest_day}, "
        f"total unique days={max_days_available}"
    )

    medians = _compute_hourly_medians(hist, da_col, rt_col, target_dt)
    fallback_level = _determine_fallback_level(max_days_available, warnings)

    rows = []
    for h in range(1, 25):
        if h <= 23:
            ds_ts = target_dt + pd.Timedelta(hours=h)
        else:
            ds_ts = target_dt + pd.Timedelta(days=1)  # hour 24 -> D+1 00:00

        if 1 <= h <= 8:
            period = "1_8"
        elif 9 <= h <= 16:
            period = "9_16"
        else:
            period = "17_24"

        m = medians.get(h, {})
        rows.append({
            "business_day": target_date,
            "ds": ds_ts.strftime("%Y-%m-%d %H:%M:%S"),
            "hour_business": h,
            "period": period,
            "dayahead_price": m.get("dayahead"),
            "realtime_price": m.get("realtime"),
        })

    out_df = pd.DataFrame(rows)
    out_df = out_df[FALLBACK_COLUMNS]

    final_dir = runs_root / target_date / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    sub_path = final_dir / "submission_ready.csv"
    out_df.to_csv(sub_path, index=False)
    logger.info(f"Fallback submission_ready.csv -> {sub_path}")

    fallback_manifest = {
        "fallback_used": True,
        "fallback_method": "historical_same_hour_median",
        "fallback_level": fallback_level,
        "reason": reason,
        "triggered_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "historical_days_used": max_days_available,
        "warnings": warnings,
        "errors": errors,
        "output_path": str(sub_path),
    }

    fb_json_path = final_dir / "fallback_report.json"
    with open(fb_json_path, "w", encoding="utf-8") as f:
        json.dump(fallback_manifest, f, indent=2, ensure_ascii=False, default=str)

    fb_md_path = final_dir / "fallback_report.md"
    with open(fb_md_path, "w", encoding="utf-8") as f:
        f.write(_fallback_markdown(target_date, fallback_manifest, rows))

    logger.info(f"Fallback report -> {fb_json_path}")

    return _fallback_result(
        True,
        warnings,
        errors,
        reason,
        output_path=str(sub_path),
        fallback_report_json=str(fb_json_path),
        fallback_report_md=str(fb_md_path),
        fallback_level=fallback_level,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_time_column(df: pd.DataFrame) -> str | None:
    """Find the timestamp column by name."""
    for name in ["ds", "时刻", "时间", "datetime"]:
        if name in df.columns:
            return name
    return None


def _resolve_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Find the first matching column name."""
    for name in candidates:
        if name in df.columns:
            return name
    return None


def _to_business_day_hour(ts: pd.Timestamp) -> tuple[str, int]:
    """Convert a timestamp to (business_day, hour_business) per formal convention.

    Formal convention:
      - hour 0 (midnight) -> previous day, hour_business 24
      - hour 1 (01:00)   -> same day,     hour_business 1
      - hour 23 (23:00)  -> same day,     hour_business 23
    """
    if ts.hour == 0:
        prev = ts - pd.Timedelta(days=1)
        return (prev.strftime("%Y-%m-%d"), 24)
    return (ts.strftime("%Y-%m-%d"), ts.hour)


def _compute_hourly_medians(
    hist: pd.DataFrame,
    da_col: str | None,
    rt_col: str | None,
    target_dt: pd.Timestamp,
) -> dict[int, dict[str, float | None]]:
    """Compute per-hour median prices with tiered fallback.

    Priority:
      1. Last 7 business days same hour
      2. Last 30 business days same hour
      3. All history same hour
      4. Global median
    """
    all_days = sorted(hist["business_day"].unique(), reverse=True)
    last_7 = all_days[:7] if len(all_days) >= 7 else all_days
    last_30 = all_days[:30] if len(all_days) >= 30 else all_days

    medians: dict[int, dict[str, float | None]] = {}

    for h in range(1, 25):
        hour_data = hist[hist["hour_business"] == h]
        if hour_data.empty:
            medians[h] = {"dayahead": None, "realtime": None}
            continue

        da_val = rt_val = None

        h7 = hour_data[hour_data["business_day"].isin(last_7)]
        if da_col and not h7.empty:
            da_val = float(h7[da_col].median())
        if rt_col and not h7.empty:
            rt_val = float(h7[rt_col].median())

        if da_val is None or rt_val is None:
            h30 = hour_data[hour_data["business_day"].isin(last_30)]
            if da_val is None and da_col and not h30.empty:
                da_val = float(h30[da_col].median())
            if rt_val is None and rt_col and not h30.empty:
                rt_val = float(h30[rt_col].median())

        if da_val is None and da_col and not hour_data.empty:
            da_val = float(hour_data[da_col].median())
        if rt_val is None and rt_col and not hour_data.empty:
            rt_val = float(hour_data[rt_col].median())

        medians[h] = {"dayahead": da_val, "realtime": rt_val}

    global_da = float(hist[da_col].median()) if da_col else None
    global_rt = float(hist[rt_col].median()) if rt_col else None

    for h in range(1, 25):
        if medians[h]["dayahead"] is None:
            medians[h]["dayahead"] = global_da
        if medians[h]["realtime"] is None:
            medians[h]["realtime"] = global_rt

    return medians


def _determine_fallback_level(max_days: int, warnings: list) -> str:
    """Determine which tier of fallback was used."""
    if max_days >= 7:
        return "emergency_baseline"
    if max_days >= 1:
        warnings.append(f"only {max_days} day(s) of historical data available for fallback")
        return "sparse_history"
    warnings.append("no historical data for per-hour median; using global median")
    return "global_median"


def _fallback_result(
    success: bool,
    warnings: list[str],
    errors: list[str],
    reason: str,
    output_path: str = "",
    fallback_report_json: str = "",
    fallback_report_md: str = "",
    fallback_level: str | None = None,
) -> dict:
    return {
        "success": success,
        "fallback_method": "historical_same_hour_median",
        "fallback_level": fallback_level or ("emergency_baseline" if success else "failed"),
        "reason": reason,
        "output_path": output_path,
        "fallback_report_json": fallback_report_json,
        "fallback_report_md": fallback_report_md,
        "warnings": warnings,
        "errors": errors,
    }


def _fallback_markdown(
    target_date: str,
    fb: dict,
    rows: list[dict],
) -> str:
    """Generate a human-readable fallback report in Markdown."""
    lines = [
        f"# Emergency Fallback Report — {target_date}",
        "",
        f"**Method:** {fb['fallback_method']}",
        f"**Level:** {fb['fallback_level']}",
        f"**Reason:** {fb['reason']}",
        f"**Historical days used:** {fb['historical_days_used']}",
    ]
    if fb.get("output_path"):
        lines.append(f"**Output:** `{fb['output_path']}`")
    lines.append("")

    if fb["warnings"]:
        lines.append("## Warnings")
        for w in fb["warnings"]:
            lines.append(f"- {w}")

    lines.append("")
    lines.append("## Hourly Values")
    lines.append("")
    lines.append("| hour_business | dayahead_price | realtime_price |")
    lines.append("|---|---|---|")
    for r in rows:
        da = f"{r['dayahead_price']:.2f}" if r["dayahead_price"] is not None else "N/A"
        rt = f"{r['realtime_price']:.2f}" if r["realtime_price"] is not None else "N/A"
        lines.append(f"| {r['hour_business']} | {da} | {rt} |")

    lines.append("")
    lines.append("## Action Required")
    lines.append(
        "This is an **emergency fallback delivery**, not a normal model output. "
        "After resolving the underlying issue, re-run the normal pipeline with "
        "`--force` to restore ledger continuity."
    )

    return "\n".join(lines)
