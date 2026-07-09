#!/usr/bin/env python
"""EFM3 Yearly Metrics Calculator.
Reads final-selected predictions and actual prices from the MySQL ledger,
computes accuracy metrics (SMAPE, MAE, RMSE, MAPE, WMAPE, coverage) and
writes a structured report.

Usage:
    python tools/db_ops/db_yearly_metrics.py ^
        --start-date 2026-01-01 --end-date 2026-12-31 ^
        --db-url "mysql+pymysql://root:PASS%23@127.0.0.1:3306/efm3" ^
        --output-md outputs/db_yearly_formal_sim/2026/metrics.md ^
        --output-json outputs/db_yearly_formal_sim/2026/metrics.json
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("yearly_metrics")

DB_URL = os.environ.get("EFM3_DB_URL", "")


def _connect(db_url: str):
    import pymysql
    u = db_url.split("//", 1)[1]
    up, hp = u.split("@")
    user, pw = up.split(":")
    hp, dbn = hp.split("/")
    host, port = hp.split(":")
    pw = unquote(pw)
    return pymysql.connect(host=host, port=int(port), user=user, password=pw, database=dbn)


# ── Data models ───────────────────────────────────────────────────

@dataclass
class DayPred:
    date: str
    hour_business: int
    pred_price: float


@dataclass
class DayActual:
    date: str
    hour_business: int
    actual_price: float


@dataclass
class DayMetrics:
    date: str
    run_id: str = ""
    run_status: str = ""
    final_selected: int = 0
    n_hours: int = 0
    smape: float = 0.0
    mae: float = 0.0
    rmse: float = 0.0
    mape: float = 0.0
    wmape: float = 0.0
    skipped_smape: int = 0
    skipped_mape: int = 0
    has_actual: bool = False
    result: str = ""


# ── Actual price resolution ───────────────────────────────────────

def load_actual_prices(cur, target_date: str) -> dict[int, float]:
    """Load actual prices for a date. Priority: efm_actual_prices > efm_market_data_hourly."""
    actuals: dict[int, float] = {}

    # Try efm_actual_prices first
    cur.execute(
        "SELECT hour_business, rt_actual FROM efm_actual_prices "
        "WHERE target_date=%s ORDER BY hour_business",
        (target_date,),
    )
    for row in cur.fetchall():
        if row[1] is not None:
            actuals[int(row[0])] = float(row[1])

    if actuals:
        return actuals

    # Fallback: efm_market_data_hourly with realtime/clearing/actual data_type
    cur.execute(
        "SELECT hour_business, value, data_type FROM efm_market_data_hourly "
        "WHERE trade_date=%s ORDER BY hour_business",
        (target_date,),
    )
    for row in cur.fetchall():
        dt = (row[2] or "").lower()
        if any(kw in dt for kw in ("actual", "realtime", "clearing", "real_time")):
            if row[1] is not None:
                actuals[int(row[0])] = float(row[1])

    return actuals


def load_predictions(cur, run_id: str) -> dict[int, float]:
    """Load final_selected predictions for a run."""
    preds: dict[int, float] = {}
    cur.execute(
        "SELECT hour_business, pred_price FROM efm_predictions "
        "WHERE run_id=%s AND task='final' AND stage='final_selected' "
        "AND is_selected=1 AND is_shadow=0 ORDER BY hour_business",
        (run_id,),
    )
    for row in cur.fetchall():
        preds[int(row[0])] = float(row[1])
    return preds


# ── Scope-aware prediction / actual loaders (metric scope semantics) ──

def load_predictions_by_scope(cur, run_id: str, scope: str) -> dict[int, float]:
    """Load predictions for a given metric scope.

    benchmark : da_anchor / benchmark_da_anchor (day-ahead clearing price)
    dayahead  : efm_task_finals WHERE task='dayahead' (a REAL model final)
    realtime  : efm_task_finals WHERE task='realtime' (a REAL model final)
    delivery  : efm_delivery_finals delivery_price
    """
    preds: dict[int, float] = {}
    if scope == "benchmark":
        cur.execute(
            "SELECT hour_business, pred_price FROM efm_predictions "
            "WHERE run_id=%s AND stage IN ('da_anchor','benchmark_da_anchor') "
            "ORDER BY hour_business",
            (run_id,),
        )
        for hb, p in cur.fetchall():
            if p is not None:
                preds[int(hb)] = float(p)
    elif scope == "dayahead":
        cur.execute(
            "SELECT hour_business, final_price FROM efm_task_finals "
            "WHERE run_id=%s AND task='dayahead' ORDER BY hour_business",
            (run_id,),
        )
        for hb, p in cur.fetchall():
            if p is not None:
                preds[int(hb)] = float(p)
    elif scope == "realtime":
        cur.execute(
            "SELECT hour_business, final_price FROM efm_task_finals "
            "WHERE run_id=%s AND task='realtime' ORDER BY hour_business",
            (run_id,),
        )
        for hb, p in cur.fetchall():
            if p is not None:
                preds[int(hb)] = float(p)
    elif scope == "delivery":
        cur.execute(
            "SELECT hour_business, delivery_price FROM efm_delivery_finals "
            "WHERE run_id=%s ORDER BY hour_business",
            (run_id,),
        )
        for hb, p in cur.fetchall():
            if p is not None:
                preds[int(hb)] = float(p)
    return preds


def load_actuals_by_scope(cur, target_date: str, scope: str) -> dict[int, float]:
    """Load actual prices for a given metric scope.

    benchmark : rt_actual  (DA clearing vs RT actual = cross-product spread)
    dayahead  : da_anchor  (model pred vs SAME product actual)
    realtime  : rt_actual
    delivery  : rt_actual
    """
    col = "rt_actual" if scope in ("benchmark", "realtime", "delivery") else "da_anchor"
    cur.execute(
        f"SELECT hour_business, {col} FROM efm_actual_prices "
        f"WHERE target_date=%s AND {col} IS NOT NULL ORDER BY hour_business",
        (target_date,),
    )
    return {int(hb): float(v) for hb, v in cur.fetchall()}


# ── Metrics computation ───────────────────────────────────────────

def compute_metrics(preds: dict[int, float], actuals: dict[int, float]) -> dict:
    """Compute SMAPE, MAE, RMSE, MAPE, WMAPE for matched (pred, actual) hours.

    Uses the legacy (no floor) SMAPE. Prefer compute_metrics_floor50 for the
    official 2.5-aligned floor(50) metric.
    """
    common_hours = sorted(set(preds.keys()) & set(actuals.keys()))
    if not common_hours:
        return {"n_hours": 0, "smape": None, "mae": None, "rmse": None,
                "mape": None, "wmape": None, "skipped_smape": 0, "skipped_mape": 0}

    smape_values: list[float] = []
    mae_values: list[float] = []
    mape_values: list[float] = []
    abs_errors: list[float] = []
    abs_actuals: list[float] = []
    skipped_smape = 0
    skipped_mape = 0
    sq_errors: list[float] = []

    for hb in common_hours:
        p = preds[hb]
        a = actuals[hb]
        abs_err = abs(p - a)
        abs_errors.append(abs_err)
        mae_values.append(abs_err)
        sq_errors.append(abs_err ** 2)
        abs_actuals.append(abs(a))

        # SMAPE
        denom = abs(p) + abs(a)
        if denom == 0:
            smape_values.append(0.0)
        elif denom > 0:
            smape_values.append(200.0 * abs_err / denom)
        else:
            skipped_smape += 1

        # MAPE
        if a == 0:
            skipped_mape += 1
        else:
            mape_values.append(100.0 * abs_err / abs(a))

    n = len(common_hours)
    smape = sum(smape_values) / len(smape_values) if smape_values else 0.0
    mae = sum(mae_values) / n if n else 0.0
    rmse = math.sqrt(sum(sq_errors) / n) if n else 0.0
    mape = sum(mape_values) / len(mape_values) if mape_values else 0.0
    wmape = (sum(abs_errors) / sum(abs_actuals) * 100.0) if sum(abs_actuals) > 0 else 0.0

    return {
        "n_hours": n,
        "smape": round(smape, 4),
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "mape": round(mape, 4),
        "wmape": round(wmape, 4),
        "skipped_smape": skipped_smape,
        "skipped_mape": skipped_mape,
    }


def compute_metrics_floor50(preds: dict[int, float], actuals: dict[int, float]) -> dict:
    """Compute SMAPE with the official floor(50) clipping (2.5-aligned).

    Each value is clamped to max(value, 50) before the SMAPE denominator, then
    pooled (mean over matched hours). This matches docs/metrics_calculation.md.
    """
    common_hours = sorted(set(preds.keys()) & set(actuals.keys()))
    if not common_hours:
        return {"n_hours": 0, "smape": None, "mae": None, "rmse": None,
                "mape": None, "wmape": None, "skipped_smape": 0, "skipped_mape": 0}

    smape_values: list[float] = []
    mae_values: list[float] = []
    mape_values: list[float] = []
    abs_errors: list[float] = []
    abs_actuals: list[float] = []
    sq_errors: list[float] = []
    skipped_mape = 0

    for hb in common_hours:
        p = float(preds[hb])
        a = float(actuals[hb])
        abs_err = abs(p - a)
        abs_errors.append(abs_err)
        mae_values.append(abs_err)
        sq_errors.append(abs_err ** 2)
        abs_actuals.append(abs(a))
        pc, ac = max(p, 50.0), max(a, 50.0)
        denom = abs(pc) + abs(ac)
        smape_values.append(200.0 * abs(pc - ac) / denom if denom > 0 else 0.0)
        if a == 0:
            skipped_mape += 1
        else:
            mape_values.append(100.0 * abs_err / abs(a))

    n = len(common_hours)
    smape = sum(smape_values) / n
    mae = sum(mae_values) / n
    rmse = math.sqrt(sum(sq_errors) / n)
    mape = sum(mape_values) / len(mape_values) if mape_values else 0.0
    wmape = (sum(abs_errors) / sum(abs_actuals) * 100.0) if sum(abs_actuals) > 0 else 0.0
    return {
        "n_hours": n,
        "smape": round(smape, 4),
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "mape": round(mape, 4),
        "wmape": round(wmape, 4),
        "skipped_smape": 0,
        "skipped_mape": skipped_mape,
    }


# ── Scope-aware metric run (persisted to efm_metric_runs) ──────────────

def run_scope_metric(
    cur, run_id: str, target_date: str, scope: str, floor50: bool = True,
) -> dict:
    """Compute a single day's metric for a given scope and persist it.

    Returns a result dict with keys: result, smape, ... and writes a row to
    efm_metric_runs. Critical semantic guards:
      * benchmark scope is ALWAYS labeled benchmark (never reported as model).
      * dayahead/realtime scopes are ONLY computed when a REAL model final
        exists in efm_task_finals; otherwise result='UNCLEAR'/no computation.
      * realtime scope with no realtime final is NEVER fabricated.
    """
    preds = load_predictions_by_scope(cur, run_id, scope)
    actuals = load_actuals_by_scope(cur, target_date, scope)

    pred_stage = {
        "benchmark": "benchmark_da_anchor",
        "dayahead": "dayahead_task_final",
        "realtime": "realtime_task_final",
        "delivery": "delivery_final",
    }[scope]
    actual_source = {
        "benchmark": "rt_actual",
        "dayahead": "da_anchor",
        "realtime": "rt_actual",
        "delivery": "rt_actual",
    }[scope]

    # Guard: production scopes require a REAL model final present.
    if scope in ("dayahead", "realtime") and not preds:
        res = {"result": "UNCLEAR", "reason": f"no {scope} model final (NEEDS_MODEL_OUTPUT)"}
        _persist_scope(cur, run_id, target_date, scope, pred_stage, actual_source,
                       res, floor50)
        return res
    if scope == "delivery" and not preds:
        res = {"result": "NO_DELIVERY", "reason": "no delivery_final rows"}
        _persist_scope(cur, run_id, target_date, scope, pred_stage, actual_source,
                       res, floor50)
        return res

    common = sorted(set(preds) & set(actuals))
    if not common:
        res = {"result": "NO_DATA", "reason": "no overlapping (pred, actual) hours"}
        _persist_scope(cur, run_id, target_date, scope, pred_stage, actual_source,
                       res, floor50)
        return res

    metrics = (compute_metrics_floor50(preds, actuals) if floor50
               else compute_metrics(preds, actuals))
    res = {
        "result": "OK",
        "smape": metrics["smape"],
        "mae": metrics["mae"],
        "rmse": metrics["rmse"],
        "mape": metrics["mape"],
        "wmape": metrics["wmape"],
        "n_hours": metrics["n_hours"],
    }
    _persist_scope(cur, run_id, target_date, scope, pred_stage, actual_source,
                   res, floor50, smape=metrics["smape"], mae=metrics["mae"],
                   rmse=metrics["rmse"], mape=metrics["mape"], wmape=metrics["wmape"],
                   n_hours=metrics["n_hours"])
    return res


def _persist_scope(cur, run_id, target_date, scope, pred_stage, actual_source,
                   res, floor50, smape=None, mae=None, rmse=None, mape=None,
                   wmape=None, n_hours=None):
    """Write (upsert) a row into efm_metric_runs for this scope/day."""
    try:
        metric_run_id = f"{scope}_{run_id}_{target_date}"
        cur.execute(
            """
            INSERT INTO efm_metric_runs
              (metric_run_id, run_id, target_date_start, target_date_end,
               metric_scope, pred_stage, actual_source, smape, mae, rmse, mape,
               wmape, evaluable_days, evaluable_hours, config_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s)
            ON DUPLICATE KEY UPDATE
              id = LAST_INSERT_ID(id),
              smape = VALUES(smape), mae = VALUES(mae), rmse = VALUES(rmse),
              mape = VALUES(mape), wmape = VALUES(wmape),
              evaluable_hours = VALUES(evaluable_hours),
              config_json = VALUES(config_json)
            """,
            (
                metric_run_id, run_id, target_date, target_date, scope,
                pred_stage, actual_source, smape, mae, rmse, mape, wmape,
                1 if smape is not None else 0, n_hours,
                json.dumps({"floor50": floor50, "result": res.get("result"),
                            "reason": res.get("reason")}, ensure_ascii=False),
            ),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("failed to persist metric_run for scope=%s: %s", scope, exc)


# ── DB query ──────────────────────────────────────────────────────

def query_daily_data(cur, start_date: str, end_date: str) -> list[dict]:
    """Query per-day data from all relevant tables for metrics computation."""
    rows = []
    s = date.fromisoformat(start_date)
    e = date.fromisoformat(end_date)

    for n in range((e - s).days + 1):
        d = (s + timedelta(n)).isoformat()

        # Get latest formal_sim run for this date
        cur.execute(
            "SELECT run_id, status, delivery_status, exit_code FROM efm_runs "
            "WHERE target_date=%s AND mode='formal_sim' "
            "ORDER BY started_at DESC LIMIT 1",
            (d,),
        )
        run = cur.fetchone()
        if not run:
            rows.append({"date": d, "result": "NO_DATA"})
            continue

        run_id, status, delivery_status, exit_code = run
        row = {
            "date": d,
            "run_id": run_id,
            "run_status": status,
            "delivery_status": delivery_status,
            "exit_code": exit_code,
        }

        # Final selected count
        cur.execute(
            "SELECT COUNT(*) FROM efm_predictions WHERE run_id=%s "
            "AND task='final' AND stage='final_selected' "
            "AND is_selected=1 AND is_shadow=0",
            (run_id,),
        )
        row["final_selected"] = cur.fetchone()[0]

        # Fusion decisions
        cur.execute(
            "SELECT COUNT(*) FROM efm_fusion_decisions WHERE run_id=%s",
            (run_id,),
        )
        row["fusion_decisions"] = cur.fetchone()[0]

        # DA anchor
        cur.execute(
            "SELECT COUNT(*) FROM efm_predictions WHERE run_id=%s AND stage='da_anchor'",
            (run_id,),
        )
        row["da_anchor"] = cur.fetchone()[0]

        # Official baseline
        cur.execute(
            "SELECT COUNT(*) FROM efm_predictions WHERE run_id=%s AND stage='official_baseline'",
            (run_id,),
        )
        row["official_baseline"] = cur.fetchone()[0]

        # Shadow rows (selected)
        cur.execute(
            "SELECT COUNT(*) FROM efm_predictions WHERE run_id=%s AND is_shadow=1 AND is_selected=1",
            (run_id,),
        )
        row["shadow_selected"] = cur.fetchone()[0]

        # Postflight
        cur.execute(
            "SELECT COUNT(*), SUM(passed) FROM efm_postflight_checks WHERE run_id=%s",
            (run_id,),
        )
        p = cur.fetchone()
        row["postflight_total"] = p[0] if p else 0
        row["postflight_pass"] = p[1] if p and p[1] else 0

        # Delivery outputs (should be 0 for formal_sim)
        cur.execute(
            "SELECT COUNT(*) FROM efm_delivery_outputs WHERE run_id=%s",
            (run_id,),
        )
        row["delivery_outputs"] = cur.fetchone()[0]

        # Load predictions and actuals for metrics
        preds = load_predictions(cur, run_id)
        actuals = load_actual_prices(cur, d)

        if not actuals:
            row["result"] = "NO_ACTUAL"
        elif row["final_selected"] == 24:
            metrics = compute_metrics(preds, actuals)
            row.update(metrics)
            row["result"] = "PASS"
        else:
            row["result"] = "FORMAL_FAIL"

        row["has_actual"] = bool(actuals)
        row["n_pred_hours"] = len(preds)
        row["n_actual_hours"] = len(actuals)

        rows.append(row)

    return rows


# ── Aggregation ───────────────────────────────────────────────────

def aggregate_monthly(daily: list[dict]) -> list[dict]:
    """Aggregate daily metrics by month."""
    months: dict[str, list[dict]] = defaultdict(list)
    for d in daily:
        m = d["date"][:7]
        months[m].append(d)

    result = []
    for m in sorted(months):
        days = months[m]
        m_days = len(days)
        m_pass = sum(1 for d in days if d.get("result") == "PASS")
        m_no_data = sum(1 for d in days if d.get("result") == "NO_DATA")
        m_no_actual = sum(1 for d in days if d.get("result") == "NO_ACTUAL")
        m_fail = sum(1 for d in days if d.get("result") == "FORMAL_FAIL")

        # Aggregate metrics from PASS days with actual data
        valid = [d for d in days if d.get("smape") is not None]
        if valid:
            n = len(valid)
            smape = sum(d["smape"] for d in valid) / n
            mae = sum(d["mae"] for d in valid) / n
            rmse = math.sqrt(sum(d["rmse"] ** 2 for d in valid) / n)
            mape = sum(d["mape"] for d in valid) / n if valid[0].get("mape") else 0.0
            wmape = sum(d.get("wmape", 0) for d in valid) / n
        else:
            smape = mae = rmse = mape = wmape = 0.0

        result.append({
            "month": m,
            "days": m_days,
            "pass": m_pass,
            "no_data": m_no_data,
            "no_actual": m_no_actual,
            "formal_fail": m_fail,
            "smape": round(smape, 4) if valid else None,
            "mae": round(mae, 4) if valid else None,
            "rmse": round(rmse, 4) if valid else None,
            "mape": round(mape, 4) if valid else None,
            "wmape": round(wmape, 4) if valid else None,
        })
    return result


def aggregate_quarterly(daily: list[dict]) -> list[dict]:
    """Aggregate by quarter."""
    def quarter(m: int) -> str:
        if m <= 3: return "Q1"
        if m <= 6: return "Q2"
        if m <= 9: return "Q3"
        return "Q4"

    qmap: dict[str, list[dict]] = defaultdict(list)
    for d in daily:
        m = int(d["date"][5:7])
        q = quarter(m)
        qmap[q].append(d)

    result = []
    for q in sorted(qmap):
        days = qmap[q]
        valid = [d for d in days if d.get("smape") is not None]
        n = len(valid)
        result.append({
            "quarter": q,
            "days": len(days),
            "pass": sum(1 for d in days if d.get("result") == "PASS"),
            "no_data": sum(1 for d in days if d.get("result") == "NO_DATA"),
            "no_actual": sum(1 for d in days if d.get("result") == "NO_ACTUAL"),
            "smape": round(sum(d["smape"] for d in valid) / n, 4) if valid else None,
            "mae": round(sum(d["mae"] for d in valid) / n, 4) if valid else None,
            "rmse": round(math.sqrt(sum(d["rmse"] ** 2 for d in valid) / n), 4) if valid else None,
            "wmape": round(sum(d.get("wmape", 0) for d in valid) / n, 4) if valid else None,
        })
    return result


# ── Reporting ─────────────────────────────────────────────────────

def generate_md(start: str, end: str, daily: list[dict],
                monthly: list[dict], quarterly: list[dict],
                yearly: dict) -> str:
    lines = ["# EFM3 Yearly Metrics Report", ""]
    lines.append(f"Period: {start} ~ {end}")
    lines.append("")

    # Summary
    n = len(daily)
    n_pass = sum(1 for d in daily if d.get("result") == "PASS")
    n_no_data = sum(1 for d in daily if d.get("result") == "NO_DATA")
    n_no_actual = sum(1 for d in daily if d.get("result") == "NO_ACTUAL")
    n_fail = sum(1 for d in daily if d.get("result") == "FORMAL_FAIL")
    n_error = sum(1 for d in daily if d.get("result") == "ERROR")

    evaluable = sum(1 for d in daily if d.get("smape") is not None)

    lines.append("## Execution Summary")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"| ------ | ----: |")
    lines.append(f"| days attempted | {n} |")
    lines.append(f"| PASS | {n_pass} |")
    lines.append(f"| NO_DATA | {n_no_data} |")
    lines.append(f"| NO_ACTUAL | {n_no_actual} |")
    lines.append(f"| FORMAL_FAIL | {n_fail} |")
    lines.append(f"| ERROR | {n_error} |")
    lines.append(f"| pass rate (data-available) | {n_pass / (n - n_no_data) * 100:.1f}% |" if (n - n_no_data) > 0 else "")
    lines.append(f"| evaluable days (pred+actual) | {evaluable} |")
    lines.append("")

    if yearly.get("smape") is not None:
        lines.append("## Yearly Metrics")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"| ------ | ----- |")
        lines.append(f"| SMAPE | {yearly['smape']:.2f}% |")
        lines.append(f"| MAE   | {yearly['mae']:.4f} |")
        lines.append(f"| RMSE  | {yearly['rmse']:.4f} |")
        lines.append(f"| MAPE  | {yearly['mape']:.2f}% |" if yearly.get('mape') else "")
        lines.append(f"| WMAPE | {yearly['wmape']:.2f}% |")
        lines.append("")

    # Monthly
    lines.append("## Monthly Metrics")
    lines.append("")
    lines.append("| Month | Days | PASS | NO_DATA | NO_ACTUAL | FAIL | SMAPE | MAE | RMSE | WMAPE |")
    lines.append("| ----- | ---: | ---: | ------: | --------: | ---: | ----: | --: | ---: | ----: |")
    for m in monthly:
        sm = f"{m['smape']:.2f}" if m['smape'] is not None else "-"
        ma = f"{m['mae']:.4f}" if m['mae'] is not None else "-"
        rm = f"{m['rmse']:.4f}" if m['rmse'] is not None else "-"
        wm = f"{m['wmape']:.2f}" if m['wmape'] is not None else "-"
        lines.append(f"| {m['month']} | {m['days']} | {m['pass']} | {m['no_data']} | "
                     f"{m['no_actual']} | {m['formal_fail']} | {sm} | {ma} | {rm} | {wm} |")
    lines.append("")

    # Quarterly
    lines.append("## Quarterly Metrics")
    lines.append("")
    lines.append("| Quarter | Days | PASS | NO_DATA | NO_ACTUAL | SMAPE | MAE | RMSE | WMAPE |")
    lines.append("| ------- | ---: | ---: | ------: | --------: | ----: | --: | ---: | ----: |")
    for q in quarterly:
        sm = f"{q['smape']:.2f}" if q['smape'] is not None else "-"
        ma = f"{q['mae']:.4f}" if q['mae'] is not None else "-"
        rm = f"{q['rmse']:.4f}" if q['rmse'] is not None else "-"
        wm = f"{q['wmape']:.2f}" if q['wmape'] is not None else "-"
        lines.append(f"| {q['quarter']} | {q['days']} | {q['pass']} | {q['no_data']} | "
                     f"{q['no_actual']} | {sm} | {ma} | {rm} | {wm} |")
    lines.append("")

    # Worst days
    valid_days = [d for d in daily if d.get("smape") is not None]
    worst_smape = sorted(valid_days, key=lambda x: x.get("smape", 0), reverse=True)[:10]
    if worst_smape:
        lines.append("## Top 10 Worst Days by SMAPE")
        lines.append("")
        lines.append("| Rank | Date | SMAPE | MAE | RMSE |")
        lines.append("| ---: | ---- | ----: | --: | ---: |")
        for i, d in enumerate(worst_smape, 1):
            lines.append(f"| {i} | {d['date']} | {d['smape']:.2f}% | {d['mae']:.4f} | {d['rmse']:.4f} |")
        lines.append("")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="EFM3 Yearly Metrics Calculator")
    ap.add_argument("--start-date", required=True)
    ap.add_argument("--end-date", required=True)
    ap.add_argument("--db-url", default=DB_URL)
    ap.add_argument("--output-md", type=Path, default=None)
    ap.add_argument("--output-json", type=Path, default=None)
    ap.add_argument("--metric-scope", default=None,
                    choices=["benchmark", "dayahead", "realtime", "delivery"],
                    help="Restrict the metric to a single scope. benchmark = "
                         "da_anchor vs rt_actual (cross-product spread, NOT a model "
                         "metric). dayahead/realtime = efm_task_finals vs same-product "
                         "actual (only when a REAL model final exists). delivery = "
                         "efm_delivery_finals vs rt_actual. When omitted, the legacy "
                         "final_selected vs rt_actual metric is computed for parity.")
    ap.add_argument("--no-floor50", action="store_true", default=False,
                    help="Disable floor(50) SMAPE clipping (official formula uses it).")
    args = ap.parse_args()

    if not args.db_url:
        ap.error("EFM3_DB_URL not set")

    floor50 = not args.no_floor50
    conn = _connect(args.db_url)
    cur = conn.cursor()

    if args.metric_scope:
        # Scope-restricted, persisted metric run.
        logger.info("Computing metric_scope=%s (floor50=%s) for %s..%s",
                    args.metric_scope, floor50, args.start_date, args.end_date)
        s = date.fromisoformat(args.start_date)
        e = date.fromisoformat(args.end_date)
        scope_rows = []
        for n in range((e - s).days + 1):
            d = (s + timedelta(n)).isoformat()
            # Find latest run for this date (any chain) to attribute predictions.
            cur.execute(
                "SELECT run_id FROM efm_runs WHERE target_date=%s "
                "ORDER BY started_at DESC LIMIT 1", (d,))
            row = cur.fetchone()
            if not row:
                scope_rows.append({"date": d, "result": "NO_RUN"})
                continue
            rid = row[0]
            res = run_scope_metric(cur, rid, d, args.metric_scope, floor50=floor50)
            scope_rows.append({"date": d, "run_id": rid, **res})
        conn.commit()
        conn.close()
        # Console summary
        ok = [r for r in scope_rows if r.get("result") == "OK"]
        unclear = [r for r in scope_rows if r.get("result") in ("UNCLEAR", "NO_DATA", "NO_DELIVERY")]
        avg = (sum(r["smape"] for r in ok) / len(ok)) if ok else None
        logger.info("scope=%s: %d OK, %d unclear/skipped, avg SMAPE(floor50)=%s",
                    args.metric_scope, len(ok), len(unclear),
                    f"{avg:.2f}" if avg is not None else "n/a")
        if args.output_json:
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(
                json.dumps({"scope": args.metric_scope, "floor50": floor50,
                            "daily": scope_rows}, indent=2, default=str),
                encoding="utf-8")
        if args.output_md:
            lines = [f"# Metric scope: {args.metric_scope} (floor50={floor50})", ""]
            lines.append("| Date | Result | SMAPE | MAE | RMSE | Note |")
            lines.append("| ---- | ------ | ----: | --: | ---: | ---- |")
            for r in scope_rows:
                lines.append(
                    f"| {r['date']} | {r.get('result')} | "
                    f"{r.get('smape', '').__str__() if r.get('smape') is not None else '-'} | "
                    f"{r.get('mae', '-')} | {r.get('rmse', '-')} | "
                    f"{r.get('reason', '')} |")
            args.output_md.parent.mkdir(parents=True, exist_ok=True)
            args.output_md.write_text("\n".join(lines), encoding="utf-8")
        return

    logger.info("Querying daily data from %s to %s...", args.start_date, args.end_date)
    daily = query_daily_data(cur, args.start_date, args.end_date)
    conn.close()
    logger.info("  %d days loaded", len(daily))

    # Aggregate
    monthly = aggregate_monthly(daily)
    quarterly = aggregate_quarterly(daily)

    # Yearly totals
    valid_all = [d for d in daily if d.get("smape") is not None]
    yearly = {}
    if valid_all:
        n = len(valid_all)
        yearly = {
            "smape": round(sum(d["smape"] for d in valid_all) / n, 4),
            "mae": round(sum(d["mae"] for d in valid_all) / n, 4),
            "rmse": round(math.sqrt(sum(d["rmse"] ** 2 for d in valid_all) / n), 4),
            "mape": round(sum(d["mape"] for d in valid_all) / n, 4) if valid_all[0].get("mape") else None,
            "wmape": round(sum(d.get("wmape", 0) for d in valid_all) / n, 4),
            "evaluable_days": n,
        }

    report = {
        "period": {"start": args.start_date, "end": args.end_date},
        "daily": daily,
        "monthly": monthly,
        "quarterly": quarterly,
        "yearly": yearly,
    }

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        logger.info("JSON written to %s", args.output_json)

    md = generate_md(args.start_date, args.end_date, daily, monthly, quarterly, yearly)
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(md, encoding="utf-8")
        logger.info("Markdown written to %s", args.output_md)

    # Console summary
    n_pass = sum(1 for d in daily if d.get("result") == "PASS")
    n_no_data = sum(1 for d in daily if d.get("result") == "NO_DATA")
    n_no_actual = sum(1 for d in daily if d.get("result") == "NO_ACTUAL")
    evaluable = sum(1 for d in daily if d.get("smape") is not None)
    logger.info(
        "Done: %d days | PASS=%d NO_DATA=%d NO_ACTUAL=%d evaluable=%d"
        " | SMAPE=%.2f%%",
        len(daily), n_pass, n_no_data, n_no_actual, evaluable,
        yearly.get("smape", 0) or 0,
    )


if __name__ == "__main__":
    main()
