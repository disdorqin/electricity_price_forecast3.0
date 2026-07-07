"""
build_baseline_features.py — P3 数据基石。

读取 efm3.0 ledger 中的实时(RT)预测、日前(DA)预测与真实值，
构建一个 leakage-free 的特征矩阵 + 融合基线(original_pred)。

设计要点（严格遵守 D14 cutoff-safe）：
- 融合权重：对每个目标日 d，仅用 d 之前的日子的预测/真实值估计各模型 MAE，
  取 w_i = 1/(MAE_i+eps) 归一化得到融合。首个无历史日用等权。
- 历史同小时统计（负价率 / p50 / p90）：对每个目标日 d，仅用 d 之前日子的
  真实值计算。这是回溯分析，推理时这些信息在 D-1 14:00 均已可见（属过去实际值），不泄漏。
- 绝对不使用 D 日 14:00 之后或 D+1 的真实值作为在线特征。
- y_true 仅用于离线评估，绝不作为校正特征。

输出：outputs/p3_spike_residual/{run_id}/baseline_features.parquet
"""
from __future__ import annotations

import json
import os
import sys

ROOT = "D:/作业/大创_挑战杯_互联网/大学生创新创业计划/大创实现\其他资料\efm3.0"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd
RUN_ID = "p3_rt_20260125_20260225_v1"
RT_MODELS = ["rt916", "sgdfnet", "timemixer", "timesfm"]
DA_MODELS = ["lightgbm", "timesfm", "timemixer"]
OUT = f"{ROOT}/outputs/p3_spike_residual/{RUN_ID}/baseline_features.parquet"


def load_ledger(task: str, kind: str) -> pd.DataFrame:
    path = f"{ROOT}/outputs/ledger/{task}/{kind}/{kind}_ledger.parquet"
    return pd.read_parquet(path)


def build_wide(pred_df: pd.DataFrame, models, value="y_pred") -> pd.DataFrame:
    w = pred_df.pivot_table(
        index=["target_day", "hour_business", "period", "ds", "business_day"],
        columns="model_name", values=value,
    ).reset_index()
    # 仅保留全部模型都存在的行
    present = [m for m in models if m in w.columns]
    w = w.dropna(subset=present)
    return w, present


def fused_inverse_mae(wide: pd.DataFrame, models, actuals: pd.DataFrame) -> pd.Series:
    """逐日 expanding-window 逆 MAE 加权融合。返回与 wide 对齐的 fused Series。"""
    actuals = actuals.set_index("target_day")
    fused = pd.Series(index=wide.index, dtype=float)
    days = sorted(wide["target_day"].unique())
    # 预计算每日每模型误差（需要真实值）——仅用过去日
    for d in days:
        past = actuals[actuals.index < d]
        if len(past) == 0:
            w = np.ones(len(models)) / len(models)
        else:
            maes = []
            for m in models:
                if m in wide.columns:
                    # 用过去日该模型预测与真实值对齐
                    sub = wide[(wide["target_day"] < d)][["target_day", "hour_business", m]].copy()
                    sub = sub.merge(
                        actuals.reset_index()[["target_day", "hour_business", "y_true"]],
                        on=["target_day", "hour_business"], how="inner")
                    if len(sub) > 0:
                        maes.append(mae_sub(sub[m].values, sub["y_true"].values))
                    else:
                        maes.append(np.inf)
                else:
                    maes.append(np.inf)
            maes = np.array(maes)
            inv = 1.0 / (maes + 1e-3)
            if inv.sum() == 0 or np.isnan(inv).any():
                w = np.ones(len(models)) / len(models)
            else:
                w = inv / inv.sum()
        cols = [m for m in models if m in wide.columns]
        wc = w[[models.index(m) for m in cols]]
        wc = wc / wc.sum()
        X = wide.loc[wide["target_day"] == d, cols].values
        fused[wide["target_day"] == d] = X @ wc
    return fused


def mae_sub(a, f):
    return float(np.mean(np.abs(np.asarray(a, float) - np.asarray(f, float))))


def main():
    rtp = load_ledger("realtime", "prediction")
    rta = load_ledger("realtime", "actual")
    dap = load_ledger("dayahead", "prediction")
    dap = dap[dap["model_name"].isin(DA_MODELS)]

    rt_wide, rt_present = build_wide(rtp, RT_MODELS)
    rt_fused = fused_inverse_mae(rt_wide, rt_present, rta)

    da_wide, da_present = build_wide(dap, DA_MODELS)
    # DA 真实值（用于 DA 融合权重估计）
    daa = load_ledger("dayahead", "actual")
    da_fused = fused_inverse_mae(da_wide, da_present, daa)

    df = rt_wide[["target_day", "business_day", "ds", "hour_business", "period"]].copy()
    for m in RT_MODELS:
        if m in rt_wide.columns:
            df[f"pred_{m}"] = rt_wide[m].values
    df["original_pred"] = rt_fused.values
    df["da_anchor"] = da_fused.reindex(rt_wide.index).values

    # 模型分歧度（标准差，作为在线特征，预测时已知）
    pred_cols = [f"pred_{m}" for m in RT_MODELS if f"pred_{m}" in df.columns]
    df["model_std"] = df[pred_cols].std(axis=1)
    df["model_min"] = df[pred_cols].min(axis=1)
    df["model_max"] = df[pred_cols].max(axis=1)

    # 历史同小时统计（expanding，仅用过去真实值）——leakage-free
    actual_full = rta[["target_day", "hour_business", "y_true"]].copy()
    actual_full["target_day"] = pd.to_datetime(actual_full["target_day"])
    hb_rate, hb_p50, hb_p90 = [], [], []
    for _, row in df.iterrows():
        d = pd.Timestamp(row["target_day"])
        h = int(row["hour_business"])
        past = actual_full[(actual_full["target_day"] < d) & (actual_full["hour_business"] == h)]["y_true"]
        if len(past) == 0:
            hb_rate.append(np.nan); hb_p50.append(np.nan); hb_p90.append(np.nan)
        else:
            hb_rate.append(float((past < 0).mean()))
            hb_p50.append(float(past.median()))
            hb_p90.append(float(past.quantile(0.9)))
    df["hist_neg_rate_samehour"] = hb_rate
    df["hist_p50_samehour"] = hb_p50
    df["hist_p90_samehour"] = hb_p90

    # 真实值（仅评估用）
    act = rta[["target_day", "hour_business", "y_true"]].copy()
    df = df.merge(act, on=["target_day", "hour_business"], how="left")
    df = df.rename(columns={"y_true": "actual"})
    df["is_negative"] = df["actual"] < 0
    df["is_spike"] = df["actual"] > 500
    df["is_normal"] = (~df["is_negative"]) & (~df["is_spike"])
    df["task"] = "realtime"

    df.to_parquet(OUT, index=False)
    print("saved:", OUT, "rows=", len(df))

    # ---- 基线诊断输出 ----
    from experimental.p3_extreme_price_correction.common_metrics import (
        full_metrics_table, mae, smape_floor50)
    a = df["actual"].values
    o = df["original_pred"].values
    mt = full_metrics_table(a, o, df["hour_business"].values)
    print("\n=== ORIGINAL FUSED BASELINE (inverse-MAE rolling ensemble) ===")
    for k, v in mt["overall"].items():
        print(f"  overall.{k} = {v:.3f}")
    print("  negative MAE/RMSE/sMAPE:", {k: round(v,3) for k,v in mt["negative"].items()})
    print("  spike    MAE/RMSE/sMAPE:", {k: round(v,3) for k,v in mt["spike"].items()})
    print("  normal   MAE/RMSE/sMAPE:", {k: round(v,3) for k,v in mt["normal"].items()})
    for p, v in mt["period"].items():
        print(f"  period {p}: MAE={v['MAE']:.3f} sMAPE={v['sMAPE_floor50']:.3f} n={v['n']}")

    # 负价小时里 fused 的分布：有多少 fused>0（漏判负价）
    neg = df[df["is_negative"]]
    print("\n=== NEGATIVE HOURS (actual<0), n=%d ===" % len(neg))
    print("  original_pred distribution: min=%.1f med=%.1f max=%.1f" % (
        neg["original_pred"].min(), neg["original_pred"].median(), neg["original_pred"].max()))
    print("  share with original_pred > 0 : %.2f" % (neg["original_pred"] > 0).mean())
    print("  share with original_pred in [-100,100]: %.2f" % ((neg["original_pred"]>=-100)&(neg["original_pred"]<=100)).mean())
    print("  actual values: min=%.1f max=%.1f median=%.1f" % (
        neg["actual"].min(), neg["actual"].max(), neg["actual"].median()))

    # 尖峰小时里 fused 的分布
    sp = df[df["is_spike"]]
    print("\n=== SPIKE HOURS (actual>500), n=%d ===" % len(sp))
    print("  original_pred distribution: min=%.1f med=%.1f max=%.1f" % (
        sp["original_pred"].min(), sp["original_pred"].median(), sp["original_pred"].max()))
    print("  underprediction (fused < actual-100): %.2f" % ((sp["original_pred"] < sp["actual"]-100).mean()))
    print("  overprediction  (fused > actual+100): %.2f" % ((sp["original_pred"] > sp["actual"]+100).mean()))

    # 各模型整体 MAE
    print("\n=== PER-MODEL MAE (overall) ===")
    for m in RT_MODELS:
        if f"pred_{m}" in df.columns:
            print(f"  {m}: MAE={mae(df['actual'], df['pred_'+m]):.3f}  sMAPE={smape_floor50(df['actual'], df['pred_'+m]):.3f}")

    meta = {
        "run_id": RUN_ID,
        "rt_models": rt_present,
        "da_models": da_present,
        "n_rows": int(len(df)),
        "date_range": [str(df["target_day"].min()), str(df["target_day"].max())],
        "fusion": "inverse-MAE expanding-window ensemble (leakage-free)",
        "overall": mt["overall"],
    }
    with open(f"{ROOT}/outputs/p3_spike_residual/{RUN_ID}/_baseline_meta.json", "w") as f:
        json.dump(meta, f, indent=2, default=str)


if __name__ == "__main__":
    main()
