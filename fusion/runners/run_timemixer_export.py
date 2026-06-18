from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from TimeMixer.repro_pipeline import RunConfig, run_monthly_reproduction


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", required=True)
    parser.add_argument("--test-start")
    parser.add_argument("--test-end-exclusive")
    parser.add_argument("--pipeline-mode", default="single_task", choices=["single_task", "historical_joint"])
    parser.add_argument("--backbone", default="timemixer", choices=["timemixer", "timesnet"])
    parser.add_argument("--rt-916-backbone", choices=["timemixer", "timesnet"])
    parser.add_argument("--data-path", default=r"D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\epf\data\shandong_pmos_hourly.csv")
    parser.add_argument("--output-root", default="fusion_runs/timemixer")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--hidden-dim", type=int, default=64)
    parser.add_argument("--blocks", type=int, default=2)
    parser.add_argument("--scales", type=int, default=3)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--rt-segment-head-mode", default="none", choices=["none", "future_residual"])
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--patience", type=int, default=6)
    parser.add_argument("--seq-len", type=int, default=168)
    parser.add_argument("--train-months", type=int, default=12)
    parser.add_argument("--training-mode", default="rolling", choices=["rolling", "frozen"])
    parser.add_argument("--frozen-train-start")
    parser.add_argument("--frozen-train-end-exclusive")
    parser.add_argument("--decomposition-mode", default="none", choices=["none", "vmd"])
    parser.add_argument("--disable-segment-training", action="store_true")
    parser.add_argument("--cutoff-hour-da", type=int, default=15)
    parser.add_argument("--cutoff-hour-rt", type=int, default=15)
    parser.add_argument("--target-mode", default="direct", choices=["residual_blend", "direct"])
    parser.add_argument("--da-target-mode", choices=["residual_blend", "direct"])
    parser.add_argument("--rt-target-mode", choices=["residual_blend", "direct"])
    parser.add_argument("--da-calibration-mode", default="none", choices=["none", "segment_bias", "segment_bias_shrink", "hour_bias"])
    parser.add_argument("--da-loss-mode", default="l1", choices=["l1", "asymmetric_under"])
    parser.add_argument("--da-under-weight-multiplier", type=float, default=1.25)
    parser.add_argument("--rt-calibration-mode", default="none", choices=["none", "segment_bias", "segment_bias_shrink", "hour_bias", "rt_916_affine", "rt_916_regime_affine", "rt_916_spike_day_affine", "rt_916_regime_affine_hourbias", "rt_916_peak_regime_affine", "rt_916_peak_regime_bias", "rt_916_auto"])
    parser.add_argument("--rt-loss-mode", default="l1", choices=["l1", "risk_hour_weighted", "risk_peak_weighted"])
    parser.add_argument("--rt-risk-profile", default="baseline", choices=["baseline", "solar_focus", "peak_focus"])
    parser.add_argument("--rt-peak-weight-multiplier", type=float, default=1.4)
    parser.add_argument("--rt-normal-focus-multiplier", type=float, default=1.2)
    parser.add_argument("--calibration-shrink", type=float, default=0.5)
    parser.add_argument("--affine-clip-min", type=float, default=0.7)
    parser.add_argument("--affine-clip-max", type=float, default=1.3)
    parser.add_argument("--regime-solar-ratio-threshold", type=float, default=0.28)
    parser.add_argument("--regime-bidding-ratio-threshold", type=float, default=0.08)
    parser.add_argument("--regime-bidding-space-threshold", type=float, default=4000.0)
    parser.add_argument("--peak-da-threshold", type=float, default=300.0)
    parser.add_argument("--peak-bidding-space-threshold", type=float, default=22000.0)
    parser.add_argument("--peak-solar-ratio-max", type=float, default=0.22)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--append-leaderboard", action="store_true")
    args = parser.parse_args()

    suffix_parts = [args.month, args.pipeline_mode, args.backbone]
    if args.training_mode != "rolling":
        suffix_parts.append(args.training_mode)
    if args.rt_loss_mode != "l1":
        suffix_parts.append(args.rt_loss_mode)
    if args.rt_risk_profile != "baseline":
        suffix_parts.append(args.rt_risk_profile)
    if args.da_calibration_mode != "none":
        suffix_parts.append(f"da-{args.da_calibration_mode}")
    if args.rt_calibration_mode != "none":
        suffix_parts.append(f"rt-{args.rt_calibration_mode}")
    output_dir = Path(args.output_root) / "_".join(suffix_parts)
    cfg = RunConfig(
        data_path=args.data_path,
        output_dir=str(output_dir),
        month=args.month,
        test_start=args.test_start,
        test_end_exclusive=args.test_end_exclusive,
        pipeline_mode=args.pipeline_mode,
        backbone=args.backbone,
        rt_916_backbone=args.rt_916_backbone,
        train_months=args.train_months,
        training_mode=args.training_mode,
        frozen_train_start=args.frozen_train_start,
        frozen_train_end_exclusive=args.frozen_train_end_exclusive,
        decomposition_mode=args.decomposition_mode,
        seq_len=args.seq_len,
        epochs=args.epochs,
        batch_size=args.batch_size,
        hidden_dim=args.hidden_dim,
        blocks=args.blocks,
        scales=args.scales,
        dropout=args.dropout,
        rt_segment_head_mode=args.rt_segment_head_mode,
        lr=args.lr,
        weight_decay=args.weight_decay,
        patience=args.patience,
        seed=args.seed,
        device=args.device,
        cutoff_hour_da=args.cutoff_hour_da,
        cutoff_hour_rt=args.cutoff_hour_rt,
        segment_training=not args.disable_segment_training,
        target_mode=args.target_mode,
        da_target_mode=args.da_target_mode,
        rt_target_mode=args.rt_target_mode,
        da_calibration_mode=args.da_calibration_mode,
        da_loss_mode=args.da_loss_mode,
        da_under_weight_multiplier=args.da_under_weight_multiplier,
        rt_calibration_mode=args.rt_calibration_mode,
        rt_loss_mode=args.rt_loss_mode,
        rt_risk_profile=args.rt_risk_profile,
        rt_peak_weight_multiplier=args.rt_peak_weight_multiplier,
        rt_normal_focus_multiplier=args.rt_normal_focus_multiplier,
        calibration_shrink=args.calibration_shrink,
        affine_clip_min=args.affine_clip_min,
        affine_clip_max=args.affine_clip_max,
        regime_solar_ratio_threshold=args.regime_solar_ratio_threshold,
        regime_bidding_ratio_threshold=args.regime_bidding_ratio_threshold,
        regime_bidding_space_threshold=args.regime_bidding_space_threshold,
        peak_da_threshold=args.peak_da_threshold,
        peak_bidding_space_threshold=args.peak_bidding_space_threshold,
        peak_solar_ratio_max=args.peak_solar_ratio_max,
        append_leaderboard=args.append_leaderboard,
    )
    result = run_monthly_reproduction(cfg)
    print(f"Outputs saved to: {result['output_dir']}")


if __name__ == "__main__":
    main()
