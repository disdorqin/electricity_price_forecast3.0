from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from vmdpy import VMD

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from TimeMixer.repro_pipeline import (
    RunConfig,
    build_arrays,
    date_range_days,
    evaluate_metrics,
    filter_available_days,
    load_data,
    predict_model,
    resolve_task_target_mode,
    restore_target_from_mode,
    run_monthly_reproduction,
    set_seed,
    split_train_valid,
    train_model,
)


MONTHS = ["2026-03", "2026-05"]


def decompose_1d(series: np.ndarray, k: int = 4, alpha: int = 2000, tau: float = 0.0) -> np.ndarray:
    u, _, _ = VMD(series.astype(float), alpha, tau, k, 0, 1, 1e-7)
    return np.asarray(u, dtype=float)


def build_vmd_targets(y_full: np.ndarray, start: int = 8, end: int = 16, k: int = 4) -> np.ndarray:
    comps = []
    for sample in y_full:
        modes = decompose_1d(sample, k=k)
        comps.append(modes[:, start:end])
    return np.stack(comps)


def build_vmd_past(past_full: np.ndarray, k: int = 4) -> np.ndarray:
    mode_pasts = []
    for sample in past_full:
        modes = decompose_1d(sample[:, 0], k=k)
        per_mode = []
        for mode in modes:
            mod_sample = sample.copy()
            mod_sample[:, 0] = mode
            per_mode.append(mod_sample)
        mode_pasts.append(np.stack(per_mode))
    return np.stack(mode_pasts)


def run_month(month: str) -> dict[str, object]:
    base_cfg = RunConfig(
        data_path=r"D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\epf\data\shandong_pmos_hourly.csv",
        output_dir=f"fusion_runs/timemixer_vmd_rt916/{month}_baseline_cache",
        month=month,
        pipeline_mode="historical_joint",
        backbone="timemixer",
        da_loss_mode="asymmetric_under",
        da_under_weight_multiplier=1.25,
        rt_loss_mode="risk_peak_weighted",
        rt_peak_weight_multiplier=1.4,
        rt_calibration_mode="rt_916_regime_affine",
        seed=42,
        device="cpu",
    )
    base_result = run_monthly_reproduction(base_cfg)
    base_pred = pd.read_csv(Path(base_result["output_dir"]) / "predictions_raw.csv")
    base_pred["ds"] = pd.to_datetime(base_pred["ds"])
    pred_da_map = {
        pd.Timestamp(row.ds): float(row.y_pred)
        for row in base_pred[base_pred["task"] == "da"].itertuples()
    }

    cfg = base_cfg
    set_seed(cfg.seed)
    device = torch.device("cpu")
    df = load_data(cfg.data_path)
    test_start = pd.Timestamp(f"{month}-01")
    test_end = test_start + pd.offsets.MonthBegin(1)
    train_start = max(
        df["ds"].min().normalize() + pd.Timedelta(days=8),
        test_start - pd.DateOffset(months=cfg.train_months),
    )
    train_days_all = date_range_days(train_start, test_start)
    train_days, valid_days = split_train_valid(train_days_all, cfg.val_ratio)
    test_days = date_range_days(test_start, test_end)
    da_target_mode = resolve_task_target_mode(cfg, "da")
    rt_target_mode = resolve_task_target_mode(cfg, "rt")
    test_days = filter_available_days(
        df,
        test_days,
        seq_len=cfg.seq_len,
        cutoff_hour_da=cfg.cutoff_hour_da,
        cutoff_hour_rt=cfg.cutoff_hour_rt,
        da_target_mode=da_target_mode,
        rt_target_mode=rt_target_mode,
    )

    train_past, train_future, train_y_full, train_baseline = build_arrays(
        df, train_days, "realtime_price", cfg.seq_len, cfg.cutoff_hour_rt, pred_da_map=None, target_mode=rt_target_mode
    )
    valid_past, valid_future, valid_y_full, valid_baseline = build_arrays(
        df, valid_days, "realtime_price", cfg.seq_len, cfg.cutoff_hour_rt, pred_da_map=None, target_mode=rt_target_mode
    )
    test_past, test_future, _, test_baseline = build_arrays(
        df, test_days, "realtime_price", cfg.seq_len, cfg.cutoff_hour_rt, pred_da_map=pred_da_map, target_mode=rt_target_mode
    )

    train_past_modes = build_vmd_past(train_past)
    valid_past_modes = build_vmd_past(valid_past)
    test_past_modes = build_vmd_past(test_past)
    train_target_modes = build_vmd_targets(restore_target_from_mode(train_y_full, train_baseline, rt_target_mode))
    valid_target_modes = build_vmd_targets(restore_target_from_mode(valid_y_full, valid_baseline, rt_target_mode))

    mode_preds = []
    for mode_idx in range(4):
        mode_cfg = cfg
        bundle = train_model(
            train_past_modes[:, mode_idx],
            train_future[:, 8:16, :],
            train_target_modes[:, mode_idx],
            mode_cfg,
            device,
            task="rt",
            segment_name=f"9_16_vmd_mode{mode_idx}",
        )
        pred_scaled = predict_model(
            bundle,
            test_past_modes[:, mode_idx],
            test_future[:, 8:16, :],
            device,
            cfg.batch_size,
        )
        mode_preds.append(pred_scaled)
    vmd_pred = np.sum(np.stack(mode_preds), axis=0)

    fused = base_pred.copy()
    rt916_mask = (fused["task"] == "rt") & (fused["period"] == "9_16")
    fused.loc[rt916_mask, "y_pred"] = vmd_pred.reshape(-1)
    out_dir = Path("fusion_runs/timemixer_vmd_rt916") / f"{month}_vmd_rt916"
    out_dir.mkdir(parents=True, exist_ok=True)
    fused.to_csv(out_dir / "predictions_raw.csv", index=False, encoding="utf-8-sig")
    metrics = pd.concat(
        [
            evaluate_metrics(fused[fused["task"] == "da"].copy(), "da"),
            evaluate_metrics(fused[fused["task"] == "rt"].copy(), "rt"),
        ],
        ignore_index=True,
    )
    metrics.to_csv(out_dir / "metrics_by_period.csv", index=False, encoding="utf-8-sig")
    (out_dir / "run_manifest.json").write_text(
        json.dumps(
            {
                "month": month,
                "decomposition_mode": "vmd",
                "k": 4,
                "alpha": 2000,
                "tau": 0,
                "base_output_dir": base_result["output_dir"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"output_dir": str(out_dir), "metrics": metrics}


def main() -> None:
    rows = []
    out_root = Path("fusion_runs/timemixer_vmd_rt916")
    out_root.mkdir(parents=True, exist_ok=True)
    for month in MONTHS:
        result = run_month(month)
        metrics = result["metrics"]
        rows.append(
            {
                "month": month,
                "output_dir": result["output_dir"],
                "rt_overall": float(metrics.loc[(metrics["task"] == "rt") & (metrics["period"] == "overall"), "sMAPE"].iloc[0]),
                "rt_9_16": float(metrics.loc[(metrics["task"] == "rt") & (metrics["period"] == "9_16"), "sMAPE"].iloc[0]),
            }
        )
    summary = pd.DataFrame(rows)
    summary.to_csv(out_root / "monthly_summary.csv", index=False, encoding="utf-8-sig")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
