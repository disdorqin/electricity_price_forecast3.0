#!/usr/bin/env python
"""Inspect the Shandong PMOS hourly CSV without writing it to the database.

Reads ``data/shandong_pmos_hourly.csv`` (auto-detect GBK / GB18030 / utf-8-sig),
prints (and writes) a structural preview: columns, row count, date range,
per-day hour-count distribution, non-null rates for 日前电价 / 实时电价,
Jan-Jun 2026 coverage, and a list of anomaly dates (days without 24 rows).

Outputs (do NOT commit the CSV itself):
    outputs/db_backfill_preview/shandong_pmos_csv_inspect.md
    outputs/db_backfill_preview/shandong_pmos_csv_inspect.json

Usage:
    python tools/db_ops/inspect_shandong_pmos_csv.py \
        --csv-path data/shandong_pmos_hourly.csv \
        --start-date 2026-01-01 --end-date 2026-06-30
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

logging_imported = False
try:
    import logging
    logging_imported = True
except Exception:
    pass

if logging_imported:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    logger = logging.getLogger("inspect_pmos")
else:
    class _L:
        def info(self, *a, **k): print("[inspect_pmos]", *a)
        def warning(self, *a, **k): print("[inspect_pmos][WARN]", *a)
        def error(self, *a, **k): print("[inspect_pmos][ERR]", *a)
    logger = _L()

# ── Column candidate lists (Chinese auto-recognition) ─────────────
TIME_COLUMN_CANDIDATES = ["时刻", "时间", "timestamp", "ds", "date", "日期", "交易日期", "trade_date"]
DAY_COLUMN_CANDIDATES = ["日期", "交易日期", "date", "ds", "trade_date"]
HOUR_COLUMN_CANDIDATES = ["时刻", "时间", "hour", "hour_business", "interval"]
DA_COLUMN_CANDIDATES = ["日前电价", "日前出清电价", "日前统一出清价", "dayahead_price", "da_price", "日前节点边际电价"]
RT_COLUMN_CANDIDATES = ["实时电价", "实时出清电价", "实时统一出清价", "actual_price", "realtime_price", "rt_price", "实时节点边际电价"]


def _detect_encoding(csv_path: Path) -> str:
    for enc in ("gbk", "gb18030", "utf-8-sig", "utf-8"):
        try:
            with open(csv_path, "r", encoding=enc) as fh:
                fh.read(4096)
            logger.info("Detected encoding: %s", enc)
            return enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise RuntimeError(f"Could not decode {csv_path} with any known encoding")


def _pick_column(columns, candidates):
    for c in candidates:
        if c in columns:
            return c
    return None


def _hour_from_ts(ts: datetime) -> int:
    """Canonical hour_business mapping: 00:00 -> 24, 01:00 -> 1, ..."""
    hb = ts.hour
    return 24 if hb == 0 else hb


def main():
    ap = argparse.ArgumentParser(description="Inspect Shandong PMOS hourly CSV (no DB writes).")
    ap.add_argument("--csv-path", default="data/shandong_pmos_hourly.csv")
    ap.add_argument("--start-date", default="2026-01-01")
    ap.add_argument("--end-date", default="2026-06-30")
    ap.add_argument("--output-dir", default="outputs/db_backfill_preview")
    ap.add_argument("--encoding", default=None, help="Force encoding (default: auto-detect)")
    args = ap.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    import pandas as pd

    enc = args.encoding or _detect_encoding(csv_path)
    df = pd.read_csv(csv_path, encoding=enc)
    columns = list(df.columns)
    logger.info("Loaded %d rows, %d columns", len(df), len(columns))

    time_col = _pick_column(columns, TIME_COLUMN_CANDIDATES)
    da_col = _pick_column(columns, DA_COLUMN_CANDIDATES)
    rt_col = _pick_column(columns, RT_COLUMN_CANDIDATES)

    if time_col is None:
        raise SystemExit(f"Could not identify a time column. Columns: {columns}")
    if da_col is None or rt_col is None:
        raise SystemExit(
            f"Could not identify day-ahead / real-time price columns. "
            f"da_col={da_col} rt_col={rt_col}. Columns: {columns}"
        )

    # Parse timestamps
    ts = pd.to_datetime(df[time_col], errors="coerce")
    df = df.assign(_ts=ts)
    df = df.dropna(subset=["_ts"]).copy()
    df["_date"] = df["_ts"].dt.date
    df["_hb"] = df["_ts"].apply(_hour_from_ts)

    all_dates = sorted(df["_date"].unique())
    date_min = all_dates[0] if all_dates else None
    date_max = all_dates[-1] if all_dates else None

    # Per-day hour-count distribution
    per_day_hours = df.groupby("_date")["_hb"].apply(lambda s: sorted(s.tolist())).to_dict()
    hour_dist = Counter(len(v) for v in per_day_hours.values())

    # Jan-Jun 2026 coverage
    sd = date.fromisoformat(args.start_date)
    ed = date.fromisoformat(args.end_date)
    jan_jun_days = {(sd + timedelta(n)).isoformat() for n in range((ed - sd).days + 1)}
    covered = [d.isoformat() for d in all_dates if sd <= d <= ed]
    missing = sorted(set(jan_jun_days) - {d.isoformat() for d in all_dates if sd <= d <= ed})

    # Anomaly dates: within Jan-Jun window but not exactly 24 distinct hours
    anomalies = []
    for d_iso in jan_jun_days:
        d = date.fromisoformat(d_iso)
        hrs = per_day_hours.get(d, [])
        distinct = len(set(hrs))
        if distinct != 24:
            anomalies.append({"date": d_iso, "distinct_hours": distinct})

    # Non-null rates for price columns
    da_nonnull = int(df[da_col].notna().sum())
    rt_nonnull = int(df[rt_col].notna().sum())
    da_rate = round(100.0 * da_nonnull / len(df), 2) if len(df) else 0.0
    rt_rate = round(100.0 * rt_nonnull / len(df), 2) if len(df) else 0.0

    # Within Jan-Jun window: price availability per day
    jj = df[(df["_date"] >= sd) & (df["_date"] <= ed)]
    jj_days_with_da = int(jj.dropna(subset=[da_col])["_date"].nunique())
    jj_days_with_rt = int(jj.dropna(subset=[rt_col])["_date"].nunique())

    report = {
        "csv_path": str(csv_path),
        "encoding": enc,
        "columns": columns,
        "row_count": int(len(df)),
        "date_min": date_min.isoformat() if date_min else None,
        "date_max": date_max.isoformat() if date_max else None,
        "time_column": time_col,
        "dayahead_column": da_col,
        "realtime_column": rt_col,
        "hour_count_distribution": {str(k): int(v) for k, v in sorted(hour_dist.items())},
        "dayahead_nonnull": da_nonnull,
        "dayahead_nonnull_rate_pct": da_rate,
        "realtime_nonnull": rt_nonnull,
        "realtime_nonnull_rate_pct": rt_rate,
        "jan_jun_window": {"start": args.start_date, "end": args.end_date},
        "jan_jun_total_days": len(jan_jun_days),
        "jan_jun_covered_days": len(covered),
        "jan_jun_missing_days": missing,
        "jan_jun_days_with_da_price": jj_days_with_da,
        "jan_jun_days_with_rt_price": jj_days_with_rt,
        "anomaly_dates_jan_jun": anomalies,
    }

    # Console summary
    logger.info("date range: %s -> %s", report["date_min"], report["date_max"])
    logger.info("day-ahead col=%s non-null=%.2f%%  realtime col=%s non-null=%.2f%%",
                da_col, da_rate, rt_col, rt_rate)
    logger.info("Jan-Jun covered %d/%d days; missing=%s",
                len(covered), len(jan_jun_days), missing or "none")
    logger.info("anomaly dates (≠24h): %d", len(anomalies))

    # Write outputs
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "shandong_pmos_csv_inspect.md"
    json_path = out_dir / "shandong_pmos_csv_inspect.json"

    md = ["# Shandong PMOS Hourly CSV — Inspection Report", ""]
    md.append(f"- csv_path: `{csv_path}`")
    md.append(f"- encoding: {enc}")
    md.append(f"- row_count: {len(df)}")
    md.append(f"- date range: {report['date_min']} → {report['date_max']}")
    md.append(f"- time_column: `{time_col}`")
    md.append(f"- dayahead_column: `{da_col}` (non-null {da_nonnull}, {da_rate}%)")
    md.append(f"- realtime_column: `{rt_col}` (non-null {rt_nonnull}, {rt_rate}%)")
    md.append("")
    md.append("## Columns")
    md.append("")
    md.append("| # | column |")
    md.append("| - | ------ |")
    for i, c in enumerate(columns, 1):
        md.append(f"| {i} | {c} |")
    md.append("")
    md.append("## Hour-count distribution (rows per day)")
    md.append("")
    md.append("| distinct hours in day | num days |")
    md.append("| --------------------: | -------: |")
    for k in sorted(hour_dist):
        md.append(f"| {k} | {hour_dist[k]} |")
    md.append("")
    md.append(f"## Jan-Jun 2026 coverage ({args.start_date} → {args.end_date})")
    md.append("")
    md.append(f"- total days in window: {len(jan_jun_days)}")
    md.append(f"- covered (any row): {len(covered)}")
    md.append(f"- days with day-ahead price: {jj_days_with_da}")
    md.append(f"- days with real-time price: {jj_days_with_rt}")
    md.append(f"- missing days: {missing or 'none'}")
    md.append("")
    md.append(f"## Anomaly dates (≠24 distinct hours) — count: {len(anomalies)}")
    md.append("")
    if anomalies:
        md.append("| date | distinct_hours |")
        md.append("| ---- | -------------: |")
        for a in anomalies:
            md.append(f"| {a['date']} | {a['distinct_hours']} |")
    else:
        md.append("None.")
    md_path.write_text("\n".join(md), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Wrote %s and %s", md_path, json_path)

    # Non-zero exit if missing days within window (so CI / caller can detect)
    if missing:
        logger.warning("Jan-Jun window has %d missing days: %s", len(missing), missing[:10])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
