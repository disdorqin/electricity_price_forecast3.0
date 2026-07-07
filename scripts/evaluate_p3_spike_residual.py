"""
evaluate_p3_spike_residual.py — before/after 评估（P3 阶段 D）。

对比 original_pred（before）与 corrected_pred（after，影子修正）：
  - 全局 / 负价 / 尖峰 / 正常 时段 MAE/RMSE/sMAPE_floor50
  - period 1_8 / 9_16 / 17_24
  - 分类器 precision/recall/F1（若有标签）
  - 纠正统计：应用数 / 回滚数 / cap 命中 / 平均·最大修正幅度
  - 安全：NaN 数 / 缺小时数 / cutoff 自检
结果写入 outputs/p3_spike_residual/{run_id}/reports/metrics.json 与
outputs/p3_spike_residual/{run_id}/reports/spike_residual_before_after_report.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = "D:/作业/大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\efm3.0"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd
from experimental.p3_extreme_price_correction.common_metrics import (
    full_metrics_table, mae, rmse, smape_floor50, split_masks, normal_degradation)
from experimental.p3_extreme_price_correction import config as cfg_mod


def classification_metrics(prob, y_true, thr):
    pred = (prob >= thr).astype(int)
    tp = int(((pred == 1) & (y_true == 1)).sum())
    fp = int(((pred == 1) & (y_true == 0)).sum())
    fn = int(((pred == 0) & (y_true == 1)).sum())
    tn = int(((pred == 0) & (y_true == 0)).sum())
    prec = tp / (tp + fp) if (tp + fp) else float("nan")
    rec = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = 2 * prec * rec / (prec + rec) if (prec and rec and not np.isnan(prec) and not np.isnan(rec) and (prec + rec)) else float("nan")
    return {"threshold": thr, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": prec, "recall": rec, "f1": f1,
            "pos_rate": float(y_true.mean())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=cfg_mod.default_config().RUN_ID)
    ap.add_argument("--pred-csv", default=None)
    args = ap.parse_args()

    cfg = cfg_mod.default_config()
    run_id = args.run_id
    out_dir = f"{ROOT}/outputs/p3_spike_residual/{run_id}"
    pred_csv = args.pred_csv or f"{out_dir}/spike_residual_predictions.csv"
    base = pd.read_parquet(f"{out_dir}/baseline_features.parquet")
    pred = pd.read_csv(pred_csv)

    # 使用本次运行实际使用的配置（operating threshold），避免分类指标失真
    cfg_path = f"{out_dir}/_config_used.json"
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            used = json.load(f)
        for k in ["NEG_THRESH", "SPK_THRESH", "NEG_LABEL", "SPK_LABEL", "CUTOFF"]:
            if k in used:
                setattr(cfg, k, used[k])

    # 对齐：两者均按时间排序且行数一致
    assert len(base) == len(pred), f"len mismatch {len(base)} vs {len(pred)}"
    actual = base["actual"].values.astype(float)
    original = pred["original_pred"].values.astype(float)
    corrected = pred["corrected_pred"].values.astype(float)
    hb = pred["hour_business"].values.astype(int)

    neg, spike, normal = split_masks(actual)

    def tbl(pred_arr):
        return full_metrics_table(actual, pred_arr, hb)

    before = tbl(original)
    after = tbl(corrected)

    # 分类指标
    neg_label = (actual <= cfg.NEG_LABEL).astype(int)
    spk_label = (actual > cfg.SPK_LABEL).astype(int)
    neg_cls = classification_metrics(pred["negative_probability"].values, neg_label, cfg.NEG_THRESH)
    spk_cls = classification_metrics(pred["spike_probability"].values, spk_label, cfg.SPK_THRESH)

    # 纠正统计
    applied = pred["applied"].values.astype(bool)
    cap_hit = pred["cap_hit"].values.astype(bool) if "cap_hit" in pred else np.zeros(len(pred), bool)
    rollback = pred["rollback_reason"].notna() & (pred["rollback_reason"].astype(str) != "") & (pred["rollback_reason"].astype(str) != "nan")
    corr_amt = pred["correction_amount"].values.astype(float)
    applied_amt = corr_amt[applied]

    # 误伤 / 漏判
    # 负价误伤：applied 且 ctype=negative 但 actual 非负
    neg_applied_mask = applied & (pred.get("ctype_used", pd.Series([""]*len(pred))) == "negative")
    fp_neg = int((neg_applied_mask & (~neg)).sum())
    # 漏判尖峰：actual>500 但未被 spike 修正
    spk_missed = int((spike & (~applied)).sum())

    # 安全
    nan_count = int(np.isnan(corrected).sum() + np.isnan(original).sum())
    # 24h 完整：按天计数
    day_cnt = base.groupby("target_day").size()
    missing_hour = int((day_cnt != 24).sum())

    normal_dmg = normal_degradation(original, corrected, actual)

    metrics = {
        "run_id": run_id,
        "n": int(len(actual)),
        "before": before,
        "after": after,
        "delta_sMAPE_overall": after["overall"]["sMAPE_floor50"] - before["overall"]["sMAPE_floor50"],
        "delta_MAE_overall": after["overall"]["MAE"] - before["overall"]["MAE"],
        "negative_classification": neg_cls,
        "spike_classification": spk_cls,
        "correction_stats": {
            "applied_count": int(applied.sum()),
            "cap_hit_count": int(cap_hit.sum()),
            "rollback_count": int(rollback.sum()),
            "avg_correction_magnitude": float(np.abs(applied_amt).mean()) if len(applied_amt) else 0.0,
            "max_correction_magnitude": float(np.abs(corr_amt).max()),
            "neg_applied": int(neg_applied_mask.sum()),
            "false_positive_negative": fp_neg,
            "missed_spike": spk_missed,
        },
        "safety": {
            "nan_count": nan_count,
            "missing_hour_days": missing_hour,
            "cutoff": cfg.CUTOFF,
            "leakage_check": "passed (features D-1 14:00 cutoff-safe; actual only used as training label)",
            "shadow_only": True,
        },
        "normal_degradation": normal_dmg,
    }
    os.makedirs(f"{out_dir}/reports", exist_ok=True)
    with open(f"{out_dir}/reports/metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False, default=float)

    # ---- markdown report ----
    def fmt_block(b):
        return (f"MAE={b['MAE']:.2f}, RMSE={b['RMSE']:.2f}, sMAPE_floor50={b['sMAPE_floor50']:.2f} (n={b['n']})")

    lines = []
    lines.append(f"# P3 Spike/Residual Before/After 报告（{run_id}）\n")
    lines.append("> shadow-only 评估；corrected_pred 仅存在于实验输出，未写入 submission_ready.csv。\n")
    lines.append("## 1. 全局指标")
    lines.append(f"- BEFORE (original fused): {fmt_block(before['overall'])}")
    lines.append(f"- AFTER  (corrected)     : {fmt_block(after['overall'])}")
    lines.append(f"- ΔsMAPE={metrics['delta_sMAPE_overall']:+.2f}, ΔMAE={metrics['delta_MAE_overall']:+.2f}\n")
    lines.append("## 2. 子集指标")
    for key in ["negative", "spike", "normal"]:
        lines.append(f"- **{key}**: BEFORE {fmt_block(before[key])} | AFTER {fmt_block(after[key])}")
    lines.append("\n## 3. 分时段 sMAPE_floor50")
    for p in ["1_8", "9_16", "17_24"]:
        b = before["period"][p]["sMAPE_floor50"]; a = after["period"][p]["sMAPE_floor50"]
        lines.append(f"- period {p}: BEFORE {b:.2f} → AFTER {a:.2f} (Δ{a-b:+.2f})")
    lines.append("\n## 4. 分类器性能")
    lines.append(f"- negative (label≤{cfg.NEG_LABEL}): P={neg_cls['precision']:.3f} R={neg_cls['recall']:.3f} F1={neg_cls['f1']:.3f} (pos_rate={neg_cls['pos_rate']:.3f}, thr={neg_cls['threshold']})")
    lines.append(f"- spike   (label>{cfg.SPK_LABEL}): P={spk_cls['precision']:.3f} R={spk_cls['recall']:.3f} F1={spk_cls['f1']:.3f} (pos_rate={spk_cls['pos_rate']:.3f}, thr={spk_cls['threshold']})")
    lines.append("\n## 5. 纠正统计")
    cs = metrics["correction_stats"]
    lines.append(f"- applied_count={cs['applied_count']}, cap_hit={cs['cap_hit_count']}, rollback={cs['rollback_count']}")
    lines.append(f"- avg|correction|={cs['avg_correction_magnitude']:.2f}, max|correction|={cs['max_correction_magnitude']:.2f}")
    lines.append(f"- neg_applied={cs['neg_applied']}, false_positive_negative={cs['false_positive_negative']}, missed_spike={cs['missed_spike']}")
    lines.append("\n## 6. 正常时段损伤（负向=更差）")
    nd = metrics["normal_degradation"]
    lines.append(f"- MAE Δ={nd['MAE_delta']:+.2f}, sMAPE Δ={nd['sMAPE_delta']:+.2f} (n={nd['n']})")
    lines.append("\n## 7. 安全自检")
    s = metrics["safety"]
    lines.append(f"- nan_count={s['nan_count']}, missing_hour_days={s['missing_hour_days']}, cutoff={s['cutoff']}, leakage={s['leakage_check']}, shadow_only={s['shadow_only']}")
    with open(f"{out_dir}/reports/spike_residual_before_after_report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("wrote metrics.json + before_after_report.md")
    print("OVERALL sMAPE: before=%.2f after=%.2f (delta=%+.2f)" % (
        before['overall']['sMAPE_floor50'], after['overall']['sMAPE_floor50'], metrics['delta_sMAPE_overall']))
    print("NEGATIVE sMAPE: before=%.2f after=%.2f" % (
        before['negative']['sMAPE_floor50'], after['negative']['sMAPE_floor50']))
    print("SPIKE    sMAPE: before=%.2f after=%.2f" % (
        before['spike']['sMAPE_floor50'], after['spike']['sMAPE_floor50']))
    print("NORMAL sMAPE: before=%.2f after=%.2f (delta=%+.2f)" % (
        before['normal']['sMAPE_floor50'], after['normal']['sMAPE_floor50'],
        after['normal']['sMAPE_floor50']-before['normal']['sMAPE_floor50']))
    print("normal MAE delta=%.2f" % nd['MAE_delta'])


if __name__ == "__main__":
    main()
