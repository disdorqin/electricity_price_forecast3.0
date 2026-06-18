from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def load_pred(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"ds", "task", "period", "hour_business", "y_true", "y_pred"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in {path}: {sorted(missing)}")
    df["ds"] = pd.to_datetime(df["ds"])
    return df


def choose_rt_pred(base: pd.DataFrame, alt: pd.DataFrame) -> pd.DataFrame:
    merged = base.merge(
        alt[["ds", "task", "period", "hour_business", "y_pred"]],
        on=["ds", "task", "period", "hour_business"],
        how="left",
        suffixes=("_base", "_alt"),
    )
    fused = merged.copy()
    use_alt = (fused["task"] == "rt") & (fused["period"] == "9_16")
    fused["y_pred"] = fused["y_pred_base"]
    fused.loc[use_alt, "y_pred"] = fused.loc[use_alt, "y_pred_alt"]
    fused["model_name"] = "TimeMixerSafeFusion"
    keep_cols = list(base.columns)
    if "model_name" not in keep_cols:
        keep_cols.append("model_name")
    return fused[keep_cols]


def evaluate_metrics(pred_df: pd.DataFrame, task: str) -> pd.DataFrame:
    rows = []
    true_col = "day_ahead_clearing_price" if task == "da" else "realtime_price"
    for period in ["overall", "1_8", "9_16", "17_24"]:
        sub = pred_df if period == "overall" else pred_df[pred_df["period"] == period]
        pred = sub["y_pred"].to_numpy(float)
        true = sub[true_col].to_numpy(float)
        pred = pred.copy()
        true = true.copy()
        pred[pred < 50] = 50
        true[true < 50] = 50
        denom = (abs(pred) + abs(true)) / 2
        smape = float((abs(pred - true) / denom).mean() * 100)
        mae = float(abs(pred - true).mean())
        mse = float(((pred - true) ** 2).mean())
        rmse = mse ** 0.5
        rows.append({"task": task, "period": period, "n": len(sub), "MAE": mae, "MSE": mse, "RMSE": rmse, "sMAPE": smape})
    return pd.DataFrame(rows)


def plot_prediction(df: pd.DataFrame, out_dir: Path, task: str) -> None:
    plt.figure(figsize=(16, 5))
    plt.plot(df["ds"], df["y_true"], label="actual")
    plt.plot(df["ds"], df["y_pred"], label="TimeMixerSafeFusion_pred")
    plt.legend()
    plt.title(f"{task}_prediction_vs_actual")
    plt.tight_layout()
    plt.savefig(out_dir / f"{task}_prediction_vs_actual.png", dpi=160)
    plt.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", required=True)
    parser.add_argument("--base-run", required=True)
    parser.add_argument("--alt-run", required=True)
    parser.add_argument("--output-root", default="fusion_runs/timemixer_safe_fusion")
    parser.add_argument("--append-leaderboard", action="store_true")
    parser.add_argument("--leaderboard-path", default="TimeMixer/outputs_v2/serial_keepdrop/leaderboard.csv")
    args = parser.parse_args()

    base_dir = Path(args.base_run)
    alt_dir = Path(args.alt_run)
    base_df = load_pred(base_dir / "predictions_raw.csv")
    alt_df = load_pred(alt_dir / "predictions_raw.csv")
    fused = choose_rt_pred(base_df, alt_df)
    out_dir = Path(args.output_root) / f"{args.month}_safe_rt9_16_fusion"
    out_dir.mkdir(parents=True, exist_ok=True)
    fused.to_csv(out_dir / "predictions_raw.csv", index=False, encoding="utf-8-sig")
    metrics = pd.concat([evaluate_metrics(fused[fused["task"] == "da"], "da"), evaluate_metrics(fused[fused["task"] == "rt"], "rt")], ignore_index=True)
    metrics.to_csv(out_dir / "metrics_by_period.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(
        [
            {
                "severity": "info",
                "item": "fusion_rule",
                "detail": "RT 9_16 from alt_run; others from base_run",
            }
        ]
    ).to_csv(out_dir / "protocol_audit.csv", index=False, encoding="utf-8-sig")
    manifest = {
        "model_name": "TimeMixerSafeFusion",
        "month": args.month,
        "base_run": str(base_dir),
        "alt_run": str(alt_dir),
        "fusion_rule": "RT 9_16 from alt_run; others from base_run",
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    plot_prediction(fused[fused["task"] == "da"], out_dir, "da")
    plot_prediction(fused[fused["task"] == "rt"], out_dir, "rt")
    if args.append_leaderboard:
        leaderboard_path = Path(args.leaderboard_path)
        leaderboard_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": pd.Timestamp.now().isoformat(sep=" "),
            "month": args.month,
            "pipeline_mode": "fusion",
            "backbone": "safe_rt9_16_fusion",
            "output_dir": str(out_dir),
            "da_smape_overall": float(metrics.loc[(metrics["task"] == "da") & (metrics["period"] == "overall"), "sMAPE"].iloc[0]),
            "rt_smape_overall": float(metrics.loc[(metrics["task"] == "rt") & (metrics["period"] == "overall"), "sMAPE"].iloc[0]),
            "da_smape_17_24": float(metrics.loc[(metrics["task"] == "da") & (metrics["period"] == "17_24"), "sMAPE"].iloc[0]),
            "rt_smape_17_24": float(metrics.loc[(metrics["task"] == "rt") & (metrics["period"] == "17_24"), "sMAPE"].iloc[0]),
        }
        if leaderboard_path.exists():
            old = pd.read_csv(leaderboard_path)
            new_df = pd.concat([old, pd.DataFrame([row])], ignore_index=True)
        else:
            new_df = pd.DataFrame([row])
        new_df.to_csv(leaderboard_path, index=False, encoding="utf-8-sig")
    print(f"Outputs saved to: {out_dir}")


if __name__ == "__main__":
    main()
