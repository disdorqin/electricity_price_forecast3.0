"""Official training-length reproduction entrypoint for Fusion V1.

Use this script when you need to regenerate the single-model reproduction
evidence that selects the formal training length for fusion.

This sits above the low-level model runners. It is the documented control
entry for train-length selection, not just an internal utility.
Before changing workflow assumptions here, read:

- `START_HERE.md` at repo root
- `docs/PROJECT_ENTRYPOINTS.md`
- `docs/FUSION_V1_EXECUTION_LOCK.md`
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fusion.project_defaults import DEFAULTS
from fusion.repro_suite import (
    TARGET_MONTHS,
    TRAIN_MONTH_CHOICES,
    build_repro_jobs,
    job_artifacts_complete,
    run_repro_job,
    select_train_length,
    summarize_existing_job,
    write_job_artifacts,
)

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run monthly single-model reproductions to choose the formal training length.")
    parser.add_argument("--work-dir", default=str(DEFAULTS.fusion_runs_root / "repro_training_length"))
    parser.add_argument("--conda-env", default="epf-2")
    parser.add_argument("--data-path", default=str(DEFAULTS.data_xlsx))
    parser.add_argument("--months", default=",".join(TARGET_MONTHS))
    parser.add_argument("--train-months-list", default=",".join(str(x) for x in TRAIN_MONTH_CHOICES))
    parser.add_argument("--tasks", default="dayahead,realtime")
    parser.add_argument("--models", default="lightgbm,timesfm,timemixer,rt916,sgdfnet")
    parser.add_argument("--skip-existing", action="store_true")
    return parser


def _parse_csv_list(raw: str, *, cast=str) -> tuple:
    values = [item.strip() for item in str(raw).split(",") if item.strip()]
    return tuple(cast(item) for item in values)


def main() -> None:
    args = build_parser().parse_args()
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    data_path = Path(args.data_path)
    months = _parse_csv_list(args.months, cast=str)
    train_months = _parse_csv_list(args.train_months_list, cast=int)
    tasks = _parse_csv_list(args.tasks, cast=str)
    models = _parse_csv_list(args.models, cast=str)

    summary_rows: list[dict[str, object]] = []
    jobs = build_repro_jobs(
        work_dir,
        months=months,
        train_month_choices=train_months,
        tasks=tasks,
        models=models,
    )
    for job in jobs:
        if args.skip_existing and job_artifacts_complete(job):
            print(
                f"[repro-skip] model={job.model_key} task={job.task} month={job.month} train_months={job.train_months}",
                flush=True,
            )
            summary_rows.append(summarize_existing_job(job, data_path=data_path))
            continue
        print(f"[repro] model={job.model_key} task={job.task} month={job.month} train_months={job.train_months}", flush=True)
        raw_path = run_repro_job(job, conda_env=args.conda_env, cwd=PROJECT_ROOT)
        if not raw_path.exists():
            raise FileNotFoundError(f"Runner did not produce expected raw output: {raw_path}")
        summary_rows.append(summarize_existing_job(job, data_path=data_path))

    summary_df = pd.DataFrame(summary_rows).sort_values(["task", "model_name", "month", "train_months"]).reset_index(drop=True)
    summary_path = work_dir / "repro_training_length_summary.csv"
    summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")

    decision = select_train_length(summary_df)
    decision.update(
        {
            "target_months": list(months),
            "candidate_train_months": list(train_months),
            "summary_csv": str(summary_path),
        }
    )
    (work_dir / "repro_training_length_decision.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
