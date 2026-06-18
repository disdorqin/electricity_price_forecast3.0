from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd


SEEDS = [42, 123, 456, 789, 2026]
MONTHS = ["2026-02", "2026-03", "2026-04", "2026-05"]


def run_one(seed: int, month: str, output_root: str) -> Path:
    cmd = [
        sys.executable,
        "fusion/runners/run_timemixer_export.py",
        "--month",
        month,
        "--pipeline-mode",
        "historical_joint",
        "--backbone",
        "timemixer",
        "--da-loss-mode",
        "asymmetric_under",
        "--da-under-weight-multiplier",
        "1.25",
        "--rt-loss-mode",
        "risk_peak_weighted",
        "--rt-peak-weight-multiplier",
        "1.4",
        "--rt-calibration-mode",
        "rt_916_regime_affine",
        "--seed",
        str(seed),
        "--output-root",
        output_root,
    ]
    subprocess.run(cmd, check=True)
    return Path(output_root) / f"{month}_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine"


def clip50_metrics(df: pd.DataFrame, task: str) -> pd.DataFrame:
    true_col = "day_ahead_clearing_price" if task == "da" else "realtime_price"
    rows = []
    for period in ["overall", "1_8", "9_16", "17_24"]:
        sub = df if period == "overall" else df[df["period"] == period]
        pred = sub["y_pred"].to_numpy(float).copy()
        true = sub[true_col].to_numpy(float).copy()
        pred[pred < 50] = 50
        true[true < 50] = 50
        denom = (abs(pred) + abs(true)) / 2
        smape = float((abs(pred - true) / denom).mean() * 100)
        mae = float(abs(pred - true).mean())
        mse = float(((pred - true) ** 2).mean())
        rows.append(
            {
                "task": task,
                "period": period,
                "n": len(sub),
                "MAE": mae,
                "MSE": mse,
                "RMSE": mse ** 0.5,
                "sMAPE": smape,
            }
        )
    return pd.DataFrame(rows)


def ensemble_month(paths: list[Path], out_dir: Path) -> pd.DataFrame:
    frames = []
    for seed, path in zip(SEEDS, paths):
        df = pd.read_csv(path / "predictions_raw.csv")
        df["seed"] = seed
        frames.append(df)
    all_preds = pd.concat(frames, ignore_index=True)
    group_cols = [c for c in all_preds.columns if c not in {"y_pred", "seed", "model_name"}]
    agg = all_preds.groupby(group_cols, dropna=False, as_index=False)["y_pred"].mean()
    agg["model_name"] = "TimeMixerMultiSeed"
    agg.to_csv(out_dir / "predictions_raw.csv", index=False, encoding="utf-8-sig")
    metrics = pd.concat(
        [
            clip50_metrics(agg[agg["task"] == "da"].copy(), "da"),
            clip50_metrics(agg[agg["task"] == "rt"].copy(), "rt"),
        ],
        ignore_index=True,
    )
    metrics.to_csv(out_dir / "metrics_by_period.csv", index=False, encoding="utf-8-sig")
    return metrics


def main() -> None:
    seed_root = Path("fusion_runs/timemixer_multiseed_runs")
    out_root = Path("fusion_runs/timemixer_multiseed_ensemble")
    seed_root.mkdir(parents=True, exist_ok=True)
    out_root.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict[str, object]] = []
    for month in MONTHS:
        month_paths: list[Path] = []
        for seed in SEEDS:
            out_dir = run_one(seed, month, str(seed_root / f"seed_{seed}"))
            month_paths.append(out_dir)
        month_out = out_root / f"{month}_multiseed"
        month_out.mkdir(parents=True, exist_ok=True)
        metrics = ensemble_month(month_paths, month_out)
        row = {"month": month, "output_dir": str(month_out)}
        for task in ["da", "rt"]:
            for period in ["overall", "1_8", "9_16", "17_24"]:
                row[f"{task}_{period}"] = float(
                    metrics.loc[(metrics["task"] == task) & (metrics["period"] == period), "sMAPE"].iloc[0]
                )
        summary_rows.append(row)

    summary = pd.DataFrame(summary_rows)
    summary.to_csv(out_root / "monthly_summary.csv", index=False, encoding="utf-8-sig")
    aggregate = pd.DataFrame(
        [
            {
                "months": ",".join(MONTHS),
                "da_overall_mean": float(summary["da_overall"].mean()),
                "rt_overall_mean": float(summary["rt_overall"].mean()),
                "rt_9_16_mean": float(summary["rt_9_16"].mean()),
            }
        ]
    )
    aggregate.to_csv(out_root / "aggregate_summary.csv", index=False, encoding="utf-8-sig")
    (out_root / "batch_manifest.json").write_text(
        json.dumps(
            {
                "runner": "fusion/runners/run_timemixer_multiseed_batch.py",
                "months": MONTHS,
                "seeds": SEEDS,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(summary.to_string(index=False))
    print(aggregate.to_string(index=False))


if __name__ == "__main__":
    main()
