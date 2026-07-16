"""
EFM3 V3.1-R1 — shared CORRECTED replay engine.

Single source of truth for:
  - panel loading (business_day consistent via utils.business_day)
  - rolling-origin OOS day-ahead (DA) model  -> legal DA proxy `da_oos_pred`
  - RT candidate tracks A-F with all V3.1-R1 defects fixed
  - unified metrics imported from fusion.metrics (no hand-rolled copies)
  - unified evaluation support (coverage / common mask / improvement_vs_DD)
  - legal Oracle (same-coverage, EX_POST_ACTUAL_AWARE_UPPER_BOUND, invariants)

Design rules (see V31_FORECAST_AVAILABILITY_CONTRACT.md):
  * target-day da_actual / rt_actual are NEVER used as features or baseline.
  * the only legal DA proxy is `da_oos_pred`, produced by a rolling-origin DA
    model trained on PAST da_actual only.
  * every prediction is made under rolling origin (train uses only data < t).
  * GPU disabled (segfault-prone on this host) -> CPU-only.

Track fixes vs V3.1:
  D: bin edges fit per rolling TRAIN window (never on full history / target).
  F: strict OOF two-stage (residual predicted from legal features only).
  C: relative->absolute index fix; assembled C_seasonal_full.
  B: assembled B_midday_full (1-8=DD, 9-16=B_midday, 17-24=DD) + keep 9-16-only.
"""
from __future__ import annotations
import os, sys, json, hashlib, time
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
import lightgbm as lgb

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
from utils.business_day import timestamp_from_business  # noqa: E402
from fusion.metrics import plain_smape, smape_floor50  # noqa: E402

PANEL = os.path.join(REPO_ROOT, "data_audit", "FULL_HISTORY_CANONICAL_PANEL.parquet")
OUTDIR = os.path.join(REPO_ROOT, "data_audit")
os.makedirs(OUTDIR, exist_ok=True)

RETRAIN_DEFAULT = 90
WINDOWS_DEFAULT = [180, 365, None]  # None = expanding


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------
def load_panel() -> pd.DataFrame:
    df = pd.read_parquet(PANEL)
    df["ds"] = pd.to_datetime(df["ds"])
    df = df.sort_values("ds").reset_index(drop=True)
    # calendar features derived from canonical ds (legal, known at time t)
    df["hour"] = df["hour_business"].astype(int)
    df["dayofweek"] = df["ds"].dt.dayofweek.astype(int)
    df["month"] = df["ds"].dt.month.astype(int)
    df["is_weekend"] = (df["dayofweek"] >= 5).astype(int)
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24.0)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24.0)
    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12.0)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12.0)
    return df


def prepare(df: pd.DataFrame):
    """Return (dfv, yv, hb, month_arr, feat_base, feat_anchor, da_oos_pred)."""
    exo_fc = [c for c in df.columns if c.endswith("_forecast")]
    lag_cols = [c for c in df.columns if "_lag_" in c]
    cal_cols = ["hour", "dayofweek", "month", "is_weekend",
                "hour_sin", "hour_cos", "month_sin", "month_cos"]
    rt_feat = exo_fc + lag_cols + cal_cols
    da_feat = exo_fc + [c for c in df.columns if "da_price_lag" in c] + cal_cols
    rt_feat = [c for c in rt_feat if c in df.columns]
    da_feat = [c for c in da_feat if c in df.columns]

    valid0 = (df[rt_feat].notna().all(axis=1).values
              & df["da_actual"].notna().values
              & df["rt_actual"].notna().values)
    dfv = df[valid0].reset_index(drop=True)

    da_oos_pred = build_oos_da(dfv, da_feat)
    dfv["da_oos_pred"] = da_oos_pred

    feat_base = ["da_oos_pred"] + rt_feat
    feat_anchor = feat_base + ["da_price_bin"]
    valid1 = (dfv[feat_base].notna().all(axis=1).values
              & np.isfinite(dfv["rt_actual"].values))
    dfv = dfv[valid1].reset_index(drop=True)
    # da_oos_pred already a column -> stays aligned after filter

    yv = dfv["rt_actual"].astype(float).values
    hb = dfv["hour_business"].astype(int).values
    month_arr = dfv["month"].astype(int).values
    return dfv, yv, hb, month_arr, feat_base, feat_anchor, dfv["da_oos_pred"].astype(float).values


# ---------------------------------------------------------------------------
# Rolling-origin OOS day-ahead (DA) model  ->  legal DA proxy
# ---------------------------------------------------------------------------
def build_oos_da(dfv: pd.DataFrame, da_feat, retrain=RETRAIN_DEFAULT,
                 windows=WINDOWS_DEFAULT, verbose=False):
    """Train L2 LightGBM per rolling window to predict da_actual (PAST only),
    producing OOS DA forecast for each target day. No target-day leakage."""
    X = dfv[da_feat].astype(float)
    y = dfv["da_actual"].astype(float).values
    valid = X.notna().all(axis=1).values & np.isfinite(y)
    Xv = X[valid].reset_index(drop=True)
    yv = y[valid]
    dfx = dfv[valid].reset_index(drop=True)
    days = sorted(dfx["business_day"].unique())
    out = np.full(len(dfx), np.nan)

    def make():
        return lgb.LGBMRegressor(n_estimators=200, learning_rate=0.05,
                                 num_leaves=31, min_child_samples=50,
                                 subsample=0.9, colsample_bytree=0.9,
                                 n_jobs=4, random_state=42)

    for W in windows:
        wlabel = "expanding" if W is None else f"W{W}"
        anchors = days[::retrain]
        for a in range(len(anchors) - 1):
            t0, t1 = anchors[a], anchors[a + 1]
            tr_mask = (dfx["ds"] < pd.Timestamp(t0))
            if W is not None:
                tr_mask &= (dfx["ds"] >= pd.Timestamp(t0) - pd.Timedelta(days=W))
            tr = np.where(tr_mask.values)[0]
            pr_mask = (dfx["ds"] >= pd.Timestamp(t0)) & (dfx["ds"] < pd.Timestamp(t1))
            pr = np.where(pr_mask.values)[0]
            if len(tr) < 200 or len(pr) == 0:
                continue
            m = make(); m.fit(Xv.iloc[tr].values, yv[tr])
            out[pr] = m.predict(Xv.iloc[pr].values)
        # last block to end
        t0 = anchors[-1]
        tr_mask = (dfx["ds"] < pd.Timestamp(t0))
        if W is not None:
            tr_mask &= (dfx["ds"] >= pd.Timestamp(t0) - pd.Timedelta(days=W))
        tr = np.where(tr_mask.values)[0]
        pr_mask = (dfx["ds"] >= pd.Timestamp(t0))
        pr = np.where(pr_mask.values)[0]
        if len(tr) >= 200:
            m = make(); m.fit(Xv.iloc[tr].values, yv[tr])
            out[pr] = m.predict(Xv.iloc[pr].values)
        if verbose:
            print(f"   [oos_da] {wlabel} done")
    # final out = union across windows (expanding dominates coverage)
    return out


# ---------------------------------------------------------------------------
# Track implementations  (each returns array aligned to dfv, NaN outside pred)
# ---------------------------------------------------------------------------
def _make_lgb(params, objective=None):
    base = dict(n_estimators=200, learning_rate=0.05, num_leaves=31,
                min_child_samples=50, subsample=0.9, colsample_bytree=0.9,
                n_jobs=4, random_state=42, verbose=-1)
    if objective:
        base["objective"] = objective
        if "alpha" in params:
            base["alpha"] = params["alpha"]
        if "fair_c" in params:
            base["fair_c"] = params["fair_c"]
    base.update({k: v for k, v in params.items() if k not in ("alpha", "fair_c")})
    return lgb.LGBMRegressor(**base)


def _fit_predict(Xtr, ytr, Xpr, params, objective=None):
    m = _make_lgb(params, objective)
    m.fit(Xtr, ytr)
    return m.predict(Xpr)


def get_pred(track, params, train_idx, pred_idx, dfv, yv, hb, month_arr,
             feat_base, feat_anchor, da_oos_pred):
    out = np.full(len(dfv), np.nan)
    Xv = dfv

    if track in ("l2", "quantile", "robust"):
        obj = None
        if track == "quantile":
            obj = "quantile"
        elif track == "robust":
            obj = params.get("objective")  # "fair" or "huber"
        feats = feat_anchor if track == "anchor" else feat_base
        Xi = Xv.iloc[train_idx][feats].values; yi = yv[train_idx]
        Xp = Xv.iloc[pred_idx][feats].values
        out[pred_idx] = _fit_predict(Xi, yi, Xp, params, obj)

    elif track == "anchor":
        # FIX D: bin edges fit on TRAIN-window da_oos_pred only (never full history)
        tr_da = da_oos_pred[train_idx]
        tr_da = tr_da[np.isfinite(tr_da)]
        if len(tr_da) < 50:
            return out
        try:
            edges = pd.qcut(pd.Series(tr_da), 5, retbins=True, duplicates="drop")[1]
        except Exception:
            return out
        edges = np.unique(edges)
        if len(edges) < 3:
            return out
        bin_tr = pd.cut(da_oos_pred[train_idx], bins=edges, labels=False,
                         include_lowest=True)
        bin_pr = pd.cut(da_oos_pred[pred_idx], bins=edges, labels=False,
                        include_lowest=True)
        # rows falling outside edges -> NaN -> drop
        keep_tr = ~np.isnan(bin_tr.astype(float))
        keep_pr = ~np.isnan(bin_pr.astype(float))
        if keep_tr.sum() < 50 or keep_pr.sum() == 0:
            return out
        Xi = np.column_stack([Xv.iloc[train_idx][feat_base].values[keep_tr],
                              bin_tr[keep_tr].astype(float).reshape(-1, 1)])
        yi = yv[train_idx][keep_tr]
        Xp = np.column_stack([Xv.iloc[pred_idx][feat_base].values[keep_pr],
                              bin_pr[keep_pr].astype(float).reshape(-1, 1)])
        pr_idx_kept = pred_idx[keep_pr]
        out[pr_idx_kept] = _fit_predict(Xi, yi, Xp, params, None)

    elif track == "midday":
        # B_midday trained/predicted ONLY on 9-16; assemble B_midday_full elsewhere
        tr_mid = np.intersect1d(train_idx, np.where((hb >= 9) & (hb <= 16))[0])
        pr_mid = np.intersect1d(pred_idx, np.where((hb >= 9) & (hb <= 16))[0])
        if len(tr_mid) < 200 or len(pr_mid) == 0:
            return out
        Xi = Xv.iloc[tr_mid][feat_base].values; yi = yv[tr_mid]
        Xp = Xv.iloc[pr_mid][feat_base].values
        out[pr_mid] = _fit_predict(Xi, yi, Xp, params, None)

    elif track == "season":
        seas = params["season"]

        def season_of(m):
            if m in (12, 1, 2):
                return "winter"
            if m in (6, 7, 8):
                return "summer"
            return "shoulder"
        # FIX C: relative mask -> apply to absolute train_idx / pred_idx
        tr_rel = np.where(np.array([season_of(m) for m in month_arr[train_idx]]) == seas)[0]
        pr_rel = np.where(np.array([season_of(m) for m in month_arr[pred_idx]]) == seas)[0]
        tr_s = train_idx[tr_rel]
        pr_s = pred_idx[pr_rel]
        if len(tr_s) < 200 or len(pr_s) == 0:
            return out
        Xi = Xv.iloc[tr_s][feat_base].values; yi = yv[tr_s]
        Xp = Xv.iloc[pr_s][feat_base].values
        out[pr_s] = _fit_predict(Xi, yi, Xp, params, None)

    elif track == "regime":
        # FIX F: strict OOF two-stage. residual predicted from LEGAL features only.
        from sklearn.model_selection import KFold
        Xi = Xv.iloc[train_idx][feat_base].values; yi = yv[train_idx]
        Xp = Xv.iloc[pred_idx][feat_base].values
        if len(Xi) < 200 or len(Xp) == 0:
            return out
        kf = KFold(n_splits=5, shuffle=True, random_state=42)
        oof = np.full(len(yi), np.nan)
        base = _make_lgb(params, None)
        for tr, va in kf.split(Xi):
            m = _make_lgb(params, None)
            m.fit(Xi[tr], yi[tr])
            oof[va] = m.predict(Xi[va])
        # stage2: predict residual from legal features
        resid = yi - oof
        m2 = _make_lgb(params, None)
        m2.fit(Xi, resid)
        base_pred = _fit_predict(Xi, yi, Xp, params, None)
        resid_pred = m2.predict(Xp)
        out[pred_idx] = base_pred + resid_pred

    return out


# ---------------------------------------------------------------------------
# Rolling driver
# ---------------------------------------------------------------------------
def run_tracks(candidate_defs, dfv, yv, hb, month_arr, feat_base, feat_anchor,
               da_oos_pred, retrain=RETRAIN_DEFAULT, windows=WINDOWS_DEFAULT,
               mini=False, verbose=False):
    """candidate_defs: name -> (track, params). Returns results dict
    {(wlabel, name): array aligned to dfv} plus DD."""
    days = sorted(dfv["business_day"].unique())
    if mini:
        retrain = 7
        # restrict to first ~21 target days after warmup
        days = days[:max(22, retrain + 15)]
    results = {}
    for W in windows:
        wlabel = "expanding" if W is None else f"W{W}"
        if verbose:
            print(f"[train] window={wlabel}")
        anchors = days[::retrain]
        for a in range(len(anchors) - 1):
            t0, t1 = anchors[a], anchors[a + 1]
            tr_mask = (dfv["ds"] < pd.Timestamp(t0))
            if W is not None:
                tr_mask &= (dfv["ds"] >= pd.Timestamp(t0) - pd.Timedelta(days=W))
            train_idx = np.where(tr_mask.values)[0]
            pr_mask = (dfv["ds"] >= pd.Timestamp(t0)) & (dfv["ds"] < pd.Timestamp(t1))
            pred_idx = np.where(pr_mask.values)[0]
            if len(train_idx) < 200 or len(pred_idx) == 0:
                continue
            for name, (track, params) in candidate_defs.items():
                p = get_pred(track, params, train_idx, pred_idx, dfv, yv, hb,
                             month_arr, feat_base, feat_anchor, da_oos_pred)
                key = (wlabel, name)
                results.setdefault(key, np.full(len(dfv), np.nan))
                results[key][pred_idx] = p[pred_idx]
        # last block
        t0 = anchors[-1]
        tr_mask = (dfv["ds"] < pd.Timestamp(t0))
        if W is not None:
            tr_mask &= (dfv["ds"] >= pd.Timestamp(t0) - pd.Timedelta(days=W))
        train_idx = np.where(tr_mask.values)[0]
        pr_mask = (dfv["ds"] >= pd.Timestamp(t0))
        pred_idx = np.where(pr_mask.values)[0]
        if len(train_idx) >= 200:
            for name, (track, params) in candidate_defs.items():
                p = get_pred(track, params, train_idx, pred_idx, dfv, yv, hb,
                             month_arr, feat_base, feat_anchor, da_oos_pred)
                key = (wlabel, name)
                results.setdefault(key, np.full(len(dfv), np.nan))
                results[key][pred_idx] = p[pred_idx]
        if verbose:
            print(f"   fits done for {wlabel}")

    # DD baseline = OOS DA proxy (legal)
    results[("all", "DD")] = da_oos_pred.copy()
    # A_QRA ensemble (expanding quantile avg) if available and non-empty
    qk, mk, hk = ("expanding", "A_q05"), ("expanding", "A_q50"), ("expanding", "A_q95")
    if all(k in results for k in (qk, mk, hk)):
        q, m, h = results[qk], results[mk], results[hk]
        if not (np.isnan(q).all() or np.isnan(m).all() or np.isnan(h).all()):
            results[("expanding", "A_QRA")] = np.nanmean(np.stack([q, m, h]), axis=0)
    # NEGW (equal blend of expanding med + W180 med) if available
    if ("expanding", "A05_med") in results and ("W180", "A05_med") in results:
        em = results[("expanding", "A05_med")]; w = results[("W180", "A05_med")]
        results[("all", "NEGW")] = np.where(np.isnan(em), w, np.where(np.isnan(w), em, 0.5 * em + 0.5 * w))
    return results


# ---------------------------------------------------------------------------
# B_midday_full assembly (defect #7): 1-8=DD, 9-16=B_midday, 17-24=DD
# ---------------------------------------------------------------------------
def assemble_b_midday_full(results, dfv, hb):
    if ("expanding", "B_midday") not in results or ("all", "DD") not in results:
        return results
    bm = results[("expanding", "B_midday")]
    dd = results[("all", "DD")]
    full = dd.copy()
    mask = (hb >= 9) & (hb <= 16)
    full[mask & ~np.isnan(bm)] = bm[mask & ~np.isnan(bm)]
    results[("expanding", "B_midday_full")] = full
    return results


# ---------------------------------------------------------------------------
# Unified metrics + evaluation support
# ---------------------------------------------------------------------------
def evaluate(results, dfv, yv, hb, point_cands, out_prefix="FH"):
    cand_names = sorted(set(k[1] for k in results.keys()))
    # arrays aligned to dfv
    arr = {n: results[("expanding", n)] if ("expanding", n) in results
           else results[("all", n)] for n in cand_names}
    dd = arr.get("DD")
    # Intrinsically-partial candidates (per-season / 9-16-only) are NOT comparable
    # on a single full-coverage mask. The FINAL ranking common mask uses only
    # full-coverage candidates.
    PARTIAL = {"C_winter", "C_summer", "C_shoulder", "B_midday", "B_midday_9_16_only"}
    rank_cands = [n for n in cand_names if (n in arr) and (n not in PARTIAL)]
    # common mask: rows where ALL full-coverage candidates (incl DD) present
    stack = np.stack([arr[n] for n in rank_cands], axis=1)
    common = (~np.isnan(stack)).all(axis=1)
    n_common = int(common.sum())

    rt_hash = hashlib.sha256(np.ascontiguousarray(yv)).hexdigest()
    rows = []
    for n in cand_names:
        p = arr[n]
        cand_mask = ~np.isnan(p)
        cov_rows = int(cand_mask.sum())
        cov_ratio = cov_rows / len(p)
        # dd same mask: candidate & DD both present
        dd_same = cand_mask & (~np.isnan(dd)) if dd is not None else cand_mask
        a = yv[dd_same]; pp = p[dd_same]
        if len(a) == 0:
            rows.append(dict(candidate=n, coverage_rows=cov_rows,
                             coverage_ratio=round(cov_ratio, 4),
                             n_eval=int(cov_rows),
                             overall_plain=np.nan, overall_f50=np.nan,
                             dd_plain=np.nan, dd_f50=np.nan,
                             improvement_vs_DD_plain=np.nan,
                             improvement_vs_DD_f50=np.nan,
                             bucket_9_16_plain=np.nan, maxDeg=np.nan,
                             daily_win_rate_vs_DD=np.nan))
            continue
        dd_p = dd[dd_same]
        overall_plain = plain_smape(a, pp)
        overall_f50 = smape_floor50(a, pp)
        dd_plain = plain_smape(a, dd_p)
        dd_f50 = smape_floor50(a, dd_p)
        # buckets on dd_same (9-16)
        m916 = (hb[dd_same] >= 9) & (hb[dd_same] <= 16)
        b916_plain = plain_smape(a[m916], pp[m916]) if m916.sum() else np.nan
        # daily degradation vs DD (9-16)
        per_day = dfv["business_day"].values[dd_same]
        dfc = pd.DataFrame({"d": per_day,
                            "m": smape_floor50(a, pp),
                            "dd": smape_floor50(a, dd_p)}).groupby("d").mean()
        maxDeg = float(np.nanmax(dfc["m"].values - dfc["dd"].values)) if len(dfc) else nan
        win = float((dfc["m"] < dfc["dd"]).mean()) if len(dfc) else nan
        rows.append(dict(
            candidate=n, coverage_rows=cov_rows, coverage_ratio=round(cov_ratio, 4),
            n_eval=int(cov_rows),
            overall_plain=round(overall_plain, 4), overall_f50=round(overall_f50, 4),
            dd_plain=round(dd_plain, 4), dd_f50=round(dd_f50, 4),
            improvement_vs_DD_plain=round(dd_plain - overall_plain, 4),
            improvement_vs_DD_f50=round(dd_f50 - overall_f50, 4),
            bucket_9_16_plain=round(b916_plain, 4) if not np.isnan(b916_plain) else np.nan,
            maxDeg=round(maxDeg, 4), daily_win_rate_vs_DD=round(win, 4),
        ))
    mdf = pd.DataFrame(rows)

    # final ranking on common mask (full-coverage candidates comparable)
    common_rows = []
    a_com = yv[common]; hb_com = hb[common]
    for n in rank_cands:
        pp = arr[n][common]
        common_rows.append(dict(
            candidate=n, n_common=n_common,
            common_plain=round(plain_smape(a_com, pp), 4),
            common_f50=round(smape_floor50(a_com, pp), 4),
        ))
    cdf = pd.DataFrame(common_rows).sort_values("common_plain")

    mdf.to_csv(os.path.join(OUTDIR, f"{out_prefix}_METRIC_AUDIT.csv"), index=False)
    cdf.to_csv(os.path.join(OUTDIR, f"{out_prefix}_COMMON_MASK_RANKING.csv"), index=False)
    return mdf, cdf, arr, common, rt_hash, n_common, rank_cands


# ---------------------------------------------------------------------------
# Legal Oracle (EX_POST_ACTUAL_AWARE_UPPER_BOUND)
# ---------------------------------------------------------------------------
def legal_oracle(results, arr, yv, hb, common, point_extra=None, out_prefix="FH"):
    """Per-row min plain_smape selection among candidates present on `common`.
    Verifies invariants. Marks EX_POST (not achievable in deployment)."""
    cand_names = sorted(set(k[1] for k in results.keys()))
    use = [n for n in cand_names if n in arr and not np.isnan(arr[n][common]).all()]
    if point_extra:
        use = use + [n for n in point_extra if n in arr and not np.isnan(arr[n][common]).all()]
    use = sorted(set(use))
    a_com = yv[common]; hb_com = hb[common]
    oracle = np.full(common.sum(), np.nan)
    src = np.empty(common.sum(), dtype=object)
    for i in range(common.sum()):
        best = None; bv = np.inf; bs = None
        for n in use:
            v = arr[n][common][i]
            if np.isnan(v):
                continue
            s = plain_smape(np.array([a_com[i]]), np.array([v]))
            if s < bv:
                bv = s; best = v; bs = n
        oracle[i] = best; src[i] = bs if best is not None else "NONE"
    om = ~np.isnan(oracle)
    oa = a_com[om]; op = oracle[om]
    overall_plain = plain_smape(oa, op)
    overall_f50 = smape_floor50(oa, op)
    hb_o = hb_com[om]
    b916_plain = plain_smape(oa[(hb_o >= 9) & (hb_o <= 16)], op[(hb_o >= 9) & (hb_o <= 16)])

    # invariants
    eq_ok = True
    for i in np.where(om)[0]:
        if abs(arr[src[i]][common][i] - oracle[i]) > 1e-9:
            eq_ok = False; break
    # oracle per-row loss <= every candidate per-row loss (on common)
    loss_ok = True
    for n in use:
        pn = arr[n][common][om]
        c = plain_smape(oa, pn)
        if c + 1e-9 < overall_plain:
            loss_ok = False; break
    inv_pass = eq_ok and loss_ok
    audit = {
        "oracle_type": "EX_POST_ACTUAL_AWARE_UPPER_BOUND",
        "candidates_used": use,
        "n_rows": int(om.sum()),
        "overall_plain_smape": round(overall_plain, 4),
        "overall_floor50_smape": round(overall_f50, 4),
        "bucket_9_16_plain_smape": round(b916_plain, 4) if not np.isnan(b916_plain) else None,
        "invariant_eq_selected_equals_candidate": eq_ok,
        "invariant_oracle_loss_le_each_candidate": loss_ok,
        "invariant_pass": inv_pass,
        "note": "Oracle selects per-row min loss; it is an EX-POST upper bound, "
                "not a deployable strategy. Not used for promotion.",
    }
    with open(os.path.join(OUTDIR, f"{out_prefix}_ORACLE_AUDIT.json"), "w", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False, indent=2)
    pd.DataFrame({"business_day": np.array([])}).to_csv(  # placeholder to avoid empty
        os.path.join(OUTDIR, f"{out_prefix}_ORACLE_ROW.csv"), index=False)
    return audit


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------
def run_replay(candidate_defs, out_prefix, mini=False, verbose=False, prepared=None):
    t0 = time.time()
    if prepared is not None:
        # reuse a pre-built (dfv, yv, hb, month_arr, feat_base, feat_anchor, da_oos_pred)
        (dfv, yv, hb, month_arr, feat_base, feat_anchor, da_oos_pred) = prepared
    else:
        df = load_panel()
        dfv, yv, hb, month_arr, feat_base, feat_anchor, da_oos_pred = prepare(df)
    if mini:
        # TRUE 14-day-scale mini: restrict to first ~30 business days so the
        # last rolling block cannot spill into the full history.
        d30 = sorted(dfv["business_day"].unique())[:30]
        m = dfv["business_day"].isin(d30)
        dfv = dfv[m].reset_index(drop=True)
        da_oos_pred = dfv["da_oos_pred"].astype(float).values
        yv = dfv["rt_actual"].astype(float).values
        hb = dfv["hour_business"].astype(int).values
        month_arr = dfv["month"].astype(int).values
        feat_base = [c for c in feat_base if c in dfv.columns]
        feat_anchor = [c for c in feat_anchor if c in dfv.columns]
    if verbose:
        print(f"[prepare] usable rows={len(dfv)} {dfv['ds'].min()}->{dfv['ds'].max()}")
    results = run_tracks(candidate_defs, dfv, yv, hb, month_arr, feat_base,
                         feat_anchor, da_oos_pred, mini=mini, verbose=verbose)
    results = assemble_b_midday_full(results, dfv, hb)
    # C_seasonal_full: union of per-season predictions (each row -> exactly one season)
    seas_parts = [n for n in ("C_winter", "C_summer", "C_shoulder")
                  if ("expanding", n) in results]
    if seas_parts:
        full = np.full(len(dfv), np.nan)
        for n in seas_parts:
            p = results[("expanding", n)]
            full[~np.isnan(p)] = p[~np.isnan(p)]
        results[("expanding", "C_seasonal_full")] = full
    # point candidates for oracle
    point_cands = [n for n in set(k[1] for k in results.keys())
                   if n not in ("B_midday_9_16_only",)]
    mdf, cdf, arr, common, rt_hash, n_common, rank_cands = evaluate(
        results, dfv, yv, hb, point_cands, out_prefix=out_prefix)
    audit = legal_oracle(results, arr, yv, hb, common, out_prefix=out_prefix)
    if verbose:
        print(f"[done] {out_prefix} in {time.time()-t0:.1f}s | common_mask rows={n_common}")
        print(mdf[["candidate", "overall_plain", "overall_f50", "improvement_vs_DD_plain",
                   "coverage_ratio"]].to_string(index=False))
        print("ORACLE:", audit["overall_plain_smape"], "inv_pass=", audit["invariant_pass"])
    return dict(results=results, mdf=mdf, cdf=cdf, audit=audit,
                common_rows=n_common, rt_hash=rt_hash, rank_cands=rank_cands)
