from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import pandas as pd
import torch

from pipeline_timemixer import (
    add_common_columns,
    build_arrays,
    date_range_days,
    evaluate_metrics,
    load_data,
    plot_prediction,
    predict_timemixer,
    set_seed,
    train_timemixer,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Single-task TimeMixer pipeline for fusion export.")
    parser.add_argument("--task", required=True, choices=["dayahead", "realtime"])
    parser.add_argument("--data-path", default="shandong_data.csv")
    parser.add_argument("--test-start", required=True)
    parser.add_argument("--test-end-exclusive", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seq-len", type=int, default=168)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--hidden-dim", type=int, default=64)
    parser.add_argument("--blocks", type=int, default=2)
    parser.add_argument("--scales", type=int, default=3)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--train-months", type=int, default=12)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--init-checkpoint", default=None)
    parser.add_argument("--save-checkpoint", default=None)
    parser.add_argument("--num-workers", type=int, default=0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    set_seed(args.seed)
    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    elif args.device == "cuda" and not torch.cuda.is_available():
        print("requested_device=cuda but torch.cuda.is_available()=False; fallback=cpu")
        device = torch.device("cpu")
    else:
        device = torch.device(args.device)
    print(f"device={device}")

    test_start = pd.Timestamp(args.test_start)
    test_end = pd.Timestamp(args.test_end_exclusive)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 80)
    print(f"Single-task TimeMixer Pipeline [{args.task}]")
    print("=" * 80)
    print("[1] Loading data...")
    df = load_data(args.data_path)
    print(f"Data rows: {len(df)}")

    test_df = df[(df["ds"] >= test_start) & (df["ds"] < test_end)]
    expected_points = (test_end - test_start).days * 24
    actual_points = len(test_df)
    print("[2] Checking test window...")
    print(f"test_start={test_start}")
    print(f"test_end_exclusive={test_end}")
    print(f"expected_points={expected_points}")
    print(f"actual_points={actual_points}")
    if actual_points < expected_points * 0.8:
        raise ValueError(f"test window too incomplete: {actual_points}/{expected_points}")
    elif actual_points != expected_points:
        print(f"[WARN] test window incomplete: {actual_points}/{expected_points}, continuing with available data")

    train_start = max(
        df["ds"].min().normalize() + pd.Timedelta(days=8),
        test_start - pd.DateOffset(months=int(args.train_months)),
    )
    lookback_days = max(5, (test_start.normalize() - train_start.normalize()).days)
    val_days = max(1, int(round(lookback_days * float(args.val_ratio))))
    valid_start = max(train_start + pd.Timedelta(days=1), test_start - pd.Timedelta(days=val_days))
    train_days = date_range_days(train_start, valid_start)
    test_days = date_range_days(test_start, test_end)
    idx = df.set_index("ds")

    if args.task == "dayahead":
        print("[3] Building DA arrays...")
        da_past, da_future, da_y = build_arrays(df, train_days, "day_ahead_clearing_price", args.seq_len, is_rt=False)
        print(f"DA train samples={len(da_y)}")
        print("[4] Training DA...")
        da_bundle = train_timemixer(da_past, da_future, da_y, args, device)

        print("[5] Predicting DA...")
        da_test_past, da_test_future, _ = build_arrays(df, test_days, "day_ahead_clearing_price", args.seq_len, is_rt=False)
        da_preds = predict_timemixer(da_bundle, da_test_past, da_test_future, device, batch_size=args.batch_size)
        da_rows = []
        for target_day, pred in zip(test_days, da_preds):
            cur = df[(df["ds"] >= target_day) & (df["ds"] < target_day + pd.Timedelta(days=1))].copy()
            cutoff = target_day - pd.Timedelta(days=1) + pd.Timedelta(hours=23, minutes=59, seconds=59)
            out = add_common_columns(cur, target_day, cutoff, False, "")
            out["day_ahead_clearing_price"] = cur["day_ahead_clearing_price"].values
            out["pred_day_ahead_price"] = pred
            da_rows.append(out)
        da_model = pd.concat(da_rows, ignore_index=True)
        if len(da_model) < actual_points * 0.95:
            raise ValueError(f"DA model output row count too low: {len(da_model)} vs {actual_points}")
        elif len(da_model) != actual_points:
            print(f"[WARN] DA model output row count mismatch: {len(da_model)} vs {actual_points}, continuing")

        print("[6] Building DA baselines...")
        da_base_rows = []
        for target_day in test_days:
            cur = df[(df["ds"] >= target_day) & (df["ds"] < target_day + pd.Timedelta(days=1))].copy()
            cutoff = target_day - pd.Timedelta(days=1) + pd.Timedelta(hours=23, minutes=59, seconds=59)
            for baseline_name, shift_days in [("M_naive_D1_DA", 1), ("M_naive_D7_DA", 7)]:
                out = add_common_columns(cur, target_day, cutoff, True, baseline_name)
                out["day_ahead_clearing_price"] = cur["day_ahead_clearing_price"].values
                out["pred_day_ahead_price"] = idx.reindex(cur["ds"] - pd.Timedelta(days=shift_days))["day_ahead_clearing_price"].values
                da_base_rows.append(out)
        da_all = pd.concat([da_model, pd.concat(da_base_rows, ignore_index=True)], ignore_index=True)
        da_metrics = evaluate_metrics(da_all, "da")
        da_cols = ["ds", "target_day", "decision_day", "info_cutoff", "hour_physical", "hour_business", "period", "day_ahead_clearing_price", "pred_day_ahead_price", "model_name", "baseline_name", "is_baseline", "training_mode", "inference_mode", "rt_prediction_mode", "test_window_complete", "official_test"]
        da_all[da_cols].to_csv(out_dir / "predictions_day_ahead_last_month.csv", index=False, encoding="utf-8-sig")
        da_metrics.to_csv(out_dir / "metrics_day_ahead_by_period.csv", index=False, encoding="utf-8-sig")
        if args.save_checkpoint:
            checkpoint_payload = {
                "task": "dayahead",
                "bundle": da_bundle,
            }
            save_path = Path(args.save_checkpoint)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            torch.save(checkpoint_payload, save_path)
            (save_path.with_suffix(".json")).write_text(
                json.dumps({"task": "dayahead", "path": str(save_path)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        plot_prediction(da_all, "day_ahead_clearing_price", "pred_day_ahead_price", out_dir, "day_ahead_prediction_vs_actual")
        print(da_metrics.to_string(index=False))
        return

    print("[3] Building RT arrays...")
    rt_past, rt_future, rt_y = build_arrays(df, train_days, "realtime_price", args.seq_len, is_rt=True, pred_da_map=None)
    print(f"RT train samples={len(rt_y)}")
    print("[4] Training RT...")
    rt_bundle = train_timemixer(rt_past, rt_future, rt_y, args, device)

    rt_pred_da_map = df.set_index("ds")["day_ahead_clearing_price"].to_dict()
    print("[5] Predicting RT...")
    rt_test_past, rt_test_future, _ = build_arrays(df, test_days, "realtime_price", args.seq_len, is_rt=True, pred_da_map=rt_pred_da_map)
    rt_preds = predict_timemixer(rt_bundle, rt_test_past, rt_test_future, device, batch_size=args.batch_size)
    rt_rows = []
    for target_day, pred in zip(test_days, rt_preds):
        cur = df[(df["ds"] >= target_day) & (df["ds"] < target_day + pd.Timedelta(days=1))].copy()
        cutoff = target_day - pd.Timedelta(days=1) + pd.Timedelta(hours=15)
        out = add_common_columns(cur, target_day, cutoff, False, "")
        out["realtime_price"] = cur["realtime_price"].values
        out["day_ahead_clearing_price"] = cur["day_ahead_clearing_price"].values
        out["pred_day_ahead_price"] = [rt_pred_da_map[x] for x in cur["ds"]]
        out["pred_realtime_price"] = pred
        out["traded"] = (out["pred_realtime_price"] > out["day_ahead_clearing_price"]).astype(int)
        out["profit_per_mwh"] = out["traded"] * (out["realtime_price"] - out["day_ahead_clearing_price"])
        rt_rows.append(out)
    rt_model = pd.concat(rt_rows, ignore_index=True)
    if len(rt_model) < actual_points * 0.95:
        raise ValueError(f"RT model output row count too low: {len(rt_model)} vs {actual_points}")
    elif len(rt_model) != actual_points:
        print(f"[WARN] RT model output row count mismatch: {len(rt_model)} vs {actual_points}, continuing")

    print("[6] Building RT baseline...")
    rt_base_rows = []
    for target_day in test_days:
        cur = df[(df["ds"] >= target_day) & (df["ds"] < target_day + pd.Timedelta(days=1))].copy()
        cutoff = target_day - pd.Timedelta(days=1) + pd.Timedelta(hours=15)
        out = add_common_columns(cur, target_day, cutoff, True, "M_naive_D7_RT")
        out["realtime_price"] = cur["realtime_price"].values
        out["day_ahead_clearing_price"] = cur["day_ahead_clearing_price"].values
        out["pred_day_ahead_price"] = [rt_pred_da_map[x] for x in cur["ds"]]
        out["pred_realtime_price"] = idx.reindex(cur["ds"] - pd.Timedelta(days=7))["realtime_price"].values
        out["traded"] = (out["pred_realtime_price"] > out["day_ahead_clearing_price"]).astype(int)
        out["profit_per_mwh"] = out["traded"] * (out["realtime_price"] - out["day_ahead_clearing_price"])
        rt_base_rows.append(out)
    rt_all = pd.concat([rt_model, pd.concat(rt_base_rows, ignore_index=True)], ignore_index=True)
    rt_metrics = evaluate_metrics(rt_all, "rt")
    rt_cols = ["ds", "target_day", "decision_day", "info_cutoff", "hour_physical", "hour_business", "period", "realtime_price", "day_ahead_clearing_price", "pred_day_ahead_price", "pred_realtime_price", "rt_prediction_mode", "traded", "profit_per_mwh", "model_name", "baseline_name", "is_baseline", "training_mode", "inference_mode", "test_window_complete", "official_test"]
    rt_all[rt_cols].to_csv(out_dir / "predictions_realtime_last_month.csv", index=False, encoding="utf-8-sig")
    rt_metrics.to_csv(out_dir / "metrics_realtime_by_period.csv", index=False, encoding="utf-8-sig")
    if args.save_checkpoint:
        checkpoint_payload = {
            "task": "realtime",
            "bundle": rt_bundle,
        }
        save_path = Path(args.save_checkpoint)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(checkpoint_payload, save_path)
        (save_path.with_suffix(".json")).write_text(
            json.dumps({"task": "realtime", "path": str(save_path)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    plot_prediction(rt_all, "realtime_price", "pred_realtime_price", out_dir, "realtime_prediction_vs_actual")
    print(rt_metrics.to_string(index=False))


if __name__ == "__main__":
    main()
