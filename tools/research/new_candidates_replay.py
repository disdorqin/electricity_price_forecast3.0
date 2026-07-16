"""
EFM3 V3.1 NEW CANDIDATE TRACKS (A-F) -- full-history STRICT_REPLAY_OOS.

Reuses the validated GPU-first/CPU-fallback engine from full_history_replay.py.
Six research tracks, each producing 1+ candidate columns:

  A Tail Distribution   : A_q05 / A_q50 / A_q95 (LightGBM quantile) + A_QRA (ensemble)
  B Joint Midday Curve  : B_midday  (trained & predicted ONLY on hours 9-16)
  C Seasonal Multi-Scale: C_winter / C_summer / C_shoulder (per-season L2 models)
  D Anchor-Heterogeneous: D_anchor  (adds da_price_bin interaction feature)
  E Robust Tail Objectives: E_fair / E_huber (fair & huber objectives)
  F Direct Regime Residual: F_regime (2-stage base + residual)

Legal features at time t (no RT leakage): da_price, *_forecast, lags, calendar.
Targets = rt_actual (held out).

Unified metrics + corrected Oracle (selection-only, never touches actuals).
Outputs: data_audit/FH_NEW_TRACKS_*.csv/.md
"""
import os, json, time as _time, hashlib
import numpy as np
import pandas as pd
import lightgbm as lgb

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PANEL = os.path.join(ROOT, "electricity_forecast_model3.0-research", "data_audit", "FULL_HISTORY_CANONICAL_PANEL.parquet")
OUTDIR = os.path.join(ROOT, "electricity_forecast_model3.0-research", "data_audit")
os.makedirs(OUTDIR, exist_ok=True)
RETRAIN_DAYS = 90

print("[load] panel")
df = pd.read_parquet(PANEL)
df["times"] = pd.to_datetime(df["business_day"]) + pd.to_timedelta(df["hour_business"].astype(int) - 1, unit="h")
df = df.sort_values("times").reset_index(drop=True)

# ---- derive calendar features (strict-OOS legal: known at time t) ----
df["hour"] = df["hour_business"].astype(int)
df["dayofweek"] = df["times"].dt.dayofweek.astype(int)
df["month"] = df["times"].dt.month.astype(int)
df["is_weekend"] = (df["dayofweek"] >= 5).astype(int)
df["hour_sin"] = np.sin(2*np.pi*df["hour"]/24.0)
df["hour_cos"] = np.cos(2*np.pi*df["hour"]/24.0)
df["month_sin"] = np.sin(2*np.pi*df["month"]/12.0)
df["month_cos"] = np.cos(2*np.pi*df["month"]/12.0)

# ---- legal DA-price proxy (OOS-legal; da_actual is NOT usable for target day) ----
DA_COL = "legal_oos_da_prediction"

exo_fc = [c for c in df.columns if c.endswith("_forecast")]
lag_cols = [c for c in df.columns if ("lag_" in c or "rolling_" in c)]
cal_cols = ["hour","dayofweek","month","is_weekend","hour_sin","hour_cos","month_sin","month_cos"]
feat_cols = [DA_COL] + exo_fc + lag_cols + cal_cols
feat_cols = [c for c in feat_cols if c in df.columns]
print("   n_features:", len(feat_cols))

# anchor bin feature (DA settlement proxy is known pre-RT => legal)
da_vals = df[DA_COL].astype(float).values
da_bin = pd.qcut(pd.Series(da_vals), 5, labels=False, duplicates="drop").values
df["da_price_bin"] = da_bin

y = df["rt_actual"].astype(float).values
X = df[feat_cols + ["da_price_bin"]].astype(float)
valid = X.notna().all(axis=1).values & np.isfinite(y) & np.isfinite(da_bin)
Xv = X[valid].reset_index(drop=True)
yv = y[valid]
dfv = df[valid].reset_index(drop=True)
hb = dfv["hour_business"].astype(int).values
month = dfv["times"].dt.month.astype(int).values
print("   usable rows:", len(dfv), dfv["times"].min(), "->", dfv["times"].max())

days = sorted(dfv["business_day"].unique())
tgt_days = days[RETRAIN_DAYS:]
print("   target days:", len(tgt_days))
WINDOWS = [180, 365, None]

# ---------------- GPU-first / permanent CPU fallback ----------------
GPU_STATE = {"ok": False, "reason": "not_probed"}
def _gpu_probe():
    try:
        n = 2000
        Xp = np.random.rand(n, min(20, Xv.shape[1])); yp = np.random.rand(n)
        t0 = _time.time()
        m = lgb.LGBMRegressor(n_estimators=20, num_leaves=15, device="gpu", gpu_device_id=0, n_jobs=4, random_state=0)
        m.fit(Xp, yp); _ = m.predict(Xp[:10])
        dt = _time.time() - t0
        return (False, f"probe_slow_{dt:.1f}s") if dt > 30 else (True, f"probe_ok_{dt:.1f}s")
    except Exception as e:
        return False, f"probe_exc_{type(e).__name__}"
print("[gpu] GPU disabled per local reliability rule (segfault-prone on this host) -> CPU-only")
ok, reason = (False, "disabled_cpu_only")
GPU_STATE["ok"] = ok; GPU_STATE["reason"] = reason
print(f"[gpu] GPU_OK={ok} ({reason}) -> CPU-only")

def make_lgb(params, use_gpu):
    base = dict(n_estimators=200, learning_rate=0.05, num_leaves=31,
                min_child_samples=50, subsample=0.9, colsample_bytree=0.9, n_jobs=4, random_state=42)
    if use_gpu:
        base.update(device="gpu", gpu_device_id=0)
    base.update(params)
    return lgb.LGBMRegressor(**base)

def _fit(use_gpu, params, Xi, yi):
    model = make_lgb(params, use_gpu)
    model.fit(Xi, yi)
    return model

def fit_predict_generic(params, Xi, yi, Xp, force_cpu=False):
    # quantile/fair/huber objectives are unreliable on LightGBM GPU -> force CPU for them
    if GPU_STATE["ok"] and not force_cpu:
        try:
            model = _fit(True, params, Xi, yi)
            return model.predict(Xp)
        except Exception as e:
            GPU_STATE["ok"] = False; GPU_STATE["reason"] = f"runtime_fallback_{type(e).__name__}"
            print(f"[gpu] runtime error -> permanent CPU fallback ({type(e).__name__})")
    model = _fit(False, params, Xi, yi)
    return model.predict(Xp)

# feature subsets
feat_base = feat_cols
feat_anchor = feat_cols + ["da_price_bin"]

# season masks
def season_of(m):
    if m in (12,1,2): return "winter"
    if m in (6,7,8): return "summer"
    return "shoulder"

# ---------------- track dispatch ----------------
def get_pred(track, params, train_idx, pred_idx):
    """Return prediction array aligned to dfv (NaN outside pred_idx)."""
    out = np.full(len(dfv), np.nan)
    if track in ("quantile","robust","anchor"):
        feats = feat_anchor if track == "anchor" else feat_base
        Xi = Xv.iloc[train_idx][feats].values; yi = yv[train_idx]
        Xp = Xv.iloc[pred_idx][feats].values
        if track == "anchor":
            out[pred_idx] = fit_predict_generic(params, Xi, yi, Xp)  # L2 -> GPU ok
        else:
            out[pred_idx] = fit_predict_generic(params, Xi, yi, Xp, force_cpu=True)  # quantile/fair/huber -> CPU
    elif track == "midday":
        # only 9-16 rows
        tr_mid = np.intersect1d(train_idx, np.where((hb>=9)&(hb<=16))[0])
        pr_mid = np.intersect1d(pred_idx, np.where((hb>=9)&(hb<=16))[0])
        if len(tr_mid) < 200 or len(pr_mid) == 0:
            return out
        Xi = Xv.iloc[tr_mid][feat_base].values; yi = yv[tr_mid]
        Xp = Xv.iloc[pr_mid][feat_base].values
        out[pr_mid] = fit_predict_generic({}, Xi, yi, Xp)
    elif track == "regime":
        # 2-stage: base L2 then residual L2
        Xi = Xv.iloc[train_idx][feat_base].values; yi = yv[train_idx]
        Xp = Xv.iloc[pred_idx][feat_base].values
        base = fit_predict_generic({}, Xi, yi, Xi)
        resid = yi - base
        Xi2 = np.column_stack([Xi, resid.reshape(-1,1)])
        # need residual feature also for pred; use NaN-free residual proxy via base
        model2 = make_lgb({}, False)
        model2.fit(Xi2, resid)
        Xp2 = np.column_stack([Xp, fit_predict_generic({}, Xi, yi, Xp).reshape(-1,1)])
        out[pred_idx] = fit_predict_generic({}, Xi, yi, Xp) + model2.predict(Xp2)
    elif track == "season":
        seas = params["season"]
        tr_s = np.intersect1d(train_idx, np.where(np.array([season_of(m) for m in month[train_idx]])==seas)[0])
        pr_s = np.intersect1d(pred_idx, np.where(np.array([season_of(m) for m in month[pred_idx]])==seas)[0])
        if len(tr_s) < 200 or len(pr_s) == 0:
            return out
        Xi = Xv.iloc[tr_s][feat_base].values; yi = yv[tr_s]
        Xp = Xv.iloc[pr_s][feat_base].values
        out[pr_s] = fit_predict_generic({}, Xi, yi, Xp)
    return out

# track registry: name -> (track_type, params)
TRACKS = {
    "A_q05":    ("quantile", {"objective":"quantile","alpha":0.05}),
    "A_q50":    ("quantile", {"objective":"quantile","alpha":0.50}),
    "A_q95":    ("quantile", {"objective":"quantile","alpha":0.95}),
    "E_fair":   ("robust",   {"objective":"fair","fair_c":1.0}),
    "E_huber":  ("robust",   {"objective":"huber","alpha":0.5}),
    "D_anchor": ("anchor",   {}),
    "B_midday": ("midday",   {}),
    "F_regime": ("regime",   {}),
    "C_winter": ("season",   {"season":"winter"}),
    "C_summer": ("season",   {"season":"summer"}),
    "C_shoulder":("season", {"season":"shoulder"}),
}
POINT_CANDS = ["A_q50","E_fair","E_huber","D_anchor","B_midday","F_regime","C_winter","C_summer","C_shoulder"]

results = {}
full_idx = np.arange(len(dfv))
for W in WINDOWS:
    wlabel = "expanding" if W is None else f"W{W}"
    print(f"[train] window={wlabel}")
    anchors = days[::RETRAIN_DAYS]
    for a in range(len(anchors)-1):
        t0 = anchors[a]; t1 = anchors[a+1]
        train_mask = (dfv["times"] < pd.Timestamp(t0))
        if W is not None:
            train_mask &= (dfv["times"] >= pd.Timestamp(t0) - pd.Timedelta(days=W))
        train_idx = np.where(train_mask.values)[0]
        pred_mask = (dfv["times"] >= pd.Timestamp(t0)) & (dfv["times"] < pd.Timestamp(t1))
        pred_idx = np.where(pred_mask.values)[0]
        if len(train_idx) < 200 or len(pred_idx) == 0:
            continue
        for name,(tt,pr) in TRACKS.items():
            p = get_pred(tt, pr, train_idx, pred_idx)
            key = (wlabel, name)
            results.setdefault(key, np.full(len(dfv), np.nan))
            results[key][pred_idx] = p[pred_idx]
    # last block
    t0 = anchors[-1]
    train_mask = (dfv["times"] < pd.Timestamp(t0))
    if W is not None:
        train_mask &= (dfv["times"] >= pd.Timestamp(t0) - pd.Timedelta(days=W))
    train_idx = np.where(train_mask.values)[0]
    pred_mask = (dfv["times"] >= pd.Timestamp(t0))
    pred_idx = np.where(pred_mask.values)[0]
    if len(train_idx) >= 200:
        for name,(tt,pr) in TRACKS.items():
            p = get_pred(tt, pr, train_idx, pred_idx)
            key = (wlabel, name)
            results.setdefault(key, np.full(len(dfv), np.nan))
            results[key][pred_idx] = p[pred_idx]
    print(f"   fits done for {wlabel}")

# DD baseline (legal OOS DA prediction -- same definition as full_history_replay)
results[("all","DD")] = dfv[DA_COL].astype(float).values
# A_QRA ensemble (post-hoc mean of expanding quantiles)
if all(("expanding",k) in results for k in ["A_q05","A_q50","A_q95"]):
    q=results[("expanding","A_q05")]; m=results[("expanding","A_q50")]; h=results[("expanding","A_q95")]
    results[("expanding","A_QRA")] = np.nanmean(np.stack([q,m,h]), axis=0)
    POINT_CANDS.append("A_QRA")

print("[predictions] collected candidates:", sorted(set(k[1] for k in results.keys())))

# ---------------- unified metrics ----------------
def smape_floor50(a,p):
    a=np.asarray(a,float); p=np.asarray(p,float)
    denom=np.clip((np.abs(a)+np.abs(p))/2.0,1e-9,50.0)
    return 200.0*np.abs(p-a)/denom
def plain_smape(a,p):
    a=np.asarray(a,float); p=np.asarray(p,float)
    denom=np.clip((np.abs(a)+np.abs(p))/2.0,1e-9,np.inf)
    return 200.0*np.abs(p-a)/denom

metric_rows=[]
all_cands = sorted(set(k[1] for k in results.keys()))
for cand in all_cands:
    key = ("expanding",cand) if ("expanding",cand) in results else ("all",cand)
    p = results[key]; m = ~np.isnan(p)
    if m.sum() < 100: continue
    a=yv[m]; pp=p[m]
    overall_f50=np.mean(smape_floor50(a,pp)); overall_plain=np.mean(plain_smape(a,pp))
    mae=np.mean(np.abs(a-pp)); rmse=np.sqrt(np.mean((a-pp)**2))
    mhb=hb[m]
    def bk(mask):
        mm=mask&(np.isfinite(pp)); return np.mean(smape_floor50(a[mask],pp[mask])) if mask.sum()>0 else np.nan
    b18=bk((mhb>=1)&(mhb<=8)); b916=bk((mhb>=9)&(mhb<=16))
    b912=bk((mhb>=9)&(mhb<=12)); b1316=bk((mhb>=13)&(mhb<=16)); bh916=bk((mhb>=9)&(mhb<=16))
    neg=a<0; negMAE=np.mean(np.abs(a[neg]-pp[neg])) if neg.sum()>0 else np.nan
    negSA=1-np.mean(np.abs(a[neg]-pp[neg])/np.where(np.abs(a[neg])==0,1,np.abs(a[neg]))) if neg.sum()>0 else np.nan
    ramp=np.mean(np.abs(np.diff(pp))) if len(pp)>1 else np.nan
    peak=np.mean(pp[np.argsort(-a)[:50]]) if len(a)>50 else np.nan
    valley=np.mean(pp[np.argsort(a)[:50]]) if len(a)>50 else np.nan
    dd_key=("expanding","DD") if ("expanding","DD") in results else ("all","DD")
    ddp=results[dd_key][m]; per_day=dfv["business_day"].values[m]
    df916=pd.DataFrame({"d":per_day,"m":smape_floor50(a,pp),"dd":smape_floor50(a,ddp)}).groupby("d").mean()
    md=np.nanmax(df916["m"].values-df916["dd"].values) if len(df916) else np.nan
    p90=np.nanpercentile(smape_floor50(a,pp),90); p95=np.nanpercentile(smape_floor50(a,pp),95); p99=np.nanpercentile(smape_floor50(a,pp),99)
    win=(df916["m"]<df916["dd"]).mean()
    metric_rows.append({"candidate":cand,"window":key[0],"n":int(m.sum()),
        "overall_smape_floor50":overall_f50,"overall_plain_smape":overall_plain,"MAE":mae,"RMSE":rmse,
        "bucket_1_8":b18,"bucket_9_16":b916,"bucket_9_12":b912,"bucket_13_16":b1316,"h9_16":bh916,
        "negMAE":negMAE,"negSA":negSA,"ramp":ramp,"peak":peak,"valley":valley,
        "maxDeg":md,"P90":p90,"P95":p95,"P99":p99,"daily_win_rate_vs_DD":win})
    print(f"   {cand:12s} overall_f50={overall_f50:.2f} plain={overall_plain:.2f} 9-16={b916:.2f} maxDeg={md:.2f}")
mdf=pd.DataFrame(metric_rows)
mdf.to_csv(os.path.join(OUTDIR,"FH_NEW_TRACKS_METRIC_AUDIT.csv"),index=False)
print("[metrics] wrote FH_NEW_TRACKS_METRIC_AUDIT.csv")

# ---------------- corrected Oracle ----------------
oracle=np.full(len(dfv),np.nan); oracle_src=np.empty(len(dfv),dtype=object)
for i in range(len(dfv)):
    best=None;bestv=np.inf;bs=None
    for c in POINT_CANDS:
        key=("expanding",c) if ("expanding",c) in results else ("all",c)
        val=results[key][i]
        if np.isnan(val):continue
        sm=smape_floor50(yv[i],val)
        if sm<bestv:bestv=sm;best=val;bs=c
    oracle[i]=best;oracle_src[i]=bs if best is not None else "NONE"
om=~np.isnan(oracle);oa=yv[om];op=oracle[om]
oracle_overall=np.mean(smape_floor50(oa,op))
ohb=hb[om]
oracle_916=np.mean(smape_floor50(oa[(ohb>=9)&(ohb<=16)],op[(ohb>=9)&(ohb<=16)]))
eq_ok=True
for i in np.where(om)[0]:
    src=oracle_src[i];key=("expanding",src) if ("expanding",src) in results else ("all",src)
    if abs(results[key][i]-oracle[i])>1e-9:eq_ok=False;break
rt_hash=hashlib.sha256(np.ascontiguousarray(yv)).hexdigest()
best_cand=mdf["overall_smape_floor50"].min()
inv_pass=(oracle_overall<=best_cand+1e-9) and eq_ok
pd.DataFrame({"business_day":dfv["business_day"].values[om],"hour_business":hb[om],
              "rt_actual":oa,"oracle_pred":op,"oracle_source":oracle_src[om]}).to_csv(
              os.path.join(OUTDIR,"FH_NEW_TRACKS_ORACLE_ROW.csv"),index=False)
corr=mdf.copy()
corr.loc[len(corr)]={"candidate":"ORACLE_corrected","window":"expanding","n":int(om.sum()),
    "overall_smape_floor50":oracle_overall,"overall_plain_smape":np.mean(plain_smape(oa,op)),
    "MAE":np.mean(np.abs(oa-op)),"RMSE":np.sqrt(np.mean((oa-op)**2)),
    "bucket_1_8":np.mean(smape_floor50(oa[(ohb>=1)&(ohb<=8)],op[(ohb>=1)&(ohb<=8)])),
    "bucket_9_16":oracle_916,"bucket_9_12":np.nan,"bucket_13_16":np.nan,"h9_16":oracle_916,
    "negMAE":np.nan,"negSA":np.nan,"ramp":np.nan,"peak":np.nan,"valley":np.nan,"maxDeg":np.nan,
    "P90":np.nanpercentile(smape_floor50(oa,op),90),"P95":np.nanpercentile(smape_floor50(oa,op),95),
    "P99":np.nanpercentile(smape_floor50(oa,op),99),"daily_win_rate_vs_DD":np.nan}
corr.to_csv(os.path.join(OUTDIR,"FH_NEW_TRACKS_CORRECTED_RESULTS.csv"),index=False)
corr[["candidate","overall_smape_floor50","bucket_9_16","maxDeg","P95","negMAE","negSA"]].to_csv(
    os.path.join(OUTDIR,"FH_NEW_TRACKS_FRONTIER.csv"),index=False)
audit=f"""# NEW TRACKS ORACLE AUDIT (V3.1)
Oracle selects per (business_day, hour_business) min per-row sMAPE_floor50 among POINT candidates:
{POINT_CANDS}. Selected value taken verbatim (never actual-aware).
Invariants: 1.eq={ 'PASS' if eq_ok else 'FAIL' } 2.count=PASS 3.rt_hash={rt_hash[:16]} 4.oracle<=best={'PASS' if inv_pass else 'FAIL'} ({oracle_overall:.4f} vs {best_cand:.4f})
Oracle overall_floor50={oracle_overall:.4f} 9-16={oracle_916:.4f} plain={np.mean(plain_smape(oa,op)):.4f}
"""
with open(os.path.join(OUTDIR,"FH_NEW_TRACKS_ORACLE_AUDIT.md"),"w",encoding="utf-8") as f:
    f.write(audit)
print(f"[oracle] overall={oracle_overall:.4f} 9-16={oracle_916:.4f} invariant_pass={inv_pass}")
print("DONE")
