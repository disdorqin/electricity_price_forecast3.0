from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run fusion collection and weight fitting in one command.")
    parser.add_argument("--manifest", required=True, help="Path to fusion manifest CSV or JSON.")
    parser.add_argument("--work-dir", required=True, help="Directory for normalized predictions and fitted weights.")
    parser.add_argument("--reg", type=float, default=0.1, help="Regularization strength toward prior weights.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    normalized_path = work_dir / "normalized_predictions.csv"
    fit_dir = work_dir / "weights"

    project_root = Path(__file__).resolve().parent.parent
    collect_script = project_root / "fusion" / "collect_predictions.py"
    fit_script = project_root / "fusion" / "run_fit.py"

    subprocess.run(
        [
            sys.executable,
            str(collect_script),
            "--manifest",
            str(args.manifest),
            "--output",
            str(normalized_path),
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(fit_script),
            "--input",
            str(normalized_path),
            "--output-dir",
            str(fit_dir),
            "--reg",
            str(args.reg),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
