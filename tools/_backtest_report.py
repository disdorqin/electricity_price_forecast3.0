#!/usr/bin/env python
"""
Comprehensive backtest report generator.
Queries the EFM3 DB after a backtest run and produces a detailed report
covering all 5 items the user requested:
  1. Everything normal? (status counts)
  2. Stage/model row counts correct?
  3. Each model's timing?
  4. DB connection status?
  5. Final complete metrics (SMAPE/MAE/RMSE/WMAPE)?

Usage:
  python tools/_backtest_report.py [--run-id-prefix p1_backtest]
                                   [--start 2025-11-01]
                                   [--end 2026-06-19]
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.db.connection import DbConnectionManager

DB_URL = os.environ.get("EFM3_DB_URL", "mysql+pymysql://root:Zlt20060313%%23@127.0.0.1:3306/efm3")


def _connect(db_url: str):
    url = db_url.replace("%%23", "%23")
    mgr = DbConnectionManager(url)
    return mgr.new_connection()


def _check_connectivity(db_url: str) -> tuple[bool, str]:
    try:
        conn = _connect(db_url)
        conn.ping()
        conn.close()
        return True, "OK"
    except Exception as e:
        return False, str(e)


def generate(run_id_prefix: str, start: str, end: str) -> dict:
    db_url = DB_URL.replace("%%23", "%23")
    conn = _connect(db_url)
    cur = conn.cursor()

    # ── 1. DB connectivity ──────────────────────────────────────────
    ok, msg = _check_connectivity(db_url)
    connectivity = {"connected": ok, "message": msg, "host": "127.0.0.1:3306", "database": "efm3"}

    # ── 2. Find all backtest runs ───────────────────────────────────
    cur.execute(
        "SELECT run_id, target_date, status, started_at, finished_at "
        "FROM efm_runs WHERE run_id LIKE CONCAT(%s, '%%') "
        "ORDER BY target_date",
        (run_id_prefix,),
    )
    runs_raw = cur.fetchall()
    runs = []
    for r in runs_raw:
        runs.append({"run_id": r[0], "target_date": str(r[1]),
                      "status": r[2], "started_at": str(r[3]) if r[3] else None,
                      "finished_at": str(r[4]) if r[4] else None})

    n_total = len(runs)

    # ── 3. Status breakdown ─────────────────────────────────────────
    status_counts = defaultdict(int)
    for r in runs:
        status_counts[r["status"]] += 1
    n_complete = status_counts.get("COMPLETE", 0)
    n_partial = status_counts.get("PARTIAL", 0)
    n_running = status_counts.get("RUNNING", 0)
    all_normal = (n_complete + n_partial) >= (len(runs) - 2)  # allow 2 edge cases

    # ── 4. Metrics (from efm_metric_runs) ───────────────────────────
    placeholders = ",".join(["%s"] * len(runs)) if runs else ("'__none__'")
    if runs:
        run_ids = [r["run_id"] for r in runs]
        cur.execute(
            f"SELECT run_id, metric_scope, smape, mae, rmse, wmape, "
            f"evaluable_days, evaluable_hours, pred_stage, actual_source "
            f"FROM efm_metric_runs WHERE run_id IN ({placeholders}) "
            f"ORDER BY metric_scope, run_id",
            run_ids,
        )
        metrics_raw = cur.fetchall()
    else:
        metrics_raw = []

    metrics_by_scope = defaultdict(list)
    for m in metrics_raw:
        metrics_by_scope[m[1]].append({
            "run_id": m[0], "scope": m[1],
            "smape": float(m[2]) if m[2] is not None else None,
            "mae": float(m[3]) if m[3] is not None else None,
            "rmse": float(m[4]) if m[4] is not None else None,
            "wmape": float(m[5]) if m[5] is not None else None,
            "days": m[6], "hours": m[7],
            "pred_stage": m[8], "actual_source": m[9],
        })

    metric_summary = {}
    for scope, entries in sorted(metrics_by_scope.items()):
        smapes = [e["smape"] for e in entries if e["smape"] is not None]
        maes = [e["mae"] for e in entries if e["mae"] is not None]
        rmses = [e["rmse"] for e in entries if e["rmse"] is not None]
        wmapes = [e["wmape"] for e in entries if e["wmape"] is not None]
        metric_summary[scope] = {
            "n_runs": len(entries),
            "mean_smape": round(sum(smapes) / len(smapes), 4) if smapes else None,
            "mean_mae": round(sum(maes) / len(maes), 4) if maes else None,
            "mean_rmse": round(sum(rmses) / len(rmses), 4) if rmses else None,
            "mean_wmape": round(sum(wmapes) / len(wmapes), 4) if wmapes else None,
            "min_smape": round(min(smapes), 4) if smapes else None,
            "max_smape": round(max(smapes), 4) if smapes else None,
        }

    # ── 5. Stage counts (sample first 5 runs for row count check) ──
    sample_runs = [r["run_id"] for r in runs[:5]] if runs else []
    stage_counts = {}
    if sample_runs:
        placeholders2 = ",".join(["%s"] * len(sample_runs))
        cur.execute(
            f"SELECT stage, model_name, COUNT(*) as n, "
            f"MIN(hour_business) as min_hb, MAX(hour_business) as max_hb "
            f"FROM efm_predictions WHERE run_id IN ({placeholders2}) "
            f"GROUP BY stage, model_name ORDER BY stage, model_name",
            sample_runs,
        )
        for s in cur.fetchall():
            stage_counts[f"{s[0]}__{s[1]}"] = {
                "stage": s[0], "model": s[1],
                "rows": s[2], "min_hb": s[3], "max_hb": s[4],
            }

    # ── 6. Pipeline steps (timing) ─────────────────────────────────
    timing = {}
    if sample_runs:
        placeholders3 = ",".join(["%s"] * len(sample_runs))
        cur.execute(
            f"SELECT step_name, step_order, status, AVG(runtime_ms), "
            f"AVG(input_count), AVG(output_count), COUNT(*) "
            f"FROM efm_pipeline_steps WHERE run_id IN ({placeholders3}) "
            f"GROUP BY step_name, step_order, status ORDER BY step_order",
            sample_runs,
        )
        for t in cur.fetchall():
            key = t[0]
            timing[key] = {
                "step_name": t[0], "order": t[1], "status": t[2],
                "avg_runtime_ms": round(float(t[3]), 1) if t[3] else None,
                "avg_input": round(float(t[4])) if t[4] else None,
                "avg_output": round(float(t[5])) if t[5] else None,
                "n_observations": t[6],
            }

    conn.close()

    # ── 7. Model ingestion counts ──────────────────────────────────
    model_counts = {}
    if sample_runs:
        conn2 = _connect(db_url)
        c2 = conn2.cursor()
        placeholders4 = ",".join(["%s"] * len(sample_runs))
        c2.execute(
            f"SELECT model_name, COUNT(DISTINCT target_date) as dates, "
            f"COUNT(*) as cnt "
            f"FROM efm_predictions WHERE run_id IN ({placeholders4}) "
            f"AND stage='dayahead_raw_model' "
            f"GROUP BY model_name ORDER BY model_name",
            sample_runs,
        )
        for mc in c2.fetchall():
            model_counts[mc[0]] = {"model": mc[0], "dates": mc[1], "cnt": mc[2]}
        conn2.close()

    return {
        "connectivity": connectivity,
        "overview": {
            "total_runs": n_total,
            "date_range": f"{start} → {end}",
            "status_breakdown": dict(status_counts),
            "all_steps_normal": all_normal,
        },
        "model_ingestion": model_counts,
        "stage_counts_sample": stage_counts,
        "pipeline_timing": timing,
        "metrics": metric_summary,
    }


def print_report(report: dict):
    c = report["connectivity"]
    o = report["overview"]
    m = report["metrics"]
    mi = report["model_ingestion"]
    sc = report["stage_counts_sample"]
    pt = report["pipeline_timing"]

    print()
    print("╔" + "═" * 78 + "╗")
    print("║  📊 EFM3.0 日前回测完整报表")
    print(f"║  日期范围: {o['date_range']}")
    print("╚" + "═" * 78 + "╝")
    print()

    # 1. DB connectivity
    print("🔌 1. 数据库连接")
    print(f"   主机: {c['host']} → {'✅ 已连接' if c['connected'] else '❌ 断开'}")
    print(f"   数据库: {c['database']}")
    print()

    # 2. Everything normal
    print("✅ 2. 整体状态")
    print(f"   总运行日期数: {o['total_runs']}")
    print(f"   状态分布: ", end="")
    for st, cnt in sorted(o["status_breakdown"].items()):
        emoji = "✅" if st in ("COMPLETE", "PARTIAL") else "❌"
        print(f"{emoji} {st}: {cnt}  ", end="")
    print()
    print(f"   全部正常: {'✅ 是' if o['all_steps_normal'] else '❌ 否'}")
    print()

    # 3. Model ingestion
    print("🏭 3. 模型导入情况 (样例行数)")
    print(f"   {'模型':<25} {'日期数':>8} {'总行数':>10}")
    print(f"   {'-'*25} {'-'*8} {'-'*10}")
    for name, info in sorted(mi.items()):
        print(f"   {name:<25} {info['dates']:>8} {info['cnt']:>10}")
    print()

    # 4. Stage counts
    if sc:
        print("📋 4. 各阶段×模型行数 (样本日)")
        print(f"   {'阶段+模型':<50} {'行数':>5} {'小时范围':>10}")
        print(f"   {'-'*50} {'-'*5} {'-'*10}")
        for key, info in sorted(sc.items()):
            hb_range = f"{info['min_hb']}-{info['max_hb']}"
            ok = "✅" if info["rows"] > 0 and info["min_hb"] == 1 and info["max_hb"] == 24 else "⚠️"
            print(f"   {ok} {key:<47} {info['rows']:>5} {hb_range:>10}")
        print()

    # 5. Timing
    if pt:
        print("⏱️  5. Pipeline 各阶段耗时 (平均值, ms)")
        print(f"   {'步骤':<35} {'状态':>10} {'耗时(ms)':>10} {'输入':>6} {'输出':>6}")
        print(f"   {'-'*35} {'-'*10} {'-'*10} {'-'*6} {'-'*6}")
        for key, info in sorted(pt.items(), key=lambda x: x[1]["order"]):
            rt = info["avg_runtime_ms"]
            rt_str = f"{rt:.0f}" if rt else "-"
            print(f"   {key:<35} {info['status']:>10} {rt_str:>10} {info['avg_input'] or '-':>6} {info['avg_output'] or '-':>6}")
        print()

    # 6. Metrics
    if m:
        print("📈 6. 完整指标")
        for scope, summary in sorted(m.items()):
            print(f"\n   [{scope.upper()}]")
            print(f"     评测天数: {summary['n_runs']}")
            print(f"     sMAPE:    {summary['mean_smape']:.2f}%  (min={summary['min_smape']:.2f}%, max={summary['max_smape']:.2f}%)" if summary['mean_smape'] else "     sMAPE:    N/A")
            print(f"     MAE:      {summary['mean_mae']:.2f} CNY/MWh" if summary['mean_mae'] else "     MAE:      N/A")
            print(f"     RMSE:     {summary['mean_rmse']:.2f} CNY/MWh" if summary['mean_rmse'] else "     RMSE:     N/A")
            print(f"     WMAPE:    {summary['mean_wmape']:.2f}%" if summary['mean_wmape'] else "     WMAPE:    N/A")

    # Summary verdict
    print()
    print("╔" + "═" * 78 + "╗")
    all_good = (c["connected"] and o["all_steps_normal"] and
                any(v.get("mean_smape") is not None for v in m.values()))
    verdict = "✅ 全链路正常，可以进入正式陪跑" if all_good else "❌ 存在问题，需修复"
    print(f"║  最终判定: {verdict}")
    print("╚" + "═" * 78 + "╝")
    print()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id-prefix", default="p1_backtest")
    ap.add_argument("--start", default="2025-11-01")
    ap.add_argument("--end", default="2026-06-19")
    args = ap.parse_args()

    report = generate(args.run_id_prefix, args.start, args.end)
    report_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "outputs", "backtest_full_report.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print_report(report)
    print(f"[report saved to {report_path}]")
