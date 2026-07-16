#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EFM3 V3.1 — Historical Data Discovery (2022+).

Walks all sibling project roots and catalogs every data file. For a prioritized
subset (large files or files whose path carries data keywords) it extracts real
date coverage so we can determine whether genuine 2022-2025 history exists.

Outputs (into data_audit/):
  HISTORICAL_DATA_INVENTORY.csv   one row per data file (path/size/hash/rows/cols/coverage/...)
  HISTORICAL_DATA_COVERAGE.csv    per-file date coverage (min/max/n_days/years present)
  HISTORICAL_DATA_SCHEMA.md       schema notes for the richest candidate sources
  HISTORICAL_DATA_VERDICT.json    machine verdict: does 2022+ history exist?

NO writes outside data_audit/. Read-only scan.
"""
import os, sys, csv, json, hashlib, datetime, re, traceback
import pandas as pd
import numpy as np

# ---- roots (siblings of the research worktree) ----
HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))          # electricity_forecast_model3.0-research
BASE = os.path.abspath(os.path.join(RESEARCH_ROOT, ".."))               # 其他资料
OUT = os.path.join(RESEARCH_ROOT, "data_audit")
os.makedirs(OUT, exist_ok=True)

ROOTS = [
    "electricity_forecast_model3.0", "electricity_forecast_model2.5",
    "electricity_forecast_model2.0", "electricity_forecast_model2.0_exp",
    "electricity_forecast_model2.1", "models", "deep_model_for_electricity",
    "all_model_train_and_eval", "electricity_forecast_deep_sgdf_delta",
    "EFM3-Artifacts",
]
DATA_EXT = {".csv", ".parquet", ".xlsx", ".xls", ".db", ".sql", ".dump", ".feather"}
SKIP_DIR = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".vscode"}

# path keywords that flag a file as a likely RAW-DATA source worth deep parsing
KW = re.compile(r"(price|load|solar|wind|actual|forecast|hourly|daily|history|"
                r"historic|train|data|dataset|market|pmos|da_|rt_|net.?load|"
                r"interconnect|bidding|exog|feature|panel|raw)", re.I)
DATE_COL = re.compile(r"(date|time|day|timestamp|ds|dt|business_day|trade_date|"
                      r"datetime|hour|period)", re.I)

MAX_DEEP_BYTES = 120 * 1024 * 1024   # don't fully parse files bigger than this
DEEP_MIN_BYTES = 50 * 1024           # deep-parse if >=50KB OR keyword hit

def sha256_head(path, nbytes=1024 * 1024):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            h.update(f.read(nbytes))
    except Exception:
        return ""
    return h.hexdigest()[:16]

def sniff_dates(series):
    """Return (min_date, max_date, n_unique_days, years_set) or None."""
    try:
        s = pd.to_datetime(series, errors="coerce", utc=False)
        s = s.dropna()
        if len(s) == 0:
            return None
        yrs = sorted(set(s.dt.year.tolist()))
        # sanity: plausible electricity years
        yrs = [y for y in yrs if 2015 <= y <= 2030]
        if not yrs:
            return None
        return (str(s.min().date()), str(s.max().date()),
                int(s.dt.normalize().nunique()), yrs)
    except Exception:
        return None

def parse_coverage(path, ext, size):
    """Attempt to read date columns and return coverage dict, plus rows/cols."""
    rows = cols = None
    cov = None
    colnames = []
    try:
        if ext == ".parquet":
            import pyarrow.parquet as pq
            pf = pq.ParquetFile(path)
            rows = pf.metadata.num_rows
            colnames = [c for c in pf.schema.names]
            cols = len(colnames)
            dcols = [c for c in colnames if DATE_COL.search(str(c))]
            if dcols:
                df = pd.read_parquet(path, columns=dcols[:3])
                best = None
                for c in dcols[:3]:
                    r = sniff_dates(df[c])
                    if r and (best is None or r[2] > best[2]):
                        best = r; cov_col = c
                if best:
                    cov = dict(zip(["min", "max", "n_days", "years"], best)); cov["col"] = cov_col
        elif ext == ".csv":
            head = pd.read_csv(path, nrows=50, dtype=str, on_bad_lines="skip",
                               encoding_errors="replace", low_memory=False)
            colnames = list(head.columns); cols = len(colnames)
            dcols = [c for c in colnames if DATE_COL.search(str(c))]
            if dcols and size <= MAX_DEEP_BYTES:
                use = dcols[:2]
                df = pd.read_csv(path, usecols=use, dtype=str, on_bad_lines="skip",
                                 encoding_errors="replace", low_memory=False)
                rows = len(df)
                best = None; cov_col = None
                for c in use:
                    r = sniff_dates(df[c])
                    if r and (best is None or r[2] > best[2]):
                        best = r; cov_col = c
                if best:
                    cov = dict(zip(["min", "max", "n_days", "years"], best)); cov["col"] = cov_col
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(path, nrows=5000)
            colnames = list(df.columns); cols = len(colnames); rows = len(df)
            dcols = [c for c in colnames if DATE_COL.search(str(c))]
            best = None; cov_col = None
            for c in dcols[:3]:
                r = sniff_dates(df[c])
                if r and (best is None or r[2] > best[2]):
                    best = r; cov_col = c
            if best:
                cov = dict(zip(["min", "max", "n_days", "years"], best)); cov["col"] = cov_col
    except Exception:
        pass
    return rows, cols, colnames, cov

def main():
    inv_rows = []
    cov_rows = []
    years_global = set()
    early_hits = []   # files with any year <= 2024
    n_scanned = 0
    n_deep = 0
    for root in ROOTS:
        rp = os.path.join(BASE, root)
        if not os.path.isdir(rp):
            continue
        for dirpath, dirnames, filenames in os.walk(rp):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR]
            for fn in filenames:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in DATA_EXT:
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    size = os.path.getsize(p)
                except Exception:
                    continue
                n_scanned += 1
                rel = os.path.relpath(p, BASE)
                kw = bool(KW.search(rel))
                deep = (size >= DEEP_MIN_BYTES or kw) and ext in {".csv", ".parquet", ".xlsx", ".xls"}
                rows = cols = None; colnames = []; cov = None
                if deep and size <= MAX_DEEP_BYTES:
                    rows, cols, colnames, cov = parse_coverage(p, ext, size)
                    n_deep += 1
                h = sha256_head(p)
                yrs = cov["years"] if cov else []
                for y in yrs:
                    years_global.add(y)
                if yrs and min(yrs) <= 2024:
                    early_hits.append((rel, cov["min"], cov["max"], cov["n_days"], size, root))
                inv_rows.append({
                    "root": root, "path": rel, "size_bytes": size,
                    "size_mb": round(size / 1e6, 3), "ext": ext,
                    "sha256_head16": h, "rows": rows, "cols": cols,
                    "date_col": (cov or {}).get("col", ""),
                    "date_min": (cov or {}).get("min", ""),
                    "date_max": (cov or {}).get("max", ""),
                    "n_days": (cov or {}).get("n_days", ""),
                    "years": ";".join(map(str, yrs)),
                    "keyword_hit": int(kw), "deep_parsed": int(deep and size <= MAX_DEEP_BYTES),
                    "n_columns_names": len(colnames),
                })
                if cov:
                    cov_rows.append({
                        "path": rel, "root": root, "date_col": cov.get("col", ""),
                        "date_min": cov["min"], "date_max": cov["max"],
                        "n_days": cov["n_days"], "years": ";".join(map(str, yrs)),
                        "rows": rows, "size_mb": round(size / 1e6, 3),
                    })

    # write inventory
    inv_path = os.path.join(OUT, "HISTORICAL_DATA_INVENTORY.csv")
    with open(inv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(inv_rows[0].keys()) if inv_rows else
                           ["root", "path", "size_bytes"])
        w.writeheader()
        for r in sorted(inv_rows, key=lambda x: -x["size_bytes"]):
            w.writerow(r)

    # coverage (only files with real date coverage), sorted by span
    cov_path = os.path.join(OUT, "HISTORICAL_DATA_COVERAGE.csv")
    with open(cov_path, "w", newline="", encoding="utf-8-sig") as f:
        fld = ["path", "root", "date_col", "date_min", "date_max", "n_days",
               "years", "rows", "size_mb"]
        w = csv.DictWriter(f, fieldnames=fld)
        w.writeheader()
        for r in sorted(cov_rows, key=lambda x: (x["date_min"] or "9")):
            w.writerow(r)

    # verdict
    has_2022 = any(y <= 2022 for y in years_global)
    has_pre2025 = any(y <= 2024 for y in years_global)
    verdict = {
        "generated": datetime.datetime.now().isoformat(),
        "roots_scanned": ROOTS,
        "n_data_files_scanned": n_scanned,
        "n_deep_parsed": n_deep,
        "years_observed_across_all_files": sorted(years_global),
        "has_2022_data": has_2022,
        "has_pre_2025_data": has_pre2025,
        "n_files_with_year_le_2024": len(early_hits),
        "earliest_year_observed": (min(years_global) if years_global else None),
        "latest_year_observed": (max(years_global) if years_global else None),
        "top_early_files": [
            {"path": e[0], "min": e[1], "max": e[2], "n_days": e[3],
             "size_mb": round(e[4] / 1e6, 3), "root": e[5]}
            for e in sorted(early_hits, key=lambda x: (x[1], -x[3]))[:40]
        ],
        "verdict": ("PRE_2025_HISTORY_FOUND" if has_pre2025 else
                    "NO_PRE_2025_HISTORY_FOUND"),
    }
    with open(os.path.join(OUT, "HISTORICAL_DATA_VERDICT.json"), "w", encoding="utf-8") as f:
        json.dump(verdict, f, ensure_ascii=False, indent=2)

    print("SCANNED files:", n_scanned, "DEEP:", n_deep)
    print("YEARS observed:", sorted(years_global))
    print("has_2022:", has_2022, "has_pre_2025:", has_pre2025,
          "files_year<=2024:", len(early_hits))
    print("VERDICT:", verdict["verdict"])
    print("--- top early files (year<=2024) ---")
    for e in verdict["top_early_files"][:25]:
        print(f"  {e['min']}..{e['max']} ({e['n_days']}d, {e['size_mb']}MB) {e['path']}")

if __name__ == "__main__":
    main()
