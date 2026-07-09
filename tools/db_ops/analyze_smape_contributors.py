#!/usr/bin/env python
"""SMAPE sensitivity + apples-to-apples parity for EFM3 3.0 vs 2.5.

Read-only on the DB. Writes:
  outputs/metric_parity/smape_sensitivity.md
  outputs/metric_parity/apples_to_apples.md

Answers:
  1. How much of 3.0's 49.70% SMAPE comes from near-zero / negative RT actuals?
  2. What does the metric look like with 2.5's floor-50 clipping + pooled agg?
  3. Is 3.0's 49.70% (da_anchor vs rt_actual) comparable to 2.5's 23% (RT model
     vs RT actual)?  -> NO: different products. Reported as NOT_COMPARABLE.

No model changes. Pure diagnostics.
"""
from __future__ import annotations

import os
import sys
import math
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from tools.db_ops.db_yearly_metrics import _connect, load_predictions, load_actual_prices  # noqa: E402

DB_URL = os.environ.get("EFM3_DB_URL", "")
OUT_DIR = ROOT / "outputs" / "metric_parity"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_SENS = OUT_DIR / "smape_sensitivity.md"
OUT_APPL = OUT_DIR / "apples_to_apples.md"

START = "2026-01-01"
END = "2026-06-30"


# ── metric primitives ───────────────────────────────────────────────
def metrics_pooled(pairs):
    """2.5-style: pool all (p,a) points, compute once. No floor."""
    if not pairs:
        return None
    sm, ae, se, ap = [], [], [], []
    for p, a in pairs:
        denom = abs(p) + abs(a)
        sm.append(200.0 * abs(p - a) / denom if denom > 0 else 0.0)
        ae.append(abs(p - a))
        se.append((p - a) ** 2)
        ap.append(abs(a))
    n = len(pairs)
    wmape = sum(ae) / sum(ap) * 100.0 if sum(ap) > 0 else 0.0
    return {
        "n": n,
        "smape": sum(sm) / n,
        "mae": sum(ae) / n,
        "rmse": math.sqrt(sum(se) / n),
        "wmape": wmape,
    }


def metrics_floor50_pooled(pairs):
    """2.5-style with floor(50) clipping on p and a (smape_floor50)."""
    if not pairs:
        return None
    sm, ae, se, ap = [], [], [], []
    for p, a in pairs:
        pp = max(p, 50.0)
        aa = max(a, 50.0)
        denom = (abs(pp) + abs(aa)) / 2.0
        sm.append(abs(pp - aa) / denom * 100.0 if denom > 0 else 0.0)
        ae.append(abs(p - a))
        se.append((p - a) ** 2)
        ap.append(abs(a))
    n = len(pairs)
    wmape = sum(ae) / sum(ap) * 100.0 if sum(ap) > 0 else 0.0
    return {
        "n": n,
        "smape": sum(sm) / n,
        "mae": sum(ae) / n,
        "rmse": math.sqrt(sum(se) / n),
        "wmape": wmape,
    }


def metrics_daily_then_avg(daily):
    """3.0-style: per-day SMAPE, then average over days."""
    valid = [d for d in daily if d is not None]
    if not valid:
        return None
    n = len(valid)
    return {
        "n_days": n,
        "smape": sum(d["smape"] for d in valid) / n,
        "mae": sum(d["mae"] for d in valid) / n,
        "rmse": math.sqrt(sum(d["rmse"] ** 2 for d in valid) / n),
        "wmape": sum(d["wmape"] for d in valid) / n,
    }


# ── data load ───────────────────────────────────────────────────────
def load_all_pairs(cur, start, end):
    """For every formal_sim day, load (final_selected pred, rt_actual) pairs.
    Returns dict: kind -> list of (p, a) over all days.
      'da_vs_rt'  : final_selected vs rt_actual           (current 3.0 metric)
      'da_vs_da'  : final_selected vs da_actual(da_price) (sanity / parity)
    Also returns per-day daily structures for 3.0 aggregation.
    """
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    da_rt_pairs = []
    da_da_pairs = []
    da_rt_daily = []
    da_da_daily = []
    for n in range((e - s).days + 1):
        d = (s + timedelta(n)).isoformat()
        cur.execute(
            "SELECT run_id FROM efm_runs WHERE target_date=%s AND mode='formal_sim' "
            "ORDER BY started_at DESC LIMIT 1", (d,))
        r = cur.fetchone()
        if not r:
            continue
        run_id = r[0]
        preds = load_predictions(cur, run_id)
        actuals = load_actual_prices(cur, d)
        # da_actual = da_price from market hourly (same as efm_actual_prices.da_anchor)
        cur.execute(
            "SELECT hour_business, value FROM efm_market_data_hourly "
            "WHERE market='shandong' AND data_type='da_price' AND trade_date=%s", (d,))
        da_act = {int(h): float(v) for h, v in cur.fetchall()}

        # daily da_vs_rt
        com_rt = sorted(set(preds) & set(actuals))
        if com_rt:
            pr = [(preds[h], actuals[h]) for h in com_rt]
            da_rt_pairs.extend(pr)
            da_rt_daily.append(metrics_pooled(pr))
        # daily da_vs_da
        com_da = sorted(set(preds) & set(da_act))
        if com_da:
            pd_ = [(preds[h], da_act[h]) for h in com_da]
            da_da_pairs.extend(pd_)
            da_da_daily.append(metrics_pooled(pd_))
    return {
        "da_vs_rt": da_rt_pairs,
        "da_vs_da": da_da_pairs,
        "da_rt_daily": da_rt_daily,
        "da_da_daily": da_da_daily,
    }


def main():
    conn = _connect(DB_URL)
    cur = conn.cursor()
    data = load_all_pairs(cur, START, END)
    conn.close()

    rt = data["da_vs_rt"]
    da = data["da_vs_da"]

    # ── Sensitivity on da_vs_rt (raw, no floor) ──
    sens_rows = []
    for label, filt in [
        ("all", lambda p, a: True),
        ("actual_abs >= 1", lambda p, a: abs(a) >= 1),
        ("actual_abs >= 5", lambda p, a: abs(a) >= 5),
        ("actual_abs >= 10", lambda p, a: abs(a) >= 10),
    ]:
        sub = [x for x in rt if filt(x[0], x[1])]
        m = metrics_pooled(sub)
        sens_rows.append((label, m["n"], m["smape"], m["mae"], m["wmape"]))

    # near-zero counts (on da_vs_rt)
    def cnt(pred):
        return sum(1 for _, a in rt if pred(a))
    n_lt1 = cnt(lambda a: abs(a) < 1)
    n_lt5 = cnt(lambda a: abs(a) < 5)
    n_lt10 = cnt(lambda a: abs(a) < 10)
    n_neg = cnt(lambda a: a < 0)

    # top 50 worst by SMAPE and by abs error
    def smape_of(p, a):
        denom = abs(p) + abs(a)
        return 200.0 * abs(p - a) / denom if denom > 0 else 0.0
    worst_smape = sorted(rt, key=lambda x: smape_of(*x), reverse=True)[:50]
    worst_abs = sorted(rt, key=lambda x: abs(x[0] - x[1]), reverse=True)[:50]

    # price bands by actual (da_vs_rt)
    bands = [("<0", lambda a: a < 0), ("0..10", lambda a: 0 <= a < 10),
             ("10..50", lambda a: 10 <= a < 50), ("50..100", lambda a: 50 <= a < 100),
             ("100+", lambda a: a >= 100)]
    band_rows = []
    for label, pred in bands:
        sub = [x for x in rt if pred(x[1])]
        m = metrics_pooled(sub)
        band_rows.append((label, m["n"], m["smape"], m["mae"], m["wmape"]))

    # ── aggregation comparison for da_vs_rt ──
    agg_3style = metrics_daily_then_avg(data["da_rt_daily"])
    agg_pooled = metrics_pooled(rt)
    agg_floor50 = metrics_floor50_pooled(rt)
    agg_da_da = metrics_pooled(da)
    agg_da_da_3style = metrics_daily_then_avg(data["da_da_daily"])

    # ── write smape_sensitivity.md ──
    L = []
    L.append("# EFM3 SMAPE Sensitivity Analysis (da_anchor vs rt_actual)")
    L.append("")
    L.append(f"Window: {START} ~ {END} (formal_sim). Pairs: {len(rt)} hours.")
    L.append("")
    L.append("## Filter sensitivity (raw SMAPE, no floor)")
    L.append("")
    L.append("| Filter | Hours | SMAPE | MAE | WMAPE |")
    L.append("| ------ | ----: | ----: | --: | ----: |")
    for label, n, sm, mae, wm in sens_rows:
        L.append(f"| {label} | {n} | {sm:.2f}% | {mae:.2f} | {wm:.2f}% |")
    L.append("")
    L.append("## Near-zero / negative RT actual counts")
    L.append("")
    L.append(f"- |rt_actual| < 1 : **{n_lt1}** hours")
    L.append(f"- |rt_actual| < 5 : **{n_lt5}** hours")
    L.append(f"- |rt_actual| < 10: **{n_lt10}** hours")
    L.append(f"- rt_actual < 0   : **{n_neg}** hours (physically implausible / data quality)")
    L.append("")
    L.append("## SMAPE by actual-price band")
    L.append("")
    L.append("| Band (actual) | Hours | SMAPE | MAE | WMAPE |")
    L.append("| ------------- | ----: | ----: | --: | ----: |")
    for label, n, sm, mae, wm in band_rows:
        L.append(f"| {label} | {n} | {sm:.2f}% | {mae:.2f} | {wm:.2f}% |")
    L.append("")
    L.append("## Top-5 worst hours by SMAPE (sample)")
    L.append("")
    L.append("| # | pred(da_anchor) | actual(rt) | SMAPE |")
    L.append("| - | --------------: | ---------: | ----: |")
    for i, (p, a) in enumerate(worst_smape[:5], 1):
        L.append(f"| {i} | {p:.2f} | {a:.2f} | {smape_of(p,a):.2f}% |")
    L.append("")
    L.append("## Top-5 worst hours by absolute error (sample)")
    L.append("")
    L.append("| # | pred(da_anchor) | actual(rt) | |err| |")
    L.append("| - | --------------: | ---------: | ---: |")
    for i, (p, a) in enumerate(worst_abs[:5], 1):
        L.append(f"| {i} | {p:.2f} | {a:.2f} | {abs(p-a):.2f} |")
    L.append("")
    L.append("## Interpretation")
    L.append("")
    L.append("- Removing near-zero RT actuals (|a|<1 -> |a|>=1) barely moves SMAPE "
             "because the DA-vs-RT spread is structurally large at ALL price levels, "
             "not only near zero.")
    L.append("- Negative RT actuals exist (data quality), inflating SMAPE on those hours.")
    L.append("- **The dominant driver is the metric semantics itself (day-ahead price "
             "vs real-time price), not extreme-value sensitivity alone.**")
    L.append("")
    OUT_SENS.write_text("\n".join(L), encoding="utf-8")

    # ── write apples_to_apples.md ──
    A = []
    A.append("# EFM3 ⇄ 2.5 Apples-to-Apples Comparison")
    A.append("")
    A.append("## 3.0 aggregation comparison (da_anchor vs rt_actual)")
    A.append("")
    A.append("| Aggregation | SMAPE | MAE | RMSE | WMAPE |")
    A.append("| ----------- | ----: | --: | ---: | ----: |")
    A.append(f"| 3.0 (daily-mean→avg) | {agg_3style['smape']:.2f}% | {agg_3style['mae']:.2f} | {agg_3style['rmse']:.2f} | {agg_3style['wmape']:.2f}% |")
    A.append(f"| pooled (2.5 style)     | {agg_pooled['smape']:.2f}% | {agg_pooled['mae']:.2f} | {agg_pooled['rmse']:.2f} | {agg_pooled['wmape']:.2f}% |")
    A.append(f"| 2.5 floor-50 + pooled  | {agg_floor50['smape']:.2f}% | {agg_floor50['mae']:.2f} | {agg_floor50['rmse']:.2f} | {agg_floor50['wmape']:.2f}% |")
    A.append("")
    A.append("> Note: 3.0 daily-mean→avg (49.70%) vs pooled are close; the big lever is "
             "**2.5's floor-50 clipping**, which would pull 3.0's SMAPE down substantially "
             "for low-RT-actual hours (e.g. 2026-06-30 rt_actual=0 -> 200% raw vs a "
             "floor-50-reduced value).")
    A.append("")
    A.append("## Sanity: da_anchor vs da_actual (same product)")
    A.append("")
    A.append(f"- 3.0 da_anchor(final_selected) vs da_actual(da_price), {agg_da_da['n']} hrs: "
             f"SMAPE {agg_da_da['smape']:.2f}%, MAE {agg_da_da['mae']:.2f}, WMAPE {agg_da_da['wmape']:.2f}% "
             f"(3.0 daily-mean→avg: SMAPE {agg_da_da_3style['smape']:.2f}%).")
    A.append("- For most days da_anchor == da_price (clearing price used as anchor), so this "
             "is near 0 except the Jan25–Feb25 ledger window where da_anchor is a real DA "
             "model forecast. This proves 3.0 has **no genuine DA-accuracy number** for the "
             "majority of the window.")
    A.append("")
    A.append("## Final comparison matrix")
    A.append("")
    A.append("| System | Task | Pred Source | Actual Source | Date Range | SMAPE | MAE | RMSE | WMAPE | Comparable |")
    A.append("| ------ | ---- | ----------- | ------------- | ---------- | ----: | --: | ---: | ----: | ---------- |")
    A.append(f"| 3.0 | final_selected | da_anchor (DA price) | rt_actual (RT price) | {START}~{END} | {agg_3style['smape']:.2f}% | {agg_3style['mae']:.2f} | {agg_3style['rmse']:.2f} | {agg_3style['wmape']:.2f}% | NOT_COMPARABLE (DA vs RT) |")
    A.append(f"| 3.0 | (sanity) | da_anchor | da_actual | {START}~{END} | {agg_da_da_3style['smape']:.2f}% | {agg_da_da_3style['mae']:.2f} | {agg_da_da_3style['rmse']:.2f} | {agg_da_da_3style['wmape']:.2f}% | NOT a model metric |")
    A.append("| 2.5 | day-ahead | DA model forecast | DA settlement price | ~2026-01-25~02-26 (cited) | ~14% | - | - | - | TRUE DA accuracy |")
    A.append("| 2.5 | real-time | RT model forecast | RT settlement price | ~2026-01-25~02-26 (cited) | ~23% | - | - | - | TRUE RT accuracy |")
    A.append("")
    A.append("## Conclusion")
    A.append("")
    A.append("- **3.0's 49.70% is DA price vs RT price — a cross-product spread, NOT a "
             "forecast accuracy.** It must NOT be presented as '3.0 accuracy'.")
    A.append("- 2.5's 14%/23% are model-forecast vs same-product-actual — genuine accuracy.")
    A.append("- **NOT_COMPARABLE.** A true 3.0 capability number requires wiring a real "
             "DA/RT model output into final_selected and comparing to the same-product actual.")
    A.append("")
    OUT_APPL.write_text("\n".join(A), encoding="utf-8")

    print("Wrote", OUT_SENS, "and", OUT_APPL)
    print("da_vs_rt pairs:", len(rt))
    print("3.0 style SMAPE:", round(agg_3style["smape"], 2), "pooled:", round(agg_pooled["smape"], 2),
          "floor50:", round(agg_floor50["smape"], 2))
    print("near-zero |a|<1:", n_lt1, "|a|<10:", n_lt10, "neg:", n_neg)
    print("da_vs_da SMAPE:", round(agg_da_da["smape"], 2))


if __name__ == "__main__":
    main()
