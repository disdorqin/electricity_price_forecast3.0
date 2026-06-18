from __future__ import annotations

import argparse
import csv
from pathlib import Path

if __package__ in {None, ""}:
    import sys

    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from fusion.project_defaults import DEFAULTS
else:
    from .project_defaults import DEFAULTS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a project-specific fusion manifest with current default paths.")
    parser.add_argument("--output", required=True, help="Manifest CSV to write.")
    parser.add_argument(
        "--task",
        required=True,
        choices=["dayahead", "realtime"],
        help="Task to generate placeholder output paths for.",
    )
    return parser


def manifest_rows(task: str) -> list[dict[str, str]]:
    task_slug = "day_ahead" if task == "dayahead" else "realtime"
    rows = [
        {
            "adapter": "timesfm",
            "source": str(DEFAULTS.timesfm_output / f"backtest_{task}.csv"),
            "task": task,
        },
        {
            "adapter": "timemixer",
            "source": str(DEFAULTS.timemixer_output / f"predictions_{task_slug}_last_month.csv"),
            "task": task,
        },
    ]
    if task == "realtime":
        rows.append(
            {
                "adapter": "sgdfnet",
                "source": str(DEFAULTS.sgdfnet_output / "predictions.csv"),
                "task": "",
            }
        )
    else:
        rows.append(
            {
                "adapter": "rt916",
                "source": str(DEFAULTS.rt916_output / "dayahead" / "rt916_dayahead.csv"),
                "task": task,
            }
        )
    return rows


def main() -> None:
    args = build_parser().parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    rows = manifest_rows(args.task)
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["adapter", "source", "task"])
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
