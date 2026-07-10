#!/usr/bin/env python
"""
Compute ALL official metrics per docs/metrics_calculation.md from the
backtest results stored in the DB Ledger V2.

Reads DAYAHEAD_TASK_FINAL predictions from the production circuit backtest
and compares them against da_anchor (the day-ahead clearing price).

Outputs:
  - Floor(50) SMAPE, MAE, RMSE, WMAPE, R2, MAPE
  - Per-period (1_8, 9_16, 17_24) breakdown
  - Accuracy = 1 - SMAPE
  - SCR (spread direction accuracy)
  - Arbitrage (basic + improved)
  - Overall + monthly breakdown
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from math import copysign

import pymysql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_URL = os.environ.get("EFM3_DB_URL",
                        "mysql+pymysql://root:Zlt20060313%%23@127.0.0.1:3306/efm3")
SMAFE_FLOOR = 50  # clip threshold for SMAPE


def _connect():
    from common.db.connection import DbConnectionManager
    db_url = DB_URL.replace("%%23", "%23")
    mgr = DbConnectionManager(db_url=db_url)
    return mgr.new_connection()


def compute_all(db_url: str = None) -> dict:
    conn = _connect()
    cur = conn.cursor()
    results = {}

    # ── 1. Fetch ALL dayahead backtest predictions + actuals ──────
    # We need: pred_price from efm_predictions (dayahead_task_final, task=dayahead)
    #          + da_anchor from efm_actual_prices
    # Match by target_date and hour_business
    cur.execute("""
        SELECT p.target_date, p.hour_business, p.pred_price,
               a.da_anchor, a.rt_actual
        FROM efm_predictions p
        JOIN efm_actual_prices a
          ON p.target_date = a.target_date
         AND p.hour_business = a.hour_business
        WHERE p.stage = 'dayahead_task_final'
          AND p.task = 'dayahead'
          AND p.target_date BETWEEN '2025-11-01' AND '2026-06-19'
          AND a.da_anchor IS NOT NULL
        ORDER BY p.target_date, p.hour_business
    """)
    rows = cur.fetchall()

    if not rows:
        print("WARN: no matching prediction+actual rows found")
        return {"error": "no data"}

    # Parse into lists
    records = []
    for r in rows:
        records.append({
            "target_date": str(r[0]),
            "hour_business": int(r[1]),
            "pred": float(r[2]),
            "da_anchor": float(r[3]),
            "rt_actual": float(r[4]) if r[4] is not None else None,
        })

    # ── 2. Per-period classification ──────────────────────────────
    for rec in records:
        hb = rec["hour_business"]
        if 1 <= hb <= 8:
            rec["period"] = "1_8"
        elif 9 <= hb <= 16:
            rec["period"] = "9_16"
        else:
            rec["period"] = "17_24"

    # ── 3. Floor(50) SMAPE computation ────────────────────────────
    def _clipped_smape(preds: list, actuals: list) -> float:
        """Compute SMAPE with floor(50) clipping."""
        n = len(preds)
        if n == 0:
            return 0.0
        total = 0.0
        for p, a in zip(preds, actuals):
            p_clip = max(p, SMAFE_FLOOR)
            a_clip = max(a, SMAFE_FLOOR)
            denom = (abs(p_clip) + abs(a_clip)) / 2.0
            if denom < 1e-10:
                total += 0.0
            else:
                total += abs(p_clip - a_clip) / denom
        return (total / n) * 100.0

    def _mae(preds: list, actuals: list) -> float:
        n = len(preds)
        return sum(abs(p - a) for p, a in zip(preds, actuals)) / n if n else 0.0

    def _rmse(preds: list, actuals: list) -> float:
        n = len(preds)
        return (sum((p - a) ** 2 for p, a in zip(preds, actuals)) / n) ** 0.5 if n else 0.0

    def _wmape(preds: list, actuals: list) -> float:
        denom = sum(abs(a) for a in actuals)
        return sum(abs(p - a) for p, a in zip(preds, actuals)) / denom * 100.0 if denom else 0.0

    def _mape(preds: list, actuals: list) -> float:
        n = len(preds)
        vals = [abs(p - a) / abs(a) * 100.0 for p, a in zip(preds, actuals) if abs(a) > 1e-10]
        return sum(vals) / len(vals) if vals else 0.0

    def _r2(preds: list, actuals: list) -> float:
        n = len(preds)
        mean_a = sum(actuals) / n
        ss_res = sum((p - a) ** 2 for p, a in zip(preds, actuals))
        ss_tot = sum((a - mean_a) ** 2 for a in actuals)
        return 1.0 - ss_res / ss_tot if ss_tot else 0.0

    def _scr(preds_da: list, actuals_da: list, actuals_rt: list) -> float:
        """SCR: spread direction accuracy (P_rt - P_da vs P̂_da vs actual)."""
        n = 0
        correct = 0
        for p_da, a_da, a_rt in zip(preds_da, actuals_da, actuals_rt):
            if a_rt is None:
                continue
            n += 1
            real_spread = a_rt - a_da
            pred_spread = p_da - a_da  # assuming P̂_rt = P̂_da for DA-only model
            if copysign(1, real_spread) == copysign(1, pred_spread) or abs(real_spread) < 1e-10:
                correct += 1
        return correct / n * 100.0 if n else 0.0

    def _arbitrage_basic(preds: list, actuals_da: list, actuals_rt: list) -> dict:
        """Basic arbitrage: sell when P̂_da > P_da."""
        total_profit = 0.0
        total_volume = 0
        for p_da, a_da, a_rt in zip(preds, actuals_da, actuals_rt):
            if a_rt is None:
                continue
            if p_da > a_da:
                profit = a_rt - a_da
                total_profit += profit
                total_volume += 1
        return {
            "total_profit": round(total_profit, 2),
            "total_volume": total_volume,
            "unit_profit": round(total_profit / total_volume, 2) if total_volume else None,
        }

    def _arbitrage_improved(preds: list, actuals_da: list, actuals_rt: list) -> dict:
        """Improved: sell when P̂_rt > P̂_da AND P̂_da > P_da.
        Since we only have DA predictions, P̂_rt ≈ P̂_da for the DA-only model."""
        total_profit = 0.0
        total_volume = 0
        for p_da, a_da, a_rt in zip(preds, actuals_da, actuals_rt):
            if a_rt is None:
                continue
            # With only DA model: condition reduces to P̂_da > P_da (same as basic)
            # The improved condition P̂_rt > P̂_da requires a RT model
            if p_da > a_da:
                profit = a_rt - a_da
                total_profit += profit
                total_volume += 1
        return {
            "total_profit": round(total_profit, 2),
            "total_volume": total_volume,
            "unit_profit": round(total_profit / total_volume, 2) if total_volume else None,
            "note": "Identical to basic (no RT model predictions available)",
        }

    # ── 4. Compute overall metrics ────────────────────────────────
    preds_all = [r["pred"] for r in records]
    actuals_da = [r["da_anchor"] for r in records]
    actuals_rt = [r["rt_actual"] for r in records if r["rt_actual"] is not None]

    results["overall"] = {
        "n_hours": len(records),
        "n_days": len(set(r["target_date"] for r in records)),
        "smape_floor50": round(_clipped_smape(preds_all, actuals_da), 2),
        "mae": round(_mae(preds_all, actuals_da), 2),
        "rmse": round(_rmse(preds_all, actuals_da), 2),
        "wmape": round(_wmape(preds_all, actuals_da), 2),
        "mape": round(_mape(preds_all, actuals_da), 2),
        "r2": round(_r2(preds_all, actuals_da), 4),
        "accuracy": round(100.0 - _clipped_smape(preds_all, actuals_da), 2),
        "scr": round(_scr(preds_all, actuals_da, [r["rt_actual"] for r in records]), 2),
        "arbitrage_basic": _arbitrage_basic(preds_all, actuals_da, [r["rt_actual"] for r in records]),
        "arbitrage_improved": _arbitrage_improved(preds_all, actuals_da, [r["rt_actual"] for r in records]),
    }

    # ── 5. Per-period breakdown ───────────────────────────────────
    periods = {"1_8": [], "9_16": [], "17_24": []}
    for rec in records:
        periods[rec["period"]].append(rec)

    results["per_period"] = {}
    for pname, precs in periods.items():
        pp = [r["pred"] for r in precs]
        pa = [r["da_anchor"] for r in precs]
        results["per_period"][pname] = {
            "n_hours": len(precs),
            "smape_floor50": round(_clipped_smape(pp, pa), 2),
            "mae": round(_mae(pp, pa), 2),
            "rmse": round(_rmse(pp, pa), 2),
            "accuracy": round(100.0 - _clipped_smape(pp, pa), 2),
        }

    # ── 6. Monthly breakdown ──────────────────────────────────────
    monthly = defaultdict(list)
    for rec in records:
        monthly[rec["target_date"][:7]].append(rec)

    results["monthly"] = {}
    for m in sorted(monthly.keys()):
        precs = monthly[m]
        pp = [r["pred"] for r in precs]
        pa = [r["da_anchor"] for r in precs]
        results["monthly"][m] = {
            "n_days": len(set(r["target_date"] for r in precs)),
            "n_hours": len(precs),
            "smape_floor50": round(_clipped_smape(pp, pa), 2),
            "mae": round(_mae(pp, pa), 2),
            "accuracy": round(100.0 - _clipped_smape(pp, pa), 2),
        }

    conn.close()
    return results


def print_official(results: dict):
    o = results.get("overall", {})
    print()
    print("╔" + "═" * 70 + "╗")
    print("║  📊 EFM3.0 日前回测 — 官方指标 (floor(50) SMAPE)")
    print("╚" + "═" * 70 + "╝")
    print()
    print(f"  评估样本: {o.get('n_days', 0)} 天 / {o.get('n_hours', 0)} 小时")
    print(f"  对比对象: 模型预测(dayahead_task_final) vs 日前出清价(da_anchor)")
    print()
    print("  ┌──────────────────────────┬────────────┐")
    print(f"  │ sMAPE (floor 50)          │ {o.get('smape_floor50', 'N/A'):>8.2f}% │")
    print(f"  │ Accuracy (1-SMAPE)        │ {o.get('accuracy', 'N/A'):>8.2f}% │")
    print(f"  │ MAE                       │ {o.get('mae', 'N/A'):>8.2f} CNY/MWh │")
    print(f"  │ RMSE                      │ {o.get('rmse', 'N/A'):>8.2f} CNY/MWh │")
    print(f"  │ WMAPE                     │ {o.get('wmape', 'N/A'):>8.2f}% │")
    print(f"  │ MAPE                      │ {o.get('mape', 'N/A'):>8.2f}% │")
    print(f"  │ R²                        │ {o.get('r2', 'N/A'):>8.4f} │")
    print("  ├──────────────────────────┼────────────┤")
    ab = o.get("arbitrage_basic", {})
    print(f"  │ 度电套利(基础) 总利润     │ {ab.get('total_profit', 'N/A'):>8.2f} 元 │")
    print(f"  │ 度电套利(基础) 总交易次数 │ {ab.get('total_volume', 'N/A'):>8} 次 │")
    print(f"  │ 度电套利(基础) 单位利润   │ {str(ab.get('unit_profit', 'N/A')):>8} 元/MWh │")
    ai = o.get("arbitrage_improved", {})
    print(f"  │ 度电套利(改良) 单位利润   │ {str(ai.get('unit_profit', 'N/A')):>8} 元/MWh │")
    print(f"  │ SCR (价差方向准确率)      │ {o.get('scr', 'N/A'):>8.2f}% │")
    print("  └──────────────────────────┴────────────┘")

    # Per-period
    pp = results.get("per_period", {})
    print()
    print("  ┌──────┬──────────┬────────┬──────────┬──────────┐")
    print("  │ 时段 │  小时数  │ sMAPE  │  MAE     │ Accuracy │")
    print("  ├──────┼──────────┼────────┼──────────┼──────────┤")
    for pname in ["1_8", "9_16", "17_24"]:
        p = pp.get(pname, {})
        print(f"  │ {pname:<4} │ {p.get('n_hours', 0):>8} │ {p.get('smape_floor50', 0):>6.2f}% │ {p.get('mae', 0):>8.2f} │ {p.get('accuracy', 0):>6.2f}% │")
    print("  └──────┴──────────┴────────┴──────────┴──────────┘")

    # Monthly
    monthly = results.get("monthly", {})
    print()
    print("  ┌────────┬──────┬────────┬────────┬──────────┐")
    print("  │ 月份   │ 天数 │ sMAPE  │ MAE    │ Accuracy │")
    print("  ├────────┼──────┼────────┼────────┼──────────┤")
    for m in sorted(monthly.keys()):
        mn = monthly[m]
        print(f"  │ {m} │  {mn['n_days']:>2}d │ {mn['smape_floor50']:>6.2f}% │ {mn['mae']:>6.2f} │ {mn['accuracy']:>6.2f}% │")
    print("  └────────┴──────┴────────┴────────┴──────────┘")
    print()


if __name__ == "__main__":
    results = compute_all()
    # Save JSON
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "official_metrics_3.0.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print_official(results)
    print(f"[report saved to outputs/official_metrics_3.0.json]")
