#!/usr/bin/env python
"""
Final comprehensive metrics report for EFM3.0 backtest.
Computes DA+RT metrics using final_selected predictions from the Ledger.
"""
from __future__ import annotations

import json, os, sys
from collections import defaultdict
from math import copysign

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.db.connection import DbConnectionManager

DB_URL = os.environ.get("EFM3_DB_URL", "mysql+pymysql://root:Zlt20060313%23@127.0.0.1:3306/efm3")
SMAFE_FLOOR = 50

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")

def _connect():
    return DbConnectionManager(db_url=DB_URL).new_connection()

def smape(preds, actuals):
    n = len(preds)
    if n == 0: return 0.0
    total = sum(abs(max(p,SMAFE_FLOOR)-max(a,SMAFE_FLOOR))/max((abs(max(p,SMAFE_FLOOR))+abs(max(a,SMAFE_FLOOR)))/2,1e-10) for p,a in zip(preds,actuals))
    return total/n*100.0

def mae_(p, a): return sum(abs(x-y) for x,y in zip(p,a))/len(p) if p else 0.0

def period(hb):
    if 1 <= hb <= 8: return "1_8(谷)"
    if 9 <= hb <= 16: return "9_16(平)"
    return "17_24(峰)"

print("=" * 90)
print("  EFM3.0 完整官方指标报表 (BGEW加权融合 + SGDFNet实时)")
print("=" * 90)

conn = _connect()
cur = conn.cursor()

# ── 1. DAY-AHEAD: task_final vs da_anchor ──────────────────────
cur.execute("""
    SELECT p.target_date, p.hour_business, p.pred_price, a.da_anchor
    FROM efm_predictions p
    JOIN efm_actual_prices a ON p.target_date=a.target_date AND p.hour_business=a.hour_business
    WHERE p.stage='dayahead_task_final' AND p.task='dayahead'
      AND p.target_date BETWEEN '2025-11-01' AND '2026-06-19'
      AND a.da_anchor IS NOT NULL
    ORDER BY p.target_date, p.hour_business
""")
rows = cur.fetchall()
da = [{"date":str(r[0]),"hb":int(r[1]),"pred":float(r[2]),"actual":float(r[3]),"period":period(int(r[1]))} for r in rows]

print(f"\n📊 日前 (DAYAHEAD): {len(rows)}h / {len(set(r['date'] for r in da))}d")

# Overall
da_p = [r["pred"] for r in da]; da_a = [r["actual"] for r in da]
da_s = smape(da_p, da_a); da_m = mae_(da_p, da_a)
print(f"  Overall: sMAPE={da_s:.2f}%  MAE={da_m:.2f}  Accuracy={100-da_s:.2f}%")

# Per-period
for pname in ["1_8(谷)", "9_16(平)", "17_24(峰)"]:
    sub = [r for r in da if r["period"]==pname]
    sp = smape([r["pred"] for r in sub],[r["actual"] for r in sub])
    print(f"  {pname}: n={len(sub):>5}h  sMAPE={sp:>6.2f}%")

# ── 2. REALTIME: realtime_task_final vs rt_actual ──────────────
cur.execute("""
    SELECT p.target_date, p.hour_business, p.pred_price,
           a.da_anchor, a.rt_actual
    FROM efm_predictions p
    JOIN efm_actual_prices a ON p.target_date=a.target_date AND p.hour_business=a.hour_business
    WHERE p.stage='realtime_task_final' AND p.task='realtime'
      AND p.target_date BETWEEN '2025-11-01' AND '2026-06-19'
      AND a.rt_actual IS NOT NULL
    ORDER BY p.target_date, p.hour_business
""")
rt_rows = cur.fetchall()
rt = [{"date":str(r[0]),"hb":int(r[1]),"pred":float(r[2]),"da_anchor":float(r[3]),"actual":float(r[4]),"period":period(int(r[1]))} for r in rt_rows]

print(f"\n📊 实时 (REALTIME): {len(rt_rows)}h / {len(set(r['date'] for r in rt))}d")

rt_p = [r["pred"] for r in rt]; rt_a = [r["actual"] for r in rt]
rt_s = smape(rt_p, rt_a); rt_m = mae_(rt_p, rt_a)
print(f"  Overall: sMAPE={rt_s:.2f}%  MAE={rt_m:.2f}  Accuracy={100-rt_s:.2f}%")

# DA_anchor baseline comparison
rt_da_a = [r["da_anchor"] for r in rt]
bench_s = smape(rt_da_a, rt_a)
print(f"  DA_anchor baseline: sMAPE={bench_s:.2f}%")

for pname in ["1_8(谷)", "9_16(平)", "17_24(峰)"]:
    sub = [r for r in rt if r["period"]==pname]
    sp = smape([r["pred"] for r in sub],[r["actual"] for r in sub])
    bp = smape([r["da_anchor"] for r in sub],[r["actual"] for r in sub])
    print(f"  {pname}: n={len(sub):>5}h  sMAPE={sp:>6.2f}%  (DA锚={bp:.2f}%)")

# ── 3. IMPROVED ARBITRAGE (P̂_rt > P̂_da > P_da) ──────────────
# Need DA_task_final + RT_task_final for the SAME date+hb
cur.execute("""
    SELECT p.target_date, p.hour_business, p.pred_price as pred_da,
           r.pred_price as pred_rt, a.da_anchor, a.rt_actual
    FROM efm_predictions p
    JOIN efm_predictions r ON p.target_date=r.target_date AND p.hour_business=r.hour_business
        AND r.stage='realtime_task_final' AND r.task='realtime'
    JOIN efm_actual_prices a ON p.target_date=a.target_date AND p.hour_business=a.hour_business
    WHERE p.stage='dayahead_task_final' AND p.task='dayahead'
      AND p.target_date BETWEEN '2025-11-01' AND '2026-06-19'
      AND a.rt_actual IS NOT NULL AND a.da_anchor IS NOT NULL
    ORDER BY p.target_date, p.hour_business
""")
joint = [(str(r[0]),int(r[1]),float(r[2]),float(r[3]),float(r[4]),float(r[5])) for r in cur.fetchall()]

def arb_basic(recs):
    p=v=0
    for r in recs:
        if r[2] > r[4]:  # pred_da > da_anchor
            p += r[5] - r[4]  # rt_actual - da_anchor
            v += 1
    return p, v

def arb_improved(recs):
    p=v=0
    for r in recs:
        if r[3] > r[2] and r[2] > r[4]:  # pred_rt > pred_da > da_anchor
            p += r[5] - r[4]
            v += 1
    return p, v

print(f"\n📊 度电套利 (n={len(joint)}h)")
pb, vb = arb_basic(joint); pi, vi = arb_improved(joint)
print(f"  基础版: 交易{vb}次  利润{pb:>8.2f}  单位利润={pb/vb if vb else 0:>6.2f}元/MWh")
print(f"  改良版: 交易{vi}次  利润{pi:>8.2f}  单位利润={pi/vi if vi else 0:>6.2f}元/MWh")

# Per-period arbitrage
print(f"\n  分时段套利:")
print(f"  {'时段':<10} {'基础交易':>8} {'基础利润':>10} {'改良交易':>8} {'改良利润':>10}")
for pname, (lo,hi) in [("1_8(谷)",(1,8)), ("9_16(平)",(9,16)), ("17_24(峰)",(17,24))]:
    sub = [r for r in joint if lo <= r[1] <= hi]
    pb_p, vb_p = arb_basic(sub); pi_p, vi_p = arb_improved(sub)
    print(f"  {pname:<10} {vb_p:>8} {pb_p:>10.2f} {vi_p:>8} {pi_p:>10.2f}")

# ── 4. SCR per period ─────────────────────────────────────────
def scr(recs):
    n_c=n_t=0
    for r in recs:
        n_t+=1
        rs=r[5]-r[4]; ps=r[2]-r[4]
        if copysign(1,rs)==copysign(1,ps) or abs(rs)<1e-10: n_c+=1
    return n_c/n_t*100 if n_t else 0

print(f"\n📊 价差方向准确率 (SCR): {scr(joint):.2f}%")

# ── 5. Monthly breakdown ──────────────────────────────────────
monthly = defaultdict(list)
for r in joint: monthly[r[0][:7]].append(r)
print(f"\n📊 月度完整对比:")
print(f"  {'月份':>8} {'DA-sMAPE':>9} {'RT-sMAPE':>9} {'基础套利':>9} {'改良套利':>9}")
for m in sorted(monthly.keys()):
    sub = monthly[m]
    da_pp = [r[2] for r in sub]; da_aa = [r[4] for r in sub]
    rt_pp = [r[3] for r in sub]; rt_aa = [r[5] for r in sub]
    ds = smape(da_pp, da_aa); rs = smape(rt_pp, rt_aa)
    pb_m, vb_m = arb_basic(sub); pi_m, vi_m = arb_improved(sub)
    pu = pb_m/vb_m if vb_m else 0; iu = pi_m/vi_m if vi_m else 0
    print(f"  {m:>8} {ds:>8.2f}% {rs:>8.2f}% {pu:>8.2f} {iu:>8.2f}")

conn.close()

# Save
os.makedirs(OUT, exist_ok=True)
report = {
    "dayahead": {"smape": round(da_s,2), "mae": round(da_m,2), "accuracy": round(100-da_s,2), "n_hours": len(da)},
    "realtime": {"smape": round(rt_s,2), "mae": round(rt_m,2), "accuracy": round(100-rt_s,2), "n_hours": len(rt),
                 "da_anchor_baseline_smape": round(bench_s,2)},
    "arbitrage": {"basic": {"trades": vb, "profit": round(pb,2), "unit_profit": round(pb/vb,2) if vb else None},
                  "improved": {"trades": vi, "profit": round(pi,2), "unit_profit": round(pi/vi,2) if vi else None}},
    "scr": round(scr(joint),2),
}
with open(os.path.join(OUT, "final_metrics_3.0.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)
print(f"\n[✅ 报表已保存至 outputs/final_metrics_3.0.json]")
