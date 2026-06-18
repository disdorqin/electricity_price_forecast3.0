from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd


MONTHS = ["2026-02", "2026-03", "2026-04", "2026-05"]


def run_one(month: str, output_root: str) -> Path:
    cmd = [
        sys.executable,
        "fusion/runners/run_timemixer_export.py",
        "--month",
        month,
        "--pipeline-mode",
        "historical_joint",
        "--backbone",
        "timemixer",
        "--training-mode",
        "frozen",
        "--frozen-train-start",
        "2026-01-01",
        "--frozen-train-end-exclusive",
        "2026-02-01",
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
        "--output-root",
        output_root,
    ]
    subprocess.run(cmd, check=True)
    return Path(output_root) / f"{month}_historical_joint_timemixer_frozen_risk_peak_weighted_rt-rt_916_regime_affine"


def main() -> None:
    output_root = "fusion_runs/timemixer_frozen_batch"
    Path(output_root).mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    metric_tables: list[pd.DataFrame] = []
    for month in MONTHS:
        out_dir = run_one(month, output_root)
        metrics = pd.read_csv(out_dir / "metrics_by_period.csv")
        metrics.insert(0, "month", month)
        metric_tables.append(metrics)
        row = {"month": month, "output_dir": str(out_dir)}
        for task in ["da", "rt"]:
            for period in ["overall", "1_8", "9_16", "17_24"]:
                row[f"{task}_{period}"] = float(
                    metrics.loc[(metrics["task"] == task) & (metrics["period"] == period), "sMAPE"].iloc[0]
                )
        rows.append(row)

    summary = pd.DataFrame(rows)
    summary.to_csv(Path(output_root) / "monthly_summary.csv", index=False, encoding="utf-8-sig")
    pd.concat(metric_tables, ignore_index=True).to_csv(
        Path(output_root) / "metrics_all_months.csv",
        index=False,
        encoding="utf-8-sig",
    )
    aggregate = pd.DataFrame(
        [
            {
                "months": ",".join(MONTHS),
                "da_overall_mean": float(summary["da_overall"].mean()),
                "rt_overall_mean": float(summary["rt_overall"].mean()),
                "rt_1_8_mean": float(summary["rt_1_8"].mean()),
                "rt_9_16_mean": float(summary["rt_9_16"].mean()),
                "rt_17_24_mean": float(summary["rt_17_24"].mean()),
            }
        ]
    )
    aggregate.to_csv(Path(output_root) / "aggregate_summary.csv", index=False, encoding="utf-8-sig")
    (Path(output_root) / "batch_manifest.json").write_text(
        json.dumps(
            {
                "runner": "fusion/runners/run_timemixer_frozen_batch.py",
                "months": MONTHS,
                "training_mode": "frozen",
                "frozen_train_start": "2026-01-01",
                "frozen_train_end_exclusive": "2026-02-01",
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
