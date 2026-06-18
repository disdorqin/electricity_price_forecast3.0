from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd


def run_one(month: str, base_run: str, alt_run: str, output_root: str, append_leaderboard: bool) -> Path:
    cmd = [
        sys.executable,
        "fusion/runners/run_timemixer_safe_fusion.py",
        "--month",
        month,
        "--base-run",
        base_run,
        "--alt-run",
        alt_run,
        "--output-root",
        output_root,
    ]
    if append_leaderboard:
        cmd.append("--append-leaderboard")
    subprocess.run(cmd, check=True)
    return Path(output_root) / f"{month}_safe_rt9_16_fusion"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="fusion_runs/timemixer_safe_fusion_batch")
    parser.add_argument("--append-leaderboard", action="store_true")
    parser.add_argument(
        "--config-json",
        default=json.dumps(
            [
                {
                    "month": "2026-02",
                    "base_run": "fusion_runs/timemixer_truth_window_da_asym_segaware/2026-02_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine",
                    "alt_run": "fusion_runs/timemixer_truth_window_rt916splitbackbone/2026-02_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine",
                },
                {
                    "month": "2026-03",
                    "base_run": "fusion_runs/timemixer_truth_window_da_asym_segaware/2026-03_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine",
                    "alt_run": "fusion_runs/timemixer_truth_window_rt916splitbackbone/2026-03_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine",
                },
                {
                    "month": "2026-04",
                    "base_run": "fusion_runs/timemixer_truth_window_da_asym_segaware/2026-04_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine",
                    "alt_run": "fusion_runs/timemixer_truth_window_rt916splitbackbone/2026-04_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine",
                },
            ],
            ensure_ascii=False,
        ),
    )
    args = parser.parse_args()

    config = json.loads(args.config_json)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    monthly_rows: list[dict[str, object]] = []
    monthly_metric_tables: list[pd.DataFrame] = []
    for item in config:
        out_dir = run_one(
            month=item["month"],
            base_run=item["base_run"],
            alt_run=item["alt_run"],
            output_root=str(output_root),
            append_leaderboard=args.append_leaderboard,
        )
        metrics = pd.read_csv(out_dir / "metrics_by_period.csv")
        metrics.insert(0, "month", item["month"])
        monthly_metric_tables.append(metrics)
        da_overall = float(metrics.loc[(metrics["task"] == "da") & (metrics["period"] == "overall"), "sMAPE"].iloc[0])
        rt_overall = float(metrics.loc[(metrics["task"] == "rt") & (metrics["period"] == "overall"), "sMAPE"].iloc[0])
        rt_916 = float(metrics.loc[(metrics["task"] == "rt") & (metrics["period"] == "9_16"), "sMAPE"].iloc[0])
        monthly_rows.append(
            {
                "month": item["month"],
                "output_dir": str(out_dir),
                "da_smape_overall": da_overall,
                "rt_smape_overall": rt_overall,
                "rt_smape_9_16": rt_916,
            }
        )

    monthly_summary = pd.DataFrame(monthly_rows)
    monthly_summary.to_csv(output_root / "monthly_summary.csv", index=False, encoding="utf-8-sig")
    pd.concat(monthly_metric_tables, ignore_index=True).to_csv(
        output_root / "metrics_all_months.csv",
        index=False,
        encoding="utf-8-sig",
    )

    aggregate = pd.DataFrame(
        [
            {
                "months": ",".join(monthly_summary["month"].astype(str).tolist()),
                "da_smape_overall_mean": float(monthly_summary["da_smape_overall"].mean()),
                "rt_smape_overall_mean": float(monthly_summary["rt_smape_overall"].mean()),
                "rt_smape_9_16_mean": float(monthly_summary["rt_smape_9_16"].mean()),
            }
        ]
    )
    aggregate.to_csv(output_root / "aggregate_summary.csv", index=False, encoding="utf-8-sig")
    (output_root / "batch_manifest.json").write_text(
        json.dumps(
            {
                "runner": "fusion/runners/run_timemixer_safe_fusion_batch.py",
                "output_root": str(output_root),
                "config": config,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Batch outputs saved to: {output_root}")


if __name__ == "__main__":
    main()
