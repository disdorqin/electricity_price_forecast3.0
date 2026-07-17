#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
load_actuals_and_backfill.py - 更新 2026-06 末尾缺失实际价 + 回填缺漏指标。

背景:
  * efm_actual_prices 中 2026-06-29 / 2026-06-30 的 da_anchor / rt_actual
    为 NULL (历史导入在 06-19 截止), 导致这 2 天电路跑完 (18C/1S)
    却无实际价可评分 -> 没落 efm_metric_runs。
  * 另有 2026-06-20~28 共 9 天电路跑完但 efm_metric_runs 缺行
    (旧 metric 步骤未落库, 且 db_yearly_metrics._persist_scope 有 `id`
     列 bug)。这 9 天实际价已存在, 仅需回填指标。

动作 (均幂等, ON DUPLICATE KEY UPDATE):
  1. 从 data/shandong_pmos_hourly_0702.xlsx (Sheet1) 读取 06-29~30 的
     日前电价 / 实时电价, UPSERT 进 efm_actual_prices。
  2. 对 2026-03~06 内「有 pc run 但无 efm_metric_runs 行」的日期,
     直接用生产 floor50 SMAPE/MAE 公式 (与 compute_metrics_floor50 一致)
     从已落库 final 预测 vs actual 重算, 并写入 efm_metric_runs,
     格式与已有 111 天完全一致 (metric_run_id 前缀 da_/rt_/bm_/dl_)。

Usage:
    python tools/db_ops/load_actuals_and_backfill.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pandas as pd
import pymysql
import urllib.parse

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

SRC_XLSX = os.path.join(_ROOT, "data", "shandong_pmos_hourly_0702.xlsx")
MISSING_ACTUAL_DATES = ["2026-06-29", "2026-06-30"]


def _db():
    url = os.environ.get("EFM3_DB_URL", "")
    raw = url.replace("mysql+pymysql://", "").replace("mysql://", "")
    user_pass, rest = raw.split("@", 1)
    user, password = user_pass.split(":", 1)
    password = urllib.parse.unquote(password)
    host_port, database = rest.split("/", 1)
    host, port = (host_port.split(":", 1) + ["3306"])[:2]
    return pymysql.connect(host=host, port=int(port), user=user,
                          password=password, database=database, charset="utf8mb4")


def _period_for_hb(hb: int) -> str:
    if hb <= 8:
        return "1_8"
    if hb <= 16:
        return "9_16"
    return "17_24"


def load_missing_actuals(conn):
    """UPSERT 06-29~30 实际价 (来自 xlsx)。返回写入行数。"""
    df = pd.read_excel(SRC_XLSX, sheet_name="Sheet1")
    df["_ts"] = pd.to_datetime(df["时刻"])
    sub = df[df["_ts"].dt.date.astype(str).isin(MISSING_ACTUAL_DATES)].copy()
    rows = []
    for _, r in sub.iterrows():
        ts: dt.datetime = r["_ts"]
        td = ts.date().isoformat()
        hb = ts.hour + 1  # 00:00 -> hb=1
        da = float(r["日前电价"]) if pd.notna(r["日前电价"]) else None
        rt = float(r["实时电价"]) if pd.notna(r["实时电价"]) else None
        if da is None or rt is None:
            continue
        rows.append((td, hb, _period_for_hb(hb), da, rt))
    cur = conn.cursor()
    n = 0
    for td, hb, period, da, rt in rows:
        # 注意: period 是 generated column, 不能显式写入, 由 MySQL 自动计算
        cur.execute(
            """
            INSERT INTO efm_actual_prices
                (target_date, hour_business, da_anchor, rt_actual,
                 source_file, loaded_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE
                da_anchor = VALUES(da_anchor),
                rt_actual = VALUES(rt_actual),
                source_file = VALUES(source_file),
                loaded_at = NOW()
            """,
            (td, hb, da, rt, os.path.basename(SRC_XLSX)),
        )
        n += 1
    conn.commit()
    cur.close()
    return n


def _floor50_smape_mae(preds: dict, actuals: dict):
    """复刻 db_yearly_metrics.compute_metrics_floor50 (2.5-aligned)。"""
    common = sorted(set(preds) & set(actuals))
    if not common:
        return None, None, 0
    sv, mv = [], []
    for hb in common:
        p = float(preds[hb]); a = float(actuals[hb])
        pc, ac = max(p, 50.0), max(a, 50.0)
        denom = abs(pc) + abs(ac)
        sv.append(200.0 * abs(pc - ac) / denom if denom > 0 else 0.0)
        mv.append(abs(p - a))
    return sum(sv) / len(sv), sum(mv) / len(mv), len(common)


def _load_preds(cur, run_id, stage, model):
    cur.execute(
        "SELECT hour_business, pred_price FROM efm_predictions "
        "WHERE run_id=%s AND stage=%s AND model_name=%s",
        (run_id, stage, model),
    )
    return {hb: float(v) for hb, v in cur.fetchall()}


def _load_actuals(cur, target_date):
    cur.execute(
        "SELECT hour_business, da_anchor, rt_actual FROM efm_actual_prices "
        "WHERE target_date=%s", (target_date,),
    )
    da, rt = {}, {}
    for hb, a, b in cur.fetchall():
        if a is not None:
            da[hb] = float(a)
        if b is not None:
            rt[hb] = float(b)
    return da, rt


SCOPES = {
    "dayahead": ("dayahead_task_final", "dayahead_final", "da_anchor",
                  '{"note": "REAL day-ahead model vs da_anchor (same product = day-ahead clearing price)"}'),
    "realtime": ("realtime_task_final", "realtime_final", "rt_actual",
                 '{"note": "REAL realtime model vs rt_actual (same product)"}'),
    "benchmark": ("benchmark_da_anchor", "da_anchor", "rt_actual",
                  '{"note": "BENCHMARK da_anchor vs rt_actual, NOT model performance"}'),
    "delivery": ("delivery_final", "delivery_final", "rt_actual",
                  '{"note": "REAL delivery vs rt_actual"}'),
}


def backfill_metrics(conn):
    cur = conn.cursor()
    cur.execute(
        "SELECT r.target_date, r.run_id FROM efm_runs r "
        "WHERE r.run_id LIKE 'efm3_pc_%%' "
        "  AND NOT EXISTS (SELECT 1 FROM efm_metric_runs m WHERE m.run_id = r.run_id) "
        "  AND r.target_date BETWEEN '2026-03-01' AND '2026-06-30' "
        "ORDER BY r.target_date",
    )
    missing = cur.fetchall()
    print(f"[backfill] pc-run 缺指标行: {len(missing)} 天")
    done = 0
    for target_date, run_id in missing:
        da_act, rt_act = _load_actuals(cur, target_date)
        for scope, (stage, model, actual_src, note) in SCOPES.items():
            if scope == "benchmark":
                preds = da_act            # benchmark: da_anchor 作为 pred
                actuals = rt_act
                pred_stage = "benchmark_da_anchor"
            else:
                preds = _load_preds(cur, run_id, stage, model)
                actuals = da_act if actual_src == "da_anchor" else rt_act
                pred_stage = stage
            smape, mae, nh = _floor50_smape_mae(preds, actuals)
            if smape is None:
                print(f"  [SKIP] {target_date} {scope}: no overlap data")
                continue
            prefix = {"dayahead": "da", "realtime": "rt",
                       "benchmark": "bm", "delivery": "dl"}[scope]
            metric_run_id = f"{prefix}_{run_id}_{target_date}"
            cur.execute(
                """
                INSERT INTO efm_metric_runs
                    (metric_run_id, run_id, target_date_start, target_date_end,
                     metric_scope, pred_stage, actual_source, smape, mae,
                     evaluable_days, evaluable_hours, config_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
                ON DUPLICATE KEY UPDATE
                    smape=VALUES(smape), mae=VALUES(mae),
                    evaluable_hours=VALUES(evaluable_hours),
                    config_json=VALUES(config_json)
                """,
                (metric_run_id, run_id, target_date, target_date, scope,
                 pred_stage, actual_src, round(smape, 4), round(mae, 4),
                 nh, note),
            )
        conn.commit()
        done += 1
        print(f"  [OK] {target_date} ({run_id[:30]}...) 回填完成")
    cur.close()
    return done


def main():
    conn = _db()
    try:
        n_act = load_missing_actuals(conn)
        print(f"[load] 06-29~30 实际价 UPSERT 行数: {n_act}")
        n_met = backfill_metrics(conn)
        print(f"[backfill] 回填指标天数: {n_met}")
        print("[done]")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
