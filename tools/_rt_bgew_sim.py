#!/usr/bin/env python
"""Offline RT BGEW fusion simulator — sweep eta / warm-start init WITHOUT re-running
the pipeline. Loads raw-run history (for weight learning) and pc-run candidates
(fusion inputs) once, then fuses per date for each config and reports pooled sMAPE.
"""
from __future__ import annotations
import sys, os
import numpy as np
import pandas as pd
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.db.connection import get_db_url, DbConnectionManager

START, END = "2025-11-01", "2026-06-19"
PERIODS = ["1_8", "9_16", "17_24"]
FLOOR = 50.0


def period_of(hb):
    return "1_8" if hb <= 8 else ("9_16" if hb <= 16 else "17_24")


def smape_floor50(t, p):
    t = np.asarray(t, float); p = np.asarray(p, float)
    tc = np.maximum(t, FLOOR); pc = np.maximum(p, FLOOR)
    den = np.maximum((np.abs(tc) + np.abs(pc)) / 2.0, 1e-10)
    return float(np.mean(np.abs(pc - tc) / den) * 100.0)


def load():
    conn = DbConnectionManager(db_url=get_db_url()).new_connection()
    cur = conn.cursor()
    # actuals
    cur.execute("SELECT target_date,hour_business,rt_actual FROM efm_actual_prices "
                "WHERE target_date BETWEEN %s AND %s AND rt_actual IS NOT NULL", (START, END))
    act = {(str(d), int(h)): float(v) for d, h, v in cur.fetchall()}
    # raw-run history (BGEW training source): realtime_raw_model, NOT pc
    cur.execute("""SELECT r.target_date,p.hour_business,m.name,p.pred_price,a.rt_actual
        FROM efm_predictions p JOIN efm_runs r ON p.run_id=r.run_id
        JOIN efm_dim_model m ON p.model_id=m.id JOIN efm_dim_stage s ON p.stage_id=s.id
        JOIN efm_actual_prices a ON r.target_date=a.target_date AND p.hour_business=a.hour_business
        WHERE p.task='realtime' AND s.name='realtime_raw_model' AND p.run_id NOT LIKE 'efm3_pc_%%'
          AND a.rt_actual IS NOT NULL AND r.target_date BETWEEN %s AND %s""", (START, END))
    hist = pd.DataFrame(cur.fetchall(), columns=["date", "hb", "model", "pred", "yt"])
    hist["date"] = pd.to_datetime(hist["date"]); hist["hb"] = hist["hb"].astype(int)
    hist["pred"] = hist["pred"].astype(float); hist["yt"] = hist["yt"].astype(float)
    hist["period"] = hist["hb"].apply(period_of)
    # pc-run candidates (fusion inputs): module_repaired stage
    cur.execute("""SELECT r.target_date,p.hour_business,m.name,p.pred_price
        FROM efm_predictions p JOIN efm_runs r ON p.run_id=r.run_id
        JOIN efm_dim_model m ON p.model_id=m.id JOIN efm_dim_stage s ON p.stage_id=s.id
        WHERE p.task='realtime' AND s.name='realtime_module_repaired' AND p.run_id LIKE 'efm3_pc_%%'
          AND r.target_date BETWEEN %s AND %s""", (START, END))
    cand = defaultdict(dict)  # (date,hb) -> {model:val}
    for d, h, m, v in cur.fetchall():
        cand[(str(d), int(h))][m] = float(v)
    conn.close()
    return act, hist, cand


def bgew_weights_asof(hist, as_of, models, eta=0.8, wfloor=0.03, lookback=60, warm=False):
    """Return {period: {model: w}} using days strictly before as_of."""
    sub = hist[hist["date"] < pd.Timestamp(as_of)]
    out = {}
    for period in PERIODS:
        pdf = sub[sub["period"] == period]
        days = sorted(pdf["date"].unique())
        if len(days) > lookback:
            days = days[-lookback:]
        if warm and days:
            # warm-start init from inverse mean-loss over the window
            ml = {}
            for m in models:
                mdf = pdf[(pdf["model"] == m) & (pdf["date"].isin(days))]
                if len(mdf):
                    ml[m] = smape_floor50(mdf["yt"], mdf["pred"])
            if ml:
                inv = {m: 1.0 / max(ml.get(m, np.mean(list(ml.values()))), 1e-6) for m in models}
                s = sum(inv.values()); w = {m: inv[m] / s for m in models}
            else:
                w = {m: 1.0 / len(models) for m in models}
        else:
            w = {m: 1.0 / len(models) for m in models}
        for di, day in enumerate(days):
            ddf = pdf[pdf["date"] == day]
            losses = {}
            for m in models:
                mdf = ddf[ddf["model"] == m]
                if len(mdf):
                    losses[m] = smape_floor50(mdf["yt"], mdf["pred"])
            if not losses:
                continue
            med = np.median(list(losses.values())) or 1.0
            gate = 0.7 if (len(days) - di) <= 15 else 0.3
            for m in models:
                if m in losses:
                    w[m] *= np.exp(-eta * gate * losses[m] / med)
                    w[m] = max(w[m], wfloor)
            tot = sum(w.values())
            if tot > 0:
                w = {m: w[m] / tot for m in w}
        out[period] = w
    return out


SELECTOR = "da_aware_sgdf_selector"


def _apply_selector_prior(w, prior):
    """Blend BGEW weights toward the DA-anchored selector: keeps adaptivity but
    guarantees the structurally-best candidate a floor. prior in [0,1]."""
    if prior <= 0 or SELECTOR not in w:
        return w
    blended = {m: (1 - prior) * w.get(m, 0) for m in w}
    blended[SELECTOR] = blended.get(SELECTOR, 0) + prior
    s = sum(blended.values())
    return {m: blended[m] / s for m in blended} if s > 0 else w


def simulate(act, hist, cand, eta=0.8, wfloor=0.03, warm=False, sel_prior=0.0):
    models = ["da_aware_sgdf_selector", "sgdfnet", "timesfm"]
    dates = sorted({d for d, _ in cand.keys()})
    T, P = [], []
    for d in dates:
        wt = bgew_weights_asof(hist, d, models, eta=eta, wfloor=wfloor, warm=warm)
        for hb in range(1, 25):
            key = (d, hb)
            if key not in cand or key not in act:
                continue
            cv = cand[key]
            per = period_of(hb)
            w = _apply_selector_prior(wt[per], sel_prior)
            num = sum(w.get(m, 0) * cv[m] for m in models if m in cv)
            den = sum(w.get(m, 0) for m in models if m in cv)
            if den <= 0:
                continue
            T.append(act[key]); P.append(num / den)
    return smape_floor50(T, P), len(T)


def sweep_fast(act, hist, cand, priors, eta=0.8, wfloor=0.03, warm=False):
    """Compute BGEW weights ONCE per date, then apply every prior in one pass.
    ~len(priors)x faster than calling simulate() per config."""
    models = ["da_aware_sgdf_selector", "sgdfnet", "timesfm"]
    dates = sorted({d for d, _ in cand.keys()})
    # per-prior accumulators
    T = {p: [] for p in priors}
    P = {p: [] for p in priors}
    for di, d in enumerate(dates):
        wt = bgew_weights_asof(hist, d, models, eta=eta, wfloor=wfloor, warm=warm)
        for hb in range(1, 25):
            key = (d, hb)
            if key not in cand or key not in act:
                continue
            cv = cand[key]
            per = period_of(hb)
            base_w = wt[per]
            for prior in priors:
                w = _apply_selector_prior(base_w, prior)
                num = sum(w.get(m, 0) * cv[m] for m in models if m in cv)
                den = sum(w.get(m, 0) for m in models if m in cv)
                if den <= 0:
                    continue
                T[prior].append(act[key]); P[prior].append(num / den)
        if (di + 1) % 30 == 0:
            print(f"  ...processed {di+1}/{len(dates)} dates", flush=True)
    return {p: (smape_floor50(T[p], P[p]), len(T[p])) for p in priors}


if __name__ == "__main__":
    print("loading ...", flush=True)
    act, hist, cand = load()
    print(f"actuals={len(act)} hist_rows={len(hist)} cand_keys={len(cand)}", flush=True)
    priors = [0.0, 0.20, 0.30, 0.40, 0.50, 0.60, 1.00]
    print(f"sweeping selector_prior={priors} (single-pass BGEW) ...", flush=True)
    res = sweep_fast(act, hist, cand, priors, eta=0.8)
    print("\n=== RT selector_prior sweep (pooled sMAPE_floor50) ===", flush=True)
    for p in priors:
        s, n = res[p]
        flag = "PASS" if s <= 25.0 else "fail"
        label = "no prior (pure BGEW)" if p == 0.0 else ("selector-only ceiling" if p == 1.0 else f"prior={p:.2f}")
        print(f"  {label:26s} RT pooled sMAPE = {s:.2f}%  [{flag}]  (n={n})", flush=True)
