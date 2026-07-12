"""
Day-ahead backtest driver for EFM3.0 (Part 1 / Final).

Feeds OUR 3.0 real day-ahead models (cfg05, xgboost_rich, catboost_rich) —
produced by the sibling P1 walk-forward (models repo) — into the EFM3.0
production circuit, for every historical date that has both (a) P1
predictions and (b) da_anchor actuals in the ledger. Collects the day-ahead
metric scope (smape / mae / wmape vs da_anchor) per date and emits a report.

This validates the entire day-ahead sub-chain (Part 1: "日前换成我们的那几个
模型") at scale with REAL model outputs.

Real-time models (sgdfnet / timesfm) live in a separate repo
(electricity_forecast_deep_sgdf_delta) and are NOT ingested here; the circuit
therefore runs in dayahead-primary mode (realtime falls back to DA_anchor /
da_aware_sgdf_selector-derived, delivery = dayahead_only_fallback). The
day-ahead metric scope is the key deliverable.

Usage
-----
  python tools/backtest_dayahead.py --p1-output <dir> [--start 2025-11-01] [--end 2026-06-19]

The --p1-output dir must contain predictions/all_predictions.csv written by
scripts/run_dayahead_p1_walkforward.py (columns include business_day,
hour_business, y_pred, model_name).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# load .env.local into env (DbConnectionManager / EFM3_DB_URL)
env_path = REPO / ".env.local"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from tools.ingest_model_predictions import ingest_file  # noqa: E402
from pipelines.production_circuit.circuit_orchestrator import run_production_circuit  # noqa: E402
from common.db.connection import DbConnectionManager  # noqa: E402

DA_MODELS = ["cfg05", "xgboost_rich", "catboost_rich"]


class DecimalEncoder(json.JSONEncoder):
    """Custom encoder that converts Decimal to float for JSON serialization."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def _parse_p1(p1_output: Path) -> dict[str, dict[str, list[tuple[int, float]]]]:
    """Return {target_date: {model: [(hour_business, y_pred), ...]}}."""
    all_csv = p1_output / "predictions" / "all_predictions.csv"
    if not all_csv.exists():
        raise FileNotFoundError(f"expected {all_csv}")
    out: dict[str, dict[str, list[tuple[int, float]]]] = defaultdict(
        lambda: defaultdict(list))
    with open(all_csv, newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        for row in r:
            m = (row.get("model_name") or "").strip()
            if m not in DA_MODELS:
                continue
            bd = (row.get("business_day") or row.get("target_day") or "").strip()
            if not bd:
                continue
            try:
                hb = int(float(row["hour_business"]))
                yp = float(row["y_pred"])
            except (TypeError, ValueError, KeyError):
                continue
            out[bd][m].append((hb, yp))
    return out


def _dates_with_actuals(db_url: str) -> set[str]:
    mgr = DbConnectionManager(db_url=db_url)
    conn = mgr.new_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT target_date FROM efm_actual_prices "
                "WHERE da_anchor IS NOT NULL")
            return {str(d[0]) for d in cur.fetchall()}
    finally:
        conn.close()


def _collect_dayahead_metric(db_url: str, run_id: str) -> dict | None:
    mgr = DbConnectionManager(db_url=db_url)
    conn = mgr.new_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT smape, mae, wmape, rmse FROM efm_metric_runs "
                "WHERE run_id=%s AND metric_scope='dayahead' LIMIT 1",
                (run_id,))
            row = cur.fetchone()
        if not row:
            return None
        return {"smape": row[0], "mae": row[1], "wmape": row[2], "rmse": row[3]}
    finally:
        conn.close()


def _ingest_date(db_url: str, target_date: str,
                 by_model: dict[str, list[tuple[int, float]]]) -> int:
    total = 0
    for m, pairs in by_model.items():
        if len(pairs) < 24:
            print(f"    [warn] {m} has only {len(pairs)} hours for {target_date}")
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, newline="",
            encoding="utf-8")
        w = csv.writer(tmp)
        w.writerow(["hour_business", "y_pred"])
        for hb, yp in sorted(pairs, key=lambda x: x[0]):
            w.writerow([hb, yp])
        tmp.close()
        total += ingest_file(db_url, "dayahead", m, target_date,
                              Path(tmp.name), model_version="p1_walkforward")
        os.unlink(tmp.name)
    return total


def run_backtest(p1_output: Path, start: str, end: str):
    db_url = os.environ["EFM3_DB_URL"]
    p1 = _parse_p1(p1_output)
    actuals = _dates_with_actuals(db_url)
    print(f"[backtest] P1 predictions cover {len(p1)} dates; "
          f"ledger da_anchor actuals cover {len(actuals)} dates")

    sd = date.fromisoformat(start)
    ed = date.fromisoformat(end)
    cand = []
    d = sd
    while d <= ed:
        ds = d.isoformat()
        if ds in p1 and ds in actuals:
            # require all 3 models present
            if all(m in p1[ds] for m in DA_MODELS):
                cand.append(ds)
            else:
                print(f"    [skip] {ds}: missing models "
                      f"{[m for m in DA_MODELS if m not in p1[ds]]}")
        d += timedelta(days=1)
    print(f"[backtest] {len(cand)} dates eligible for day-ahead scoring")

    report = []
    for i, ds in enumerate(cand, 1):
        print(f"[backtest] ({i}/{len(cand)}) {ds}")
        n = _ingest_date(db_url, ds, p1[ds])
        res = run_production_circuit(
            ds, mode="dry_run", use_db=True, db_url=db_url,
            config={"dayahead_models": DA_MODELS,
                    "allow_benchmark_fallback": False})
        rid = res["run_id"]
        metric = _collect_dayahead_metric(db_url, rid)
        status = res.get("status")
        rec = {"target_date": ds, "run_id": rid, "status": status,
               "ingested_rows": n,
               "dayahead_smape": (metric or {}).get("smape"),
               "dayahead_mae": (metric or {}).get("mae"),
               "dayahead_wmape": (metric or {}).get("wmape"),
               "dayahead_rmse": (metric or {}).get("rmse")}
        report.append(rec)
        print(f"    status={status} dayahead_metric={metric}")

    # aggregate
    smapes = [r["dayahead_smape"] for r in report if r["dayahead_smape"] is not None]
    maes = [r["dayahead_mae"] for r in report if r["dayahead_mae"] is not None]
    agg = {
        "n_dates": len(report),
        "mean_smape": (sum(smapes) / len(smapes)) if smapes else None,
        "mean_mae": (sum(maes) / len(maes)) if maes else None,
        "min_smape": min(smapes) if smapes else None,
        "max_smape": max(smapes) if smapes else None,
    }
    out_path = REPO / "outputs" / "backtest_dayahead_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"aggregate": agg, "per_date": report},
                                   indent=2, ensure_ascii=False, cls=DecimalEncoder))
    # also a CSV
    csv_path = REPO / "outputs" / "backtest_dayahead_report.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(report[0].keys()) if report else [])
        w.writeheader()
        for r in report:
            w.writerow(r)
    print(f"\n[backtest] DONE. aggregate={agg}")
    print(f"[backtest] report -> {out_path}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--p1-output", required=True, type=Path,
                    help="P1 walk-forward output dir (has predictions/all_predictions.csv)")
    ap.add_argument("--start", default="2025-11-01")
    ap.add_argument("--end", default="2026-06-19")
    args = ap.parse_args()
    sys.exit(run_backtest(args.p1_output, args.start, args.end))
