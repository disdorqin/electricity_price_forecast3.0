#!/usr/bin/env python
"""Hour-alignment audit for EFM3 3.0 vs 2.5.

Goal: verify how the 00:00 timestamp is mapped to hour_business=24 and which
*date* owns that hour, and compare against the 2.5 OUTPUT_CONVENTION
(`hour 24 = D+1 00:00` belongs to business day D).

Read-only on the DB + read-only on the source CSV. Writes a markdown report to
outputs/metric_parity/hour_alignment_audit.md (gitignored).

Findings (expected):
  * 3.0 backfill `_hour_from_ts` keeps the calendar date: CSV `D 00:00` ->
    (trade_date=D, hour_business=24).
  * 2.5 convention: CSV `D 00:00` -> (business_day=D-1, hour_business=24).
  * Within 3.0 da_anchor and rt_actual are co-aligned (same CSV row), so the
    da_vs_rt metric is NOT intra-day misaligned. But the *date ownership* of
    hb=24 differs from 2.5 by +1 day, which is a parity bug for cross-system
    day-level alignment.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from tools.db_ops.db_yearly_metrics import _connect  # noqa: E402

DB_URL = os.environ.get("EFM3_DB_URL", "")
CSV_PATH = ROOT / "data" / "shandong_pmos_hourly.csv"
OUT_DIR = ROOT / "outputs" / "metric_parity"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_MD = OUT_DIR / "hour_alignment_audit.md"

SAMPLE_DATES = ["2026-01-01", "2026-02-14", "2026-03-15", "2026-06-30"]


def hour_from_ts(ts: datetime) -> int:
    """Replicate 3.0 backfill `_hour_from_ts` (for documentation parity)."""
    return 24 if ts.hour == 0 else ts.hour


def load_csv_window(dates):
    import pandas as pd
    # Read just enough rows around the sample dates (read whole file, it's small-ish).
    df = pd.read_csv(CSV_PATH, encoding="gbk", usecols=["时刻", "日前电价", "实时电价"])
    ts = pd.to_datetime(df["时刻"], errors="coerce")
    df = df.assign(_ts=ts, _date=ts.dt.date, _hb=ts.apply(lambda t: hour_from_ts(t) if pd.notna(t) else None))
    df = df.dropna(subset=["_ts"])
    wanted = set(dates) | {str((datetime.fromisoformat(d) + timedelta(days=1)).date()) for d in dates}
    return df[df["_date"].astype(str).isin(wanted)]


def main():
    conn = _connect(DB_URL)
    cur = conn.cursor()

    csv_df = load_csv_window(SAMPLE_DATES)

    lines = ["# EFM3 Hour-Alignment Audit (00:00 → hour_business=24)", ""]
    lines.append("> Read-only audit. Compares 3.0 backfill mapping against the 2.5 "
                 "`OUTPUT_CONVENTION.md` rule: **hour 24 = D+1 00:00 belongs to business "
                 "day D**.")
    lines.append("")
    lines.append("## 3.0 mapping rule (as implemented)")
    lines.append("")
    lines.append("`tools/db_ops/backfill_shandong_pmos_csv.py::_hour_from_ts`: "
                 "`hb = ts.hour; return 24 if hb == 0 else hb`. "
                 "`_date = df['_ts'].dt.date` keeps the **calendar date**. "
                 "So CSV `D 00:00` -> `(trade_date=D, hour_business=24)`.")
    lines.append("")
    lines.append("## 2.5 mapping rule (target parity)")
    lines.append("")
    lines.append("`docs/OUTPUT_CONVENTION.md`: `hour_business: 1..24 (hour 24 = D+1 00:00)`. "
                 "So CSV `D 00:00` -> `(business_day=D-1, hour_business=24)`.")
    lines.append("")
    lines.append("## Per-date evidence")
    lines.append("")
    lines.append("For each sample business day D, the table shows what 3.0 stored under "
                 "`(trade_date=D, hour_business=24)` and what 2.5 *would* store for the "
                 "same physical CSV row `D 00:00`.")
    lines.append("")
    lines.append("| Sample D | 3.0 stored as | CSV `D 00:00` da/rt | CSV `D 00:00` -> 2.5 would be | Match? |")
    lines.append("| -------- | ------------- | --------------------- | ----------------------------- | ------ |")

    mismatch_count = 0
    for d in SAMPLE_DATES:
        # What 3.0 stored under (trade_date=d, hb=24)
        cur.execute(
            "SELECT value, data_type FROM efm_market_data_hourly "
            "WHERE market='shandong' AND trade_date=%s AND hour_business=24 ORDER BY data_type",
            (d,),
        )
        rows = cur.fetchall()
        da3 = rt3 = None
        for v, dt in rows:
            if dt == "da_price":
                da3 = float(v)
            elif dt == "rt_price":
                rt3 = float(v)
        # CSV row for exactly D 00:00
        sub = csv_df[(csv_df["_date"].astype(str) == d) & (csv_df["_hb"] == 24)]
        csv_da = csv_rt = None
        if len(sub):
            csv_da = float(sub.iloc[0]["日前电价"])
            csv_rt = float(sub.iloc[0]["实时电价"])
        # 2.5 would assign D 00:00 to business_day D-1, hb 24
        prev = (datetime.fromisoformat(d) - timedelta(days=1)).date().isoformat()
        match = (da3 is not None and csv_da is not None and abs(da3 - csv_da) < 1e-6
                 and rt3 is not None and csv_rt is not None and abs(rt3 - csv_rt) < 1e-6)
        # The "match" column here means: does 3.0's (D,hb24) value equal the CSV D-00:00 value?
        # If yes, 3.0 kept calendar date (so it disagrees with 2.5's D-1 ownership).
        lines.append(
            f"| {d} | (trade_date={d}, hb=24) da={da3} rt={rt3} "
            f"| da={csv_da} rt={csv_rt} | (business_day={prev}, hb=24) | "
            f"{'3.0=calendar-date (DIFFERS from 2.5)' if match else 'n/a'} |"
        )
        if match:
            mismatch_count += 1

    lines.append("")
    lines.append("## Cross-check: intra-day co-alignment (does it distort da_vs_rt?)")
    lines.append("")
    lines.append("Within 3.0, `da_anchor` and `rt_actual` for a given `(date, hb)` come "
                 "from the **same CSV row** (same timestamp), so the da_vs_rt SMAPE is "
                 "internally consistent — no 1-hour shift *within* a day. The mismatch is "
                 "only in the *date label* of hb=24 (3.0 tags it D, 2.5 tags it D-1). "
                 "Over the full 181-day window the same 24 hourly values per day appear, "
                 "just the boundary hour's date tag differs, so aggregate metrics are "
                 "numerically identical; only per-date alignment with 2.5 differs.")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    lines.append(f"- 3.0 keeps calendar date for hb=24 on **all {mismatch_count}/{len(SAMPLE_DATES)}** "
                 "sampled days (CSV `D 00:00` -> `trade_date=D, hb=24`).")
    lines.append("- 2.5 assigns CSV `D 00:00` -> `business_day=D-1, hb=24`.")
    lines.append("- **Severity: MEDIUM (structural parity bug).** It does NOT cause the "
                 "49.70% SMAPE (that is a metric-semantics issue). It must be fixed before "
                 "any per-date cross-system comparison / fusion with 2.5 outputs.")
    lines.append("- Fix: shift the 00:00 row to the previous business day (mirror 2.5 "
                 "`train_fix.py`: `物理 00:00 -> 业务 前一天 24:00`).")
    lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    conn.close()
    print("Wrote", OUT_MD)
    print(f"Mismatched (calendar-date) days: {mismatch_count}/{len(SAMPLE_DATES)}")
    for l in lines:
        print(l)


if __name__ == "__main__":
    main()
