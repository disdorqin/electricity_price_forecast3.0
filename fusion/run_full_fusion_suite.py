"""Official Fusion V1 full-suite entrypoint.

Use this script when you want the repository's formal fusion path:

1. run the `dayahead` and `realtime` task pipelines
2. save per-task outputs
3. save the joint report and suite summary

This is a control-layer entrypoint, not just a low-level helper.
Before changing workflow assumptions here, read:

- `START_HERE.md` at repo root
- `docs/PROJECT_ENTRYPOINTS.md`
- `docs/FUSION_V1_STATUS.md`
- `docs/FUSION_V1_EXECUTION_LOCK.md`
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fusion.coverage_utils import latest_complete_target_day, latest_cross_model_safe_target_day
from fusion.pipeline_common import (
    build_common_parser,
    save_joint_report,
    save_suite_summary,
    run_task_pipeline,
)


def build_parser() -> argparse.ArgumentParser:
    base = build_common_parser("full")
    parser = argparse.ArgumentParser(
        description="Run dayahead + realtime fusion pipelines and save a unified final report.",
        parents=[base],
        add_help=False,
    )
    parser.add_argument("--suite-name", default=None, help="Optional subfolder label under work-dir.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    started = time.time()

    requested_end = pd.Timestamp(args.target_end).normalize()
    latest_available_day = latest_complete_target_day(args.data_path_xlsx)
    latest_safe_day = latest_cross_model_safe_target_day(args.data_path_xlsx)
    effective_end = min(requested_end, latest_safe_day)
    if effective_end < pd.Timestamp(args.target_start).normalize():
        raise ValueError(
            f"Requested window starts after the latest complete target day in data: "
            f"start={args.target_start}, latest_cross_model_safe_target_day={latest_safe_day:%Y-%m-%d}"
        )
    args.target_end = effective_end.strftime("%Y-%m-%d")

    root_work_dir = Path(args.work_dir)
    if args.suite_name:
        root_work_dir = root_work_dir / args.suite_name
    root_work_dir.mkdir(parents=True, exist_ok=True)

    dayahead_dir = root_work_dir / "dayahead_run"
    realtime_dir = root_work_dir / "realtime_run"
    joint_dir = root_work_dir / "joint_report"

    print("[suite] phase=dayahead")
    original_work_dir = args.work_dir
    args.work_dir = str(dayahead_dir)
    run_task_pipeline("dayahead", args)

    print("[suite] phase=realtime")
    args.work_dir = str(realtime_dir)
    run_task_pipeline("realtime", args)

    print("[suite] phase=joint_report")
    save_joint_report(dayahead_dir, realtime_dir, joint_dir)
    save_suite_summary(
        dayahead_dir,
        realtime_dir,
        joint_dir,
        root_work_dir / "suite_metrics_summary.csv",
    )

    suite_summary = {
        "target_start": args.target_start,
        "target_end": args.target_end,
        "requested_target_end": requested_end.strftime("%Y-%m-%d"),
        "latest_complete_target_day": latest_available_day.strftime("%Y-%m-%d"),
        "latest_cross_model_safe_target_day": latest_safe_day.strftime("%Y-%m-%d"),
        "train_length_decision": args.train_length_decision,
        "dayahead_dir": str(dayahead_dir),
        "realtime_dir": str(realtime_dir),
        "joint_report_dir": str(joint_dir),
        "suite_metrics_summary": str(root_work_dir / "suite_metrics_summary.csv"),
        "runtime_seconds": round(time.time() - started, 2),
    }
    (root_work_dir / "suite_summary.json").write_text(
        json.dumps(suite_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    args.work_dir = original_work_dir


if __name__ == "__main__":
    main()
