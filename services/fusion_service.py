from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_formal_fusion(
    *,
    target: str,
    start_date: str,
    end_date: str,
    work_dir: str,
    train_length_decision: str,
    weight_lower_bound: float,
    weight_upper_bound: float,
    conda_env: str,
) -> Path:
    project_root = Path(__file__).resolve().parents[1]
    if target == "both":
        script_path = project_root / "fusion" / "run_full_fusion_suite.py"
    elif target == "dayahead":
        script_path = project_root / "fusion" / "run_dayahead_pipeline.py"
    elif target == "realtime":
        script_path = project_root / "fusion" / "run_realtime_pipeline.py"
    else:
        raise ValueError(f"Unsupported fusion target: {target}")

    cmd = [
        sys.executable,
        str(script_path),
        "--target-start",
        start_date,
        "--target-end",
        end_date,
        "--work-dir",
        work_dir,
        "--train-length-decision",
        train_length_decision,
        "--weight-lower-bound",
        str(weight_lower_bound),
        "--weight-upper-bound",
        str(weight_upper_bound),
    ]
    if conda_env:
        cmd.extend(["--conda-env", str(conda_env)])
    subprocess.run(cmd, check=True, cwd=project_root)
    return Path(work_dir)
