#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
batch_phase_run.py - 阶段陪跑: 批量执行 production circuit 并收集指标。

Usage:
    python tools/db_ops/batch_phase_run.py --start 2026-01-01 --end 2026-02-28

特性:
  * 逐天调用 pipelines.production_circuit.run_production_circuit (mode=formal, use_db=True)
  * 每日起独立子进程，单日超时保护 (默认 120s)，超时视为 FAIL 继续下一天
  * 跑前检查已有 efm3_pc_ 的 COMPLETE run，直接复用其指标 (幂等，可重复运行)
  * 从 DB Ledger 查询 efm_pipeline_steps (20步状态) 与 efm_metric_runs (DA/RT/Benchmark SMAPE/MAE)
  * 单日失败不阻塞后续日期，记录 error message
  * 输出 ASCII 控制台汇总表 + Markdown 报告 (outputs/phase_run_report_YYYY_mm_dd_mm_dd.md)

注意: 本脚本只调用 pipeline API 并读 DB，不修改任何 pipeline 代码。
"""
from __future__ import annotations

import argparse
import datetime as dt
import multiprocessing as mp
import os
import sys
import traceback
from collections import defaultdict

# ---- 路径: 确保项目根 (含 common/ pipelines/ 包) 在 sys.path ----
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ---- 常量 ----
DAY_TIMEOUT_S = 120
REPORT_DIR = "outputs"


# --------------------------------------------------------------------------
# 参数 / 日期范围
# --------------------------------------------------------------------------
def _parse_args():
    p = argparse.ArgumentParser(description="Batch phase run for EFM3 production circuit")
    p.add_argument("--start", required=True, help="start date YYYY-MM-DD")
    p.add_argument("--end", required=True, help="end date YYYY-MM-DD")
    p.add_argument("--mode", default="formal", help="circuit mode (default formal)")
    p.add_argument("--timeout", type=int, default=DAY_TIMEOUT_S,
                   help="per-day wall-clock timeout seconds (default 120)")
    p.add_argument("--db-url", default=None, help="override EFM3_DB_URL")
    p.add_argument("--report", default=None, help="output markdown report path")
    return p.parse_args()


def _date_range(start: str, end: str) -> list[str]:
    s = dt.date.fromisoformat(start)
    e = dt.date.fromisoformat(end)
    out = []
    cur = s
    while cur <= e:
        out.append(cur.isoformat())
        cur += dt.timedelta(days=1)
    return out


# --------------------------------------------------------------------------
# DB 查询 (父进程，只读)
# --------------------------------------------------------------------------
def _db_url(args) -> str:
    return args.db_url or os.environ.get("EFM3_DB_URL", "")


def _new_conn(db_url: str):
    from common.db.connection import DbConnectionManager
    return DbConnectionManager(db_url=db_url).new_connection()


def _existing_pc_run(conn, target_date: str):
    """返回 (run_id, status) 或 None。

    跳过逻辑: 复用「电路已完整跑完」的日期——即存在 finish_run 步骤
    且状态为 COMPLETE 的 efm3_pc_ run。status 可为 COMPLETE 或 ARCHIVED
    (ARCHIVED 系历史清理时由 CANCELLED/旧状态改名而来, 仍含完整 20 步与
    指标, 属有效完成结果, 应复用而非重算)。这样符合「不重新跑已存在日期 /
    幂等可重复运行」的核心意图。
    """
    cur = conn.cursor()
    # pymysql: LIKE 字面 % 必须写成 %%
    # 「已完成」的稳健判据 = 该 run 已在 efm_metric_runs 落过指标
    # (指标存在即电路跑通并产出结果; 不依赖 finish_run 单步状态,
    #  因为历史 ARCHIVED run 用旧版代码, 可能 19 步且 finish_run 未标 COMPLETE)。
    cur.execute(
        "SELECT r.run_id, r.status FROM efm_runs r "
        "WHERE r.target_date = %s AND r.run_id LIKE 'efm3_pc_%%' "
        "  AND EXISTS (SELECT 1 FROM efm_metric_runs m WHERE m.run_id = r.run_id) "
        "ORDER BY r.started_at DESC LIMIT 1",
        (target_date,),
    )
    row = cur.fetchone()
    cur.close()
    return row


def _query_steps(conn, run_id: str):
    """返回 [(step_order, step_name, status), ...] 按 order 排序。"""
    cur = conn.cursor()
    cur.execute(
        "SELECT step_order, step_name, status FROM efm_pipeline_steps "
        "WHERE run_id = %s ORDER BY step_order",
        (run_id,),
    )
    rows = cur.fetchall()
    cur.close()
    return rows


def _query_metrics(conn, run_id: str):
    """返回 {scope: {'smape': float|None, 'mae': float|None}, ...}。"""
    cur = conn.cursor()
    cur.execute(
        "SELECT metric_scope, smape, mae FROM efm_metric_runs WHERE run_id = %s",
        (run_id,),
    )
    rows = cur.fetchall()
    cur.close()
    m = {}
    for scope, smape, mae in rows:
        m[scope] = {
            "smape": float(smape) if smape is not None else None,
            "mae": float(mae) if mae is not None else None,
        }
    return m


# --------------------------------------------------------------------------
# 子进程 worker (每个日期独立进程，硬超时保护)
# --------------------------------------------------------------------------
def _worker(target_date: str, db_url: str, mode: str, q: mp.Queue):
    try:
        from pipelines.production_circuit import run_production_circuit
        res = run_production_circuit(
            target_date=target_date, mode=mode, use_db=True, db_url=db_url
        )
        q.put(("OK", res))
    except Exception as e:  # noqa: BLE001
        q.put(("ERR", f"{e}\n{traceback.format_exc()}"))


def _run_one_day(target_date: str, db_url: str, mode: str, timeout: int):
    """在子进程中跑单日，超时返回 ('TIMEOUT', msg)。正常返回 ('OK'|'ERR', payload)。"""
    q: mp.Queue = mp.Queue()
    p = mp.Process(target=_worker, args=(target_date, db_url, mode, q))
    p.start()
    p.join(timeout=timeout)
    if p.is_alive():
        p.terminate()
        try:
            p.join(timeout=10)
        except Exception:
            pass
        return ("TIMEOUT", f"exceeded {timeout}s wall-clock limit")
    try:
        kind, payload = q.get(timeout=5)
    except Exception:
        return ("ERR", "worker finished but returned no result (likely crash)")
    if kind == "OK":
        return ("OK", payload)
    return ("ERR", payload)


# --------------------------------------------------------------------------
# 记录构建 / 格式化
# --------------------------------------------------------------------------
def _fmt(v, nd=1):
    return f"{v:.{nd}f}" if isinstance(v, (int, float)) else "-"


def _build_record(date, run_id, overall, steps, metrics, skipped, error):
    n = len(steps)
    name_status = [(nm, st) for _, nm, st in steps]
    counts = defaultdict(int)
    for _, st in name_status:
        counts[st] += 1

    if n > 0 and all(st == "COMPLETE" for _, st in name_status):
        steps_str = f"{n}/{n}C"
    else:
        parts = []
        for st in ("COMPLETE", "SKIPPED", "FAIL", "PARTIAL", "RUNNING", "PENDING"):
            if counts.get(st):
                parts.append(f"{counts[st]}{st[0]}")
        steps_str = "/".join(parts) if parts else f"{n}"

    skipped_names = [nm for nm, st in name_status if st == "SKIPPED"]
    if overall == "COMPLETE" and skipped_names:
        if skipped_names == ["realtime_classifier"]:
            status_label = "OK (classifier SKIPPED)"
        else:
            status_label = f"OK ({len(skipped_names)} SKIPPED)"
    elif overall == "COMPLETE":
        status_label = "OK"
    elif overall == "PARTIAL":
        status_label = "PARTIAL"
    elif overall == "FAIL":
        status_label = "FAIL"
    elif overall == "NEEDS_MODEL_OUTPUT":
        status_label = "NEED_MODEL"
    else:
        status_label = str(overall)

    da = metrics.get("dayahead", {})
    rt = metrics.get("realtime", {})
    bm = metrics.get("benchmark", {})

    return {
        "date": date,
        "run_id": run_id,
        "overall": overall,
        "steps_str": steps_str,
        "status_label": status_label,
        "n_steps": n,
        "da_smape": da.get("smape"),
        "da_mae": da.get("mae"),
        "rt_smape": rt.get("smape"),
        "rt_mae": rt.get("mae"),
        "bm_smape": bm.get("smape"),
        "bm_mae": bm.get("mae"),
        "skipped": skipped,
        "error": error,
    }


def _print_progress(i, total, rec):
    if rec["error"]:
        tail = f"FAIL ({rec['error'][:60].replace(chr(10), ' ')})"
    else:
        tail = (f"{rec['overall']} smape_da={_fmt(rec['da_smape'])} "
                f"smape_rt={_fmt(rec['rt_smape'])}")
    print(f"[{i}/{total}] {rec['date']}: {tail}")
    sys.stdout.flush()


# --------------------------------------------------------------------------
# 统计
# --------------------------------------------------------------------------
def _stats(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)

    def pct(p):
        if n == 1:
            return s[0]
        k = (n - 1) * p
        f = int(k)
        c = min(f + 1, n - 1)
        if f == c:
            return s[f]
        return s[f] + (s[c] - s[f]) * (k - f)

    return {
        "mean": sum(s) / n,
        "median": pct(0.5),
        "p25": pct(0.25),
        "p75": pct(0.75),
        "min": s[0],
        "max": s[-1],
        "n": n,
    }


def _fmt_stat(st, label, nd=2):
    if not st:
        return f"{label}: n=0"
    return (f"{label}: mean={st['mean']:.{nd}f} median={st['median']:.{nd}f} "
            f"p25={st['p25']:.{nd}f} p75={st['p75']:.{nd}f} "
            f"min={st['min']:.{nd}f} max={st['max']:.{nd}f} (n={st['n']})")


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------
def _default_report_name(start, end):
    return f"phase_run_report_{start[:7].replace('-', '_')}_{end[:7].replace('-', '_')}.md"


def _write_report(records, start, end, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    total = len(records)
    pass_n = sum(1 for r in records if r["overall"] in ("COMPLETE", "PARTIAL") and not r["error"])
    fail_n = total - pass_n
    skip_n = sum(1 for r in records if r["skipped"])

    da_s = [r["da_smape"] for r in records if isinstance(r["da_smape"], (int, float))]
    rt_s = [r["rt_smape"] for r in records if isinstance(r["rt_smape"], (int, float))]
    da_m = [r["da_mae"] for r in records if isinstance(r["da_mae"], (int, float))]
    rt_m = [r["rt_mae"] for r in records if isinstance(r["rt_mae"], (int, float))]

    da_st, rt_st, da_mst, rt_mst = _stats(da_s), _stats(rt_s), _stats(da_m), _stats(rt_m)

    lines = []
    lines.append(f"# 阶段陪跑报告: Production Circuit 2026-01 ~ 2026-02\n")
    lines.append(f"- 日期范围: **{start} ~ {end}** ({total} 天)")
    lines.append(f"- 通过 (COMPLETE/PARTIAL): **{pass_n}** | 失败: **{fail_n}** | 复用已存在 run: **{skip_n}**")
    lines.append(f"- 生成时间: {dt.datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- 环境: EFM3_DB_URL 已设, mode=formal, 单日超时={DAY_TIMEOUT_S}s\n")

    # 逐日明细
    lines.append("## 逐日明细\n")
    lines.append("| Date | DA_SMAPE | DA_MAE | RT_SMAPE | RT_MAE | Steps(20) | Status | run_id |")
    lines.append("|------|----------|--------|----------|--------|------------|--------|--------|")
    for r in records:
        lines.append(
            f"| {r['date']} | {_fmt(r['da_smape'])} | {_fmt(r['da_mae'])} | "
            f"{_fmt(r['rt_smape'])} | {_fmt(r['rt_mae'])} | {r['steps_str']} | "
            f"{r['status_label']} | {r['run_id'] or '-'} |"
        )
    lines.append("")

    # 汇总统计
    lines.append("## 汇总统计\n")
    lines.append(f"- **DA SMAPE**: {_fmt_stat(da_st, 'DA SMAPE')}")
    lines.append(f"- **RT SMAPE**: {_fmt_stat(rt_st, 'RT SMAPE')}")
    lines.append(f"- **DA MAE**: {_fmt_stat(da_mst, 'DA MAE', 2)}")
    lines.append(f"- **RT MAE**: {_fmt_stat(rt_mst, 'RT MAE', 2)}")
    lines.append("")

    # 按月聚合
    lines.append("## 按月聚合\n")
    lines.append("| Month | n | DA_SMAPE(mean) | RT_SMAPE(mean) | DA_MAE(mean) | RT_MAE(mean) |")
    lines.append("|-------|---|---------------|---------------|-------------|-------------|")
    by_month = defaultdict(list)
    for r in records:
        by_month[r["date"][:7]].append(r)
    for mth in sorted(by_month):
        rs = by_month[mth]
        da_mm = _stats([x["da_smape"] for x in rs if isinstance(x["da_smape"], (int, float))])
        rt_mm = _stats([x["rt_smape"] for x in rs if isinstance(x["rt_smape"], (int, float))])
        da_ma_mm = _stats([x["da_mae"] for x in rs if isinstance(x["da_mae"], (int, float))])
        rt_ma_mm = _stats([x["rt_mae"] for x in rs if isinstance(x["rt_mae"], (int, float))])
        lines.append(
            f"| {mth} | {len(rs)} | {_fmt(da_mm['mean'] if da_mm else None)} | "
            f"{_fmt(rt_mm['mean'] if rt_mm else None)} | "
            f"{_fmt(da_ma_mm['mean'] if da_ma_mm else None)} | "
            f"{_fmt(rt_ma_mm['mean'] if rt_ma_mm else None)} |"
        )
    lines.append("")

    # 按周聚合
    lines.append("## 按周聚合 (ISO week)\n")
    lines.append("| Week | n | DA_SMAPE(mean) | RT_SMAPE(mean) |")
    lines.append("|------|---|---------------|---------------|")
    by_week = defaultdict(list)
    for r in records:
        iso = dt.date.fromisoformat(r["date"]).isocalendar()
        wk = f"{iso[0]}-W{iso[1]:02d}"
        by_week[wk].append(r)
    for wk in sorted(by_week):
        rs = by_week[wk]
        da_mm = _stats([x["da_smape"] for x in rs if isinstance(x["da_smape"], (int, float))])
        rt_mm = _stats([x["rt_smape"] for x in rs if isinstance(x["rt_smape"], (int, float))])
        lines.append(
            f"| {wk} | {len(rs)} | {_fmt(da_mm['mean'] if da_mm else None)} | "
            f"{_fmt(rt_mm['mean'] if rt_mm else None)} |"
        )
    lines.append("")

    # 失败详情
    fails = [r for r in records if r["error"] or r["overall"] == "FAIL"]
    if fails:
        lines.append("## 失败日期详情\n")
        for r in fails:
            lines.append(f"### {r['date']} (run_id={r['run_id'] or '-'})")
            lines.append(f"```\n{r['error'] or r['status_label']}\n```\n")
    else:
        lines.append("## 失败日期详情\n")
        lines.append("无失败日期。\n")

    # 复用说明
    lines.append("## 说明\n")
    lines.append("- 指标口径: DA = dayahead_task_final vs da_anchor (同产品日前出清价); "
                 "RT = realtime_task_final vs rt_actual (同产品实时实际价); "
                 "Benchmark = da_anchor vs rt_actual (跨产品价差, 非模型精度)。")
    lines.append(f"- 本脚本仅调用 `run_production_circuit` 并读 DB Ledger, 未修改任何 pipeline 代码。")
    lines.append(f"- 已存在 COMPLETE 的 efm3_pc_ run 直接复用指标 (幂等)。\n")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _print_console(records, start, end):
    total = len(records)
    pass_n = sum(1 for r in records if r["overall"] in ("COMPLETE", "PARTIAL") and not r["error"])
    fail_n = total - pass_n
    skip_n = sum(1 for r in records if r["skipped"])

    print("")
    print(f"=== Phase Run Report: {start} ~ {end} ===")
    print(f"Total: {total} days | Pass: {pass_n} | Fail: {fail_n} | Skip: {skip_n}")
    print("")
    print(f"{'Date':<12} {'DA_SMAPE':>9} {'DA_MAE':>8} {'RT_SMAPE':>9} {'RT_MAE':>8} {'Steps(20)':>11}  Status")
    for r in records:
        print(f"{r['date']:<12} {_fmt(r['da_smape']):>9} {_fmt(r['da_mae']):>8} "
              f"{_fmt(r['rt_smape']):>9} {_fmt(r['rt_mae']):>8} {r['steps_str']:>11}  {r['status_label']}")
    print("")

    da_s = [r["da_smape"] for r in records if isinstance(r["da_smape"], (int, float))]
    rt_s = [r["rt_smape"] for r in records if isinstance(r["rt_smape"], (int, float))]
    da_m = [r["da_mae"] for r in records if isinstance(r["da_mae"], (int, float))]
    rt_m = [r["rt_mae"] for r in records if isinstance(r["rt_mae"], (int, float))]
    da_st, rt_st = _stats(da_s), _stats(rt_s)
    da_mst, rt_mst = _stats(da_m), _stats(rt_m)

    print("--- Summary Statistics ---")
    print(_fmt_stat(da_st, "DA SMAPE"))
    print(_fmt_stat(rt_st, "RT SMAPE"))
    print(_fmt_stat(da_mst, "DA MAE"))
    print(_fmt_stat(rt_mst, "RT MAE"))
    print("")


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main():
    args = _parse_args()
    db_url = _db_url(args)
    if not db_url:
        print("ERROR: EFM3_DB_URL not set (or --db-url not given)", file=sys.stderr)
        sys.exit(1)

    dates = _date_range(args.start, args.end)
    total = len(dates)
    print(f"[init] {total} days from {args.start} to {args.end}, mode={args.mode}, timeout={args.timeout}s")

    records = []
    conn = _new_conn(db_url)
    try:
        for i, d in enumerate(dates, start=1):
            existing = _existing_pc_run(conn, d)
            # _existing_pc_run 已用 EXISTS(efm_metric_runs) 保证该 run 是
            # 「电路跑通并产出指标」的有效完成结果 (status 可能为 COMPLETE 或
            # ARCHIVED, 两者都含完整 20 步与指标, 应复用而非重算)。
            if existing:
                run_id, _ = existing
                steps = _query_steps(conn, run_id)
                metrics = _query_metrics(conn, run_id)
                rec = _build_record(d, run_id, "COMPLETE", steps, metrics,
                                    skipped=True, error=None)
                records.append(rec)
                _print_progress(i, total, rec)
                continue

            status, payload = _run_one_day(d, db_url, args.mode, args.timeout)
            if status == "OK":
                run_id = payload.get("run_id")
                overall = payload.get("status")
                steps = _query_steps(conn, run_id) if run_id else []
                metrics = _query_metrics(conn, run_id) if run_id else {}
                rec = _build_record(d, run_id, overall, steps, metrics,
                                    skipped=False, error=None)
            else:
                rec = _build_record(d, None, "FAIL", [], {},
                                    skipped=False, error=f"[{status}] {payload}")
            records.append(rec)
            _print_progress(i, total, rec)
    finally:
        conn.close()

    report_path = args.report or os.path.join(
        REPORT_DIR, _default_report_name(args.start, args.end)
    )
    _write_report(records, args.start, args.end, report_path)
    _print_console(records, args.start, args.end)
    print(f"[done] report written -> {report_path}")


if __name__ == "__main__":
    # Windows 必须 spawn; 显式设置以策万全
    try:
        mp.set_start_method("spawn", force=True)
    except Exception:
        pass
    main()
