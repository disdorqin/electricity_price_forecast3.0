from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd


MONTH_CONFIGS = [
    {
        "month": "2026-02",
        "test_start": "2026-02-24",
        "test_end_exclusive": "2026-06-01",
    },
    {
        "month": "2026-03",
        "test_start": None,
        "test_end_exclusive": None,
    },
    {
        "month": "2026-04",
        "test_start": None,
        "test_end_exclusive": None,
    },
]


def run_one(item: dict[str, str | None], output_root: str) -> Path:
    cmd = [
        sys.executable,
        "fusion/runners/run_timemixer_export.py",
        "--month",
        str(item["month"]),
        "--pipeline-mode",
        "historical_joint",
        "--backbone",
        "timemixer",
        "--da-loss-mode",
        "asymmetric_under",
        "--rt-loss-mode",
        "risk_peak_weighted",
        "--rt-calibration-mode",
        "rt_916_spike_day_affine",
        "--output-root",
        output_root,
    ]
    if item["test_start"]:
        cmd.extend(["--test-start", str(item["test_start"])])
    if item["test_end_exclusive"]:
        cmd.extend(["--test-end-exclusive", str(item["test_end_exclusive"])])
    subprocess.run(cmd, check=True)

    suffix = [str(item["month"]), "historical_joint", "timemixer", "risk_peak_weighted", "rt-rt_916_spike_day_affine"]
    return Path(output_root) / "_".join(suffix)


def main() -> None:
    output_root = "fusion_runs/timemixer_truth_window_rt916spikeday"
    Path(output_root).mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    metrics_tables: list[pd.DataFrame] = []
    for item in MONTH_CONFIGS:
        out_dir = run_one(item, output_root)
        metrics = pd.read_csv(out_dir / "metrics_by_period.csv")
        metrics.insert(0, "month", item["month"])
        metrics_tables.append(metrics)
        rows.append(
            {
                "month": item["month"],
                "output_dir": str(out_dir),
                "da_smape_overall": float(metrics.loc[(metrics["task"] == "da") & (metrics["period"] == "overall"), "sMAPE"].iloc[0]),
                "rt_smape_overall": float(metrics.loc[(metrics["task"] == "rt") & (metrics["period"] == "overall"), "sMAPE"].iloc[0]),
                "rt_smape_9_16": float(metrics.loc[(metrics["task"] == "rt") & (metrics["period"] == "9_16"), "sMAPE"].iloc[0]),
            }
        )

    summary = pd.DataFrame(rows)
    summary.to_csv(Path(output_root) / "monthly_summary.csv", index=False, encoding="utf-8-sig")
    pd.concat(metrics_tables, ignore_index=True).to_csv(
        Path(output_root) / "metrics_all_months.csv",
        index=False,
        encoding="utf-8-sig",
    )
    aggregate = pd.DataFrame(
        [
            {
                "months": ",".join(summary["month"].astype(str).tolist()),
                "da_smape_overall_mean": float(summary["da_smape_overall"].mean()),
                "rt_smape_overall_mean": float(summary["rt_smape_overall"].mean()),
                "rt_smape_9_16_mean": float(summary["rt_smape_9_16"].mean()),
            }
        ]
    )
    aggregate.to_csv(Path(output_root) / "aggregate_summary.csv", index=False, encoding="utf-8-sig")
    (Path(output_root) / "batch_manifest.json").write_text(
        json.dumps(
            {
                "runner": "fusion/runners/run_timemixer_rt916_spike_day_batch.py",
                "output_root": output_root,
                "config": MONTH_CONFIGS,
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
