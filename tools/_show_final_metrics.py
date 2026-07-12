#!/usr/bin/env python
"""Focused metrics output: real-time, arbitrage, per-period breakdown."""
from __future__ import annotations

import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql
from collections import defaultdict
from math import copysign

DB_URL = os.environ.get("EFM3_DB_URL", "mysql+pymysql://root:Zlt20060313%23@127.0.0.1:3306/efm3")
SMAFE_FLOOR = 50

def _connect():
    from common.db.connection import DbConnectionManager
    mgr = DbConnectionManager(db_url=DB_URL)
    return mgr.new_connection()

conn = _connect()
cur = conn.cursor()

# 1. Fetch ALL dayahead_task_final predictions + actuals
cur.execute("""
    SELECT p.target_date, p.hour_business, p.pred_price,
           a.da_anchor, a.rt_actual
    FROM efm_predictions p
    JOIN efm_actual_prices a
      ON p.target_date = a.target_date AND p.hour_business = a.hour_business
    WHERE p.stage = 'dayahead_task_final' AND p.task = 'dayahead'
      AND p.target_date BETWEEN '2025-11-01' AND '2026-06-19'
      AND a.da_anchor IS NOT NULL
    ORDER BY p.target_date, p.hour_business
""")
records = [{
    "date": str(r[0]), "hb": int(r[1]),
    "pred": float(r[2]), "da": float(r[3]),
    "rt": float(r[4]) if r[4] is not None else None,
} for r in cur.fetchall()]

# 2. Per-period classification
for rec in records:
    hb = rec["hb"]
    rec["period"] = "1_8(谷)" if 1 <= hb <= 8 else ("9_16(平)" if hb <= 16 else "17_24(峰)")

# 3. Compute functions
def clipped_smape(preds, actuals):
    n = len(preds)
    if n == 0: return 0.0
    total = sum(abs(max(p,SMAFE_FLOOR)-max(a,SMAFE_FLOOR)) / max((abs(max(p,SMAFE_FLOOR))+abs(max(a,SMAFE_FLOOR)))/2, 1e-10)
                for p, a in zip(preds, actuals))
    return total / n * 100.0

def mae(preds, actuals):
    return sum(abs(p-a) for p,a in zip(preds,actuals)) / len(preds) if preds else 0

# 4. DA metrics per period
print("=" * 80)
print("  日前 (DAY-AHEAD) 预测 vs da_anchor")
print("=" * 80)
da_all = [r["pred"] for r in records]
act_da = [r["da"] for r in records]
print(f"\n  总样本: {len(records)} 小时 / {len(set(r['date'] for r in records))} 天")
print(f"  sMAPE(floor50): {clipped_smape(da_all, act_da):.2f}%")
print(f"  MAE:           {mae(da_all, act_da):.2f} CNY/MWh")

# 5. Realtime metrics (using realtime_task_final for RT, or DA_anchor as fallback)
print()
print("=" * 80)
print("  实时 (REAL-TIME) 预测 vs rt_actual")
print("=" * 80)

# RT: query realtime_task_final predictions
cur.execute("""
    SELECT p.target_date, p.hour_business, p.pred_price,
           a.da_anchor, a.rt_actual
    FROM efm_predictions p
    JOIN efm_actual_prices a
      ON p.target_date = a.target_date AND p.hour_business = a.hour_business
    WHERE p.stage = 'realtime_task_final' AND p.task = 'realtime'
      AND p.target_date BETWEEN '2025-11-01' AND '2026-06-19'
      AND a.rt_actual IS NOT NULL
    ORDER BY p.target_date, p.hour_business
""")
rt_records = [{
    "date": str(r[0]), "hb": int(r[1]),
    "pred": float(r[2]), "da": float(r[3]),
    "rt": float(r[4]),
} for r in cur.fetchall()]
for rec in rt_records:
    hb = rec["hb"]
    rec["period"] = "1_8(谷)" if 1 <= hb <= 8 else ("9_16(平)" if hb <= 16 else "17_24(峰)")

rt_all = [r["pred"] for r in rt_records]
rt_act = [r["rt"] for r in rt_records]
rt_daa = [r["da"] for r in rt_records]

print(f"\n  总样本: {len(rt_records)} 小时 / {len(set(r['date'] for r in rt_records))} 天")
# RT vs rt_actual
rt_smape = clipped_smape(rt_all, rt_act)
rt_mae_v = mae(rt_all, rt_act)
# Also compute benchmark: DA_anchor vs rt_actual
bench_preds = rt_daa
bench_smape = clipped_smape(bench_preds, rt_act)
bench_mae_v = mae(bench_preds, rt_act)
print(f"  RT预测 vs rt_actual:   sMAPE={rt_smape:.2f}%   MAE={rt_mae_v:.2f}")
print(f"  DA_anchor vs rt_actual: sMAPE={bench_smape:.2f}%   MAE={bench_mae_v:.2f}  (基准对照)")

# Per-period breakdown for RT
print()
print("  ┌──────────┬────────┬──────────┬──────────┬──────────┐")
print("  │ 时段     │ 小时数 │ RT sMAPE │ DA_anchor│ 差异     │")
print("  ├──────────┼────────┼──────────┼──────────┼──────────┤")
for p in ["1_8(谷)", "9_16(平)", "17_24(峰)"]:
    subset = [r for r in rt_records if r["period"] == p]
    if not subset:
        continue
    sp = clipped_smape([r["pred"] for r in subset], [r["rt"] for r in subset])
    bp = clipped_smape([r["da"] for r in subset], [r["rt"] for r in subset])
    print(f"  │ {p:<8} │ {len(subset):>6} │ {sp:>8.2f}% │ {bp:>8.2f}% │ {sp-bp:>+6.2f}% │")
print("  └──────────┴────────┴──────────┴──────────┴──────────┘")

# 6. Arbitrage: per-period breakdown
print()
print("=" * 80)
print("  度电套利 (Arbitrage) — 分时段")
print("=" * 80)

def arbitrage_basic(preds, da_actuals, rt_actuals):
    profit = 0.0
    volume = 0
    for p, da, rt in zip(preds, da_actuals, rt_actuals):
        if rt is None:
            continue
        if p > da:
            profit += rt - da
            volume += 1
    return profit, volume

def arbitrage_improved(pred_da, pred_rt, da_actuals, rt_actuals):
    profit = 0.0
    volume = 0
    for pd_, pr, da, rt in zip(pred_da, pred_rt, da_actuals, rt_actuals):
        if rt is None or pr is None:
            continue
        if pr > pd_ and pd_ > da:
            profit += rt - da
            volume += 1
    return profit, volume

print()
print("  ┌──────────┬──────────┬───────────┬──────────┬───────────┬──────────┐")
print("  │ 时段     │ DA-sMAPE │ DA-MAE    │ 交易次数 │ 总利润    │ 单位利润 │")
print("  ├──────────┼──────────┼───────────┼──────────┼───────────┼──────────┤")

for p in ["1_8(谷)", "9_16(平)", "17_24(峰)"]:
    subset = [r for r in records if r["period"] == p]
    if not subset:
        continue
    sp = clipped_smape([r["pred"] for r in subset], [r["da"] for r in subset])
    mv = mae([r["pred"] for r in subset], [r["da"] for r in subset])
    prof, vol = arbitrage_basic(
        [r["pred"] for r in subset],
        [r["da"] for r in subset],
        [r["rt"] for r in subset],
    )
    unit = prof / vol if vol else 0
    print(f"  │ {p:<8} │ {sp:>8.2f}% │ {mv:>9.2f} │ {vol:>8} │ {prof:>9.2f} │ {unit:>8.2f} │")

# Overall
prof_t, vol_t = arbitrage_basic(da_all, act_da, [r["rt"] for r in records])
unit_t = prof_t / vol_t if vol_t else 0
print(f"  ├──────────┼──────────┼───────────┼──────────┼───────────┼──────────┤")
print(f"  │ 合计     │ {clipped_smape(da_all, act_da):>8.2f}% │ {mae(da_all, act_da):>9.2f} │ {vol_t:>8} │ {prof_t:>9.2f} │ {unit_t:>8.2f} │")
print("  └──────────┴──────────┴───────────┴──────────┴───────────┴──────────┘")

# 7. SCR per period
print()
print("=" * 80)
print("  价差方向准确率 (SCR)")
print("=" * 80)
print()
print("  ┌──────────┬──────────┬──────────┐")
print("  │ 时段     │ 样本数   │ SCR      │")
print("  ├──────────┼──────────┼──────────┤")
for p in ["1_8(谷)", "9_16(平)", "17_24(峰)"]:
    subset = [r for r in records if r["period"] == p]
    if not subset:
        continue
    n_correct = 0
    n_total = 0
    for r in subset:
        if r["rt"] is None:
            continue
        n_total += 1
        real_spread = r["rt"] - r["da"]
        pred_spread = r["pred"] - r["da"]
        if copysign(1, real_spread) == copysign(1, pred_spread) or abs(real_spread) < 1e-10:
            n_correct += 1
    scr = n_correct / n_total * 100 if n_total else 0
    print(f"  │ {p:<8} │ {n_total:>8} │ {scr:>8.2f}% │")
# Overall
n_correct = 0; n_total = 0
for r in records:
    if r["rt"] is None: continue
    n_total += 1
    rs = r["rt"] - r["da"]
    ps = r["pred"] - r["da"]
    if copysign(1, rs) == copysign(1, ps) or abs(rs) < 1e-10:
        n_correct += 1
print(f"  ├──────────┼──────────┼──────────┤")
print(f"  │ 合计     │ {n_total:>8} │ {n_correct/n_total*100:>8.2f}% │")
print("  └──────────┴──────────┴──────────┘")

# 8. Monthly arbitrage
print()
print("=" * 80)
print("  月度度电套利")
print("=" * 80)
print()
print("  ┌────────┬──────┬────────┬──────────┬───────────┐")
print("  │ 月份   │ 天数 │ sMAPE  │ 交易次数 │ 单位利润  │")
print("  ├────────┼──────┼────────┼──────────┼───────────┤")

monthly = defaultdict(list)
for r in records:
    monthly[r["date"][:7]].append(r)
for m in sorted(monthly.keys()):
    subset = monthly[m]
    sp = clipped_smape([r["pred"] for r in subset], [r["da"] for r in subset])
    prof, vol = arbitrage_basic(
        [r["pred"] for r in subset], [r["da"] for r in subset],
        [r["rt"] for r in subset],
    )
    unit = prof / vol if vol else 0
    print(f"  │ {m} │  {len(set(r['date'] for r in subset)):>2}d │ {sp:>6.2f}% │ {vol:>8} │ {unit:>8.2f} │")
print("  └────────┴──────┴────────┴──────────┴───────────┘")

conn.close()
