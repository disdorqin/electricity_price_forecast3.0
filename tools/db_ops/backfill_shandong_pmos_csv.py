#!/usr/bin/env python
"""Backfill Shandong PMOS hourly CSV into the EFM3 MySQL ledger.

Registers the source, imports day-ahead / real-time prices into
``efm_market_data_hourly`` (data_type ``da_price`` / ``rt_price``) and the
actual prices into ``efm_actual_prices`` (columns ``da_anchor`` / ``rt_actual``),
and records a persistent ``da_anchor`` prediction ledger for lineage.

Conventions (aligned with common/data_ingestion/importers.py):
    * 日前电价  -> efm_market_data_hourly.data_type = 'da_price'
                  efm_actual_prices.da_anchor
    * 实时电价  -> efm_market_data_hourly.data_type = 'rt_price'
                  efm_actual_prices.rt_actual
    * hour mapping: 00:00 -> 24, 01:00 -> 1, ... 23:00 -> 23
    * missing values within a 24h day are linearly interpolated and flagged.

Usage (dry-run first, then commit):
    python tools/db_ops/backfill_shandong_pmos_csv.py \
        --csv-path data/shandong_pmos_hourly.csv \
        --start-date 2026-01-01 --end-date 2026-06-30 \
        --db-url $env:EFM3_DB_URL --encoding gbk --dry-run

    python tools/db_ops/backfill_shandong_pmos_csv.py \
        ... --commit
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("backfill_pmos")

# ── Column candidate lists (Chinese auto-recognition) ─────────────
TIME_COLUMN_CANDIDATES = ["时刻", "时间", "timestamp", "ds", "date", "日期", "交易日期", "trade_date"]
DA_COLUMN_CANDIDATES = ["日前电价", "日前出清电价", "日前统一出清价", "dayahead_price", "da_price", "日前节点边际电价"]
RT_COLUMN_CANDIDATES = ["实时电价", "实时出清电价", "实时统一出清价", "actual_price", "realtime_price", "rt_price", "实时节点边际电价"]

DA_PRICE_TYPE = "da_price"
RT_PRICE_TYPE = "rt_price"
MARKET = "shandong"

SOURCE_ID = "shandong_pmos_hourly_csv"
SOURCE_NAME = "Shandong PMOS Hourly CSV"


# ── DB URL parsing (handles %23 in password) ─────────────────────

def parse_db_url(db_url: str):
    """Parse a mysql[+pymysql]://user:pass@host:port/db URL.

    Returns (host, port, user, password, database). The password is
    URL-decoded so that %23 (encoded '#') becomes '#'.
    """
    from urllib.parse import unquote, urlparse
    u = urlparse(db_url)
    netloc = u.netloc  # user:pass@host:port
    userinfo, hostport = netloc.rsplit("@", 1)
    user, pw = userinfo.split(":", 1) if ":" in userinfo else (userinfo, "")
    pw = unquote(pw)
    if ":" in hostport:
        host, port_s = hostport.rsplit(":", 1)
        port = int(port_s)
    else:
        host, port = hostport, 3306
    database = u.path.lstrip("/")
    return host, port, user, pw, database


def _connect(db_url: str):
    import pymysql
    host, port, user, pw, database = parse_db_url(db_url)
    return pymysql.connect(host=host, port=port, user=user, password=pw, database=database,
                            autocommit=False, charset="utf8mb4")


def _detect_encoding(csv_path: Path) -> str:
    # Try strict UTF-8 first. A GBK-encoded file's bytes are *invalid* UTF-8,
    # so this correctly falls through to GBK/gb18030. Conversely, a real UTF-8
    # file (incl. Chinese text) decodes cleanly as UTF-8. GBK is a large
    # superset and can also decode many UTF-8 byte sequences, so it must NOT be
    # tried before UTF-8 or genuine UTF-8 files would be mis-detected as GBK.
    # utf-8-sig is placed second so BOM-prefixed files still get the BOM
    # stripped variant, while plain UTF-8 files report exactly 'utf-8'.
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        try:
            with open(csv_path, "r", encoding=enc) as fh:
                fh.read(4096)
            return enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise RuntimeError(f"Could not decode {csv_path}")


def _pick_column(columns, candidates):
    for c in candidates:
        if c in columns:
            return c
    return None


def _hour_from_ts(ts) -> int:
    hb = ts.hour
    return 24 if hb == 0 else hb


def _load_window_df(csv_path: Path, enc: str, sd: date, ed: date):
    """Load CSV, parse timestamps, return rows within [sd, ed] as list of dict."""
    import pandas as pd

    df = pd.read_csv(csv_path, encoding=enc)
    columns = list(df.columns)
    time_col = _pick_column(columns, TIME_COLUMN_CANDIDATES)
    da_col = _pick_column(columns, DA_COLUMN_CANDIDATES)
    rt_col = _pick_column(columns, RT_COLUMN_CANDIDATES)
    if time_col is None or da_col is None or rt_col is None:
        raise SystemExit(
            f"Column detection failed. time={time_col} da={da_col} rt={rt_col}. "
            f"Columns: {columns}"
        )

    ts = pd.to_datetime(df[time_col], errors="coerce")
    df = df.assign(_ts=ts)
    df = df.dropna(subset=["_ts"]).copy()
    df["_date"] = df["_ts"].dt.date
    df["_hb"] = df["_ts"].apply(_hour_from_ts)
    mask = (df["_date"] >= sd) & (df["_date"] <= ed)
    df = df.loc[mask].copy()
    return df, columns, time_col, da_col, rt_col


def _interpolate_day(series):
    """Given a 24-length list (index 0..23 -> hour 1..24) with possible None,
    linearly interpolate missing values. Returns (filled_list, n_interpolated)."""
    import math
    vals = list(series)
    n = len(vals)
    present = [i for i, v in enumerate(vals) if v is not None]
    n_interp = 0
    if not present:
        # No data at all — fill with 0 (will be flagged incomplete).
        return [0.0] * n, n
    # Forward/backward fill then linear between present points.
    for i in range(n):
        if vals[i] is None:
            # find nearest present indices
            left = max([p for p in present if p < i], default=None)
            right = min([p for p in present if p > i], default=None)
            if left is not None and right is not None:
                lv, rv = vals[left], vals[right]
                vals[i] = lv + (rv - lv) * (i - left) / (right - left)
            elif left is not None:
                vals[i] = vals[left]
            elif right is not None:
                vals[i] = vals[right]
            n_interp += 1
    return [float(v) for v in vals], n_interp


def _build_day_records(df_day, da_col, rt_col):
    """From a per-day DataFrame, build (da_list[24], rt_list[24], n_interp, n_missing_rows)."""
    # index by hour_business 1..24
    by_hb = {int(r["_hb"]): r for _, r in df_day.iterrows()}
    da_raw = [None] * 24
    rt_raw = [None] * 24
    for hb in range(1, 25):
        r = by_hb.get(hb)
        if r is not None:
            da_raw[hb - 1] = None if pd_isna(r[da_col]) else float(r[da_col])
            rt_raw[hb - 1] = None if pd_isna(r[rt_col]) else float(r[rt_col])
    da_filled, da_i = _interpolate_day(da_raw)
    rt_filled, rt_i = _interpolate_day(rt_raw)
    n_missing_rows = int(sum(1 for hb in range(1, 25) if hb not in by_hb))
    return da_filled, rt_filled, da_i + rt_i, n_missing_rows


def pd_isna(v):
    import math
    if v is None:
        return True
    try:
        return bool(v != v)  # NaN
    except Exception:
        return False


# ── DB writers ────────────────────────────────────────────────────

def upsert_data_source(conn, source_id, source_name, root_path, path_pattern, config_json, market):
    sql = """
        INSERT INTO efm_data_sources
            (source_id, source_name, source_type, market, root_path, path_pattern, enabled, config_json)
        VALUES (%s, %s, 'file', %s, %s, %s, TRUE, %s)
        ON DUPLICATE KEY UPDATE
            source_name=VALUES(source_name),
            market=VALUES(market),
            root_path=VALUES(root_path),
            path_pattern=VALUES(path_pattern),
            enabled=TRUE,
            config_json=VALUES(config_json),
            updated_at=CURRENT_TIMESTAMP(3)
    """
    with conn.cursor() as cur:
        cur.execute(sql, (source_id, source_name, market, root_path, path_pattern,
                          json.dumps(config_json, ensure_ascii=False)))


def upsert_source_file(conn, source_id, file_path, file_name, file_ext, file_size,
                       file_mtime, file_sha256, import_status, import_message, metadata_json):
    sql = """
        INSERT INTO efm_source_files
            (source_id, file_path, file_name, file_ext, file_size, file_mtime,
             file_sha256, detected_at, imported_at, import_status, import_message, metadata_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3), %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            file_size=VALUES(file_size),
            file_mtime=VALUES(file_mtime),
            imported_at=CURRENT_TIMESTAMP(3),
            import_status=VALUES(import_status),
            import_message=VALUES(import_message),
            metadata_json=VALUES(metadata_json)
    """
    with conn.cursor() as cur:
        cur.execute(sql, (source_id, file_path, file_name, file_ext, file_size,
                          file_mtime, file_sha256, import_status, import_message,
                          json.dumps(metadata_json, ensure_ascii=False)))


def upsert_market_hourly(conn, market, rows):
    """rows: list of (data_type, trade_date, hour_business, value, source_file_id, quality_flags_json).

    ``market`` is prepended to each row inside this function. ``unit`` is omitted
    (DB default 'CNY/MWh'). The UNIQUE KEY (market, data_type, trade_date,
    hour_business) drives the upsert so re-runs are idempotent.
    """
    sql = """
        INSERT INTO efm_market_data_hourly
            (market, data_type, trade_date, hour_business, value, source_file_id, quality_flags)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            value=VALUES(value),
            source_file_id=VALUES(source_file_id),
            quality_flags=VALUES(quality_flags)
    """
    full_rows = [(market,) + r for r in rows]
    with conn.cursor() as cur:
        cur.executemany(sql, full_rows)


def upsert_actual_prices(conn, rows):
    """rows: list of (target_date, hour_business, da_anchor, rt_actual, source_file)."""
    sql = """
        INSERT INTO efm_actual_prices
            (target_date, hour_business, da_anchor, rt_actual, source_file)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            da_anchor=COALESCE(VALUES(da_anchor), da_anchor),
            rt_actual=COALESCE(VALUES(rt_actual), rt_actual),
            source_file=VALUES(source_file)
    """
    with conn.cursor() as cur:
        cur.executemany(sql, rows)


def upsert_da_anchor_predictions(conn, run_id, target_date, da_list, metadata):
    """Write da_anchor predictions (one per hour) for a backfill run_id.

    Requires an efm_runs row to exist (FK). We insert one with mode='dry_run'
    so it is excluded from formal_sim metric queries but preserved for lineage.
    """
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO efm_runs
               (run_id, target_date, chain_version, mode, status, delivery_status, exit_code, started_at, finished_at)
               VALUES (%s, %s, '3.0-backfill-v1', 'dry_run', 'COMPLETE', 'NORMAL', 0, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3))
               ON DUPLICATE KEY UPDATE status='COMPLETE'""",
            (run_id, target_date),
        )
        cur.execute(
            """DELETE FROM efm_predictions WHERE run_id=%s AND target_date=%s AND stage='da_anchor'""",
            (run_id, target_date),
        )
        rows = []
        for hb in range(1, 25):
            qf = json.dumps({
                "source": "shandong_pmos_hourly_csv",
                "source_column": "日前电价",
                "imported_by": "backfill_shandong_pmos_csv.py",
                "interpolated": bool(metadata.get("interpolated_hours", 0) > 0),
            }, ensure_ascii=False)
            rows.append((run_id, target_date, hb, "dayahead", "da_anchor",
                         "da_anchor", "shandong_pmos_hourly_csv",
                         float(da_list[hb - 1]), 0, 0, None, qf))
        sql = """
            INSERT INTO efm_predictions
                (run_id, target_date, hour_business, task, stage, model_name, model_version,
                 pred_price, is_shadow, is_selected, selected_reason, quality_flags)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        cur.executemany(sql, rows)


# ── Main ──────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Backfill Shandong PMOS CSV into EFM3 ledger.")
    ap.add_argument("--csv-path", default="data/shandong_pmos_hourly.csv")
    ap.add_argument("--start-date", default="2026-01-01")
    ap.add_argument("--end-date", default="2026-06-30")
    ap.add_argument("--db-url", default=os.environ.get("EFM3_DB_URL", ""))
    ap.add_argument("--encoding", default=None)
    ap.add_argument("--source-id", default=SOURCE_ID)
    ap.add_argument("--market", default=MARKET)
    ap.add_argument("--dry-run", action="store_true", help="Parse/validate only, no DB writes.")
    ap.add_argument("--commit", action="store_true", help="Write to database.")
    ap.add_argument("--no-da-anchor-ledger", action="store_true",
                    help="Skip writing efm_predictions da_anchor ledger (chain derives per-run).")
    args = ap.parse_args()

    if not args.commit and not args.dry_run:
        ap.error("Specify --dry-run or --commit")
    if args.commit and not args.db_url:
        ap.error("EFM3_DB_URL not set; pass --db-url or set the env variable.")

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    sd = date.fromisoformat(args.start_date)
    ed = date.fromisoformat(args.end_date)
    enc = args.encoding or _detect_encoding(csv_path)

    logger.info("Loading %s (%s) window %s..%s", csv_path.name, enc, sd, ed)
    df, columns, time_col, da_col, rt_col = _load_window_df(csv_path, enc, sd, ed)
    logger.info("Window rows: %d", len(df))

    # Group by date
    days = sorted(df["_date"].unique())
    logger.info("Distinct dates in window: %d", len(days))

    # Pre-compute per-day records (used by both dry-run stats and commit)
    day_recs = {}
    total_da = total_rt = total_actual = 0
    incomplete_days = []
    interp_total = 0
    for d in days:
        d_df = df[df["_date"] == d]
        da_filled, rt_filled, n_interp, n_missing_rows = _build_day_records(d_df, da_col, rt_col)
        interp_total += n_interp
        rec = {
            "date": d, "da": da_filled, "rt": rt_filled,
            "n_interp": n_interp, "n_missing_rows": n_missing_rows,
        }
        day_recs[d] = rec
        total_da += 24
        total_rt += 24
        total_actual += 24
        if n_missing_rows > 0:
            incomplete_days.append({"date": d.isoformat(), "missing_rows": n_missing_rows})

    # ── Dry-run report ──
    if args.dry_run:
        logger.info("[DRY-RUN] would write:")
        logger.info("  efm_market_data_hourly da_price rows : %d", total_da)
        logger.info("  efm_market_data_hourly rt_price rows : %d", total_rt)
        logger.info("  efm_actual_prices rows               : %d", total_actual)
        logger.info("  interpolated value cells             : %d", interp_total)
        logger.info("  days with missing rows (<24)         : %d", len(incomplete_days))
        if incomplete_days:
            logger.warning("  incomplete days: %s", incomplete_days[:10])
        logger.info("[DRY-RUN] NO database writes performed.")
        return 0

    # ── Commit ──
    conn = _connect(args.db_url)
    try:
        # 1) data source
        cfg = {
            "encoding": enc,
            "imported_columns": {"da_price": da_col, "rt_price": rt_col, "time": time_col},
            "date_range": [sd.isoformat(), ed.isoformat()],
            "hour_mapping": "00:00->24 ... 23:00->23",
            "source_semantics": {
                "da_price": "day-ahead clearing price (日前电价)",
                "rt_price": "real-time clearing price (实时电价)",
            },
        }
        upsert_data_source(conn, args.source_id, SOURCE_NAME,
                           str(csv_path.parent), csv_path.name, cfg, args.market)
        logger.info("Upserted efm_data_sources (%s)", args.source_id)

        # 2) source file (sha256)
        file_bytes = csv_path.read_bytes()
        sha = hashlib.sha256(file_bytes).hexdigest()
        st = csv_path.stat()
        meta = {"rows_in_window": len(df), "distinct_dates": len(days),
                "encoding": enc, "dayahead_column": da_col, "realtime_column": rt_col}
        upsert_source_file(conn, args.source_id, str(csv_path), csv_path.name,
                           csv_path.suffix, st.st_size,
                           datetime.fromtimestamp(st.st_mtime), sha,
                           "IMPORTED", "backfilled Jan-Jun 2026", meta)
        logger.info("Upserted efm_source_files (sha256=%s..)", sha[:16])
        source_file_id = None
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM efm_source_files WHERE source_id=%s AND file_sha256=%s",
                        (args.source_id, sha))
            row = cur.fetchone()
            source_file_id = row[0] if row else None
        conn.commit()

        # 3) market_data_hourly + actual_prices (batch)
        mh_rows = []
        act_rows = []
        for d in days:
            rec = day_recs[d]
            for hb in range(1, 25):
                qf = json.dumps({"interpolated": bool(rec["n_interp"] > 0),
                                  "source_sha256": sha[:16]}, ensure_ascii=False)
                mh_rows.append((DA_PRICE_TYPE, d, hb, rec["da"][hb - 1], source_file_id, qf))
                mh_rows.append((RT_PRICE_TYPE, d, hb, rec["rt"][hb - 1], source_file_id, qf))
                act_rows.append((d, hb, rec["da"][hb - 1], rec["rt"][hb - 1], csv_path.name))
        upsert_market_hourly(conn, args.market, mh_rows)
        upsert_actual_prices(conn, act_rows)
        conn.commit()
        logger.info("Wrote efm_market_data_hourly=%d rows, efm_actual_prices=%d rows",
                    len(mh_rows), len(act_rows))

        # 4) da_anchor prediction ledger (lineage) — optional
        if not args.no_da_anchor_ledger:
            run_id = f"backfill_da_anchor_{sd.strftime('%Y%m%d')}_{ed.strftime('%Y%m%d')}"
            for d in days:
                rec = day_recs[d]
                upsert_da_anchor_predictions(conn, run_id, d, rec["da"],
                                             {"interpolated_hours": rec["n_interp"]})
            conn.commit()
            logger.info("Wrote da_anchor prediction ledger under run_id=%s", run_id)

        logger.info("Backfill commit complete. Interpolated cells=%d, incomplete_days=%d",
                    interp_total, len(incomplete_days))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
