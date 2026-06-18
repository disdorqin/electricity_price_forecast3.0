"""Official TimeMixer export runner used by the current fusion path.

This is the active fusion-facing export bridge for TimeMixer.
It delegates to `TimeMixer/enhanced_pipeline.py` and is the correct runner
for the current preferred candidate path.

Drop-in replacement for ``run_timemixer_export.py`` that delegates to
``enhanced_pipeline.py`` instead of the original monolithic pipeline.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fusion.project_defaults import DEFAULTS


TASK_TO_FILENAME = {
    "dayahead": "predictions_day_ahead_last_month.csv",
    "realtime": "predictions_realtime_last_month.csv",
}

ENHANCED_SCRIPT = PROJECT_ROOT / "TimeMixer" / "enhanced_pipeline.py"
ARCHIVED_ENHANCED_SCRIPT = PROJECT_ROOT / "TimeMixer" / "_archive" / "enhanced_pipeline.py"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Enhanced Segmented TimeMixer export into fusion output folder."
    )
    parser.add_argument("--task", required=True, choices=["dayahead", "realtime"])
    parser.add_argument("--test-start", required=True, help="Inclusive start, YYYY-MM-DD.")
    parser.add_argument("--test-end-exclusive", required=True, help="Exclusive end, YYYY-MM-DD.")
    parser.add_argument("--data-path", default=str(DEFAULTS.data_csv))
    parser.add_argument("--output-dir", default=str(DEFAULTS.timemixer_output))
    parser.add_argument("--output", default=None, help="Stable copy path for the task CSV.")
    parser.add_argument("--manifest", default=None)
    # Pass-through hyperparameters
    parser.add_argument("--seq-len", type=int, default=None)
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--hidden-dim", type=int, default=None)
    parser.add_argument("--blocks", type=int, default=None)
    parser.add_argument("--scales", type=int, default=None)
    parser.add_argument("--dropout", type=float, default=None)
    parser.add_argument("--lr", type=float, default=None)
    parser.add_argument("--weight-decay", type=float, default=None)
    parser.add_argument("--patience", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--device", default=None, choices=["auto", "cpu", "cuda"])
    parser.add_argument("--train-months", type=int, default=None)
    parser.add_argument("--val-ratio", type=float, default=None)
    parser.add_argument("--init-checkpoint", default=None)
    parser.add_argument("--init-checkpoint-dir", default=None)
    parser.add_argument("--save-checkpoint", default=None)
    parser.add_argument("--num-workers", type=int, default=None)
    parser.add_argument("--no-calibration", action="store_true")
    parser.add_argument("--no-cosine", action="store_true")
    # Phase 4 arguments
    parser.add_argument("--scaler-type", default=None, choices=["standard", "minmax"])
    parser.add_argument("--no-enhanced-model", action="store_true")
    parser.add_argument("--no-enhanced-loss", action="store_true")
    parser.add_argument("--clamp-low", type=float, default=None)
    parser.add_argument("--clamp-high", type=float, default=None)
    parser.add_argument("--candidate-config", default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    data_path = Path(args.data_path)
    if not data_path.is_absolute():
        data_path = (PROJECT_ROOT / data_path).resolve()
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = (PROJECT_ROOT / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    script_path = ENHANCED_SCRIPT if ENHANCED_SCRIPT.exists() else ARCHIVED_ENHANCED_SCRIPT
    if not script_path.exists():
        raise FileNotFoundError(f"Enhanced pipeline not found: {ENHANCED_SCRIPT} or {ARCHIVED_ENHANCED_SCRIPT}")

    command = [
        sys.executable,
        str(script_path),
        "--task", str(args.task),
        "--data-path", str(data_path),
        "--test-start", str(args.test_start),
        "--test-end-exclusive", str(args.test_end_exclusive),
        "--output-dir", str(output_dir),
    ]

    passthrough_fields = [
        "seq_len", "epochs", "batch_size", "hidden_dim", "blocks", "scales",
        "dropout", "lr", "weight_decay", "patience", "seed", "device",
        "train_months", "val_ratio", "init_checkpoint", "init_checkpoint_dir",
        "save_checkpoint", "num_workers", "scaler_type", "clamp_low", "clamp_high",
        "candidate_config",
    ]
    path_fields = {"data_path", "init_checkpoint", "init_checkpoint_dir", "save_checkpoint", "candidate_config"}
    for field in passthrough_fields:
        value = getattr(args, field, None)
        if value is None:
            continue
        if field in path_fields:
            candidate = Path(value)
            if not candidate.is_absolute():
                value = str((PROJECT_ROOT / candidate).resolve())
        command.extend([f"--{field.replace('_', '-')}", str(value)])

    if args.no_calibration:
        command.append("--no-calibration")
    if args.no_cosine:
        command.append("--no-cosine")
    if args.no_enhanced_model:
        command.append("--no-enhanced-model")
    if args.no_enhanced_loss:
        command.append("--no-enhanced-loss")

    env = os.environ.copy()
    timemixer_root = str((PROJECT_ROOT / "TimeMixer").resolve())
    env["PYTHONPATH"] = timemixer_root if not env.get("PYTHONPATH") else timemixer_root + os.pathsep + env["PYTHONPATH"]

    print(f"[run_timemixer_enhanced_export] cmd: {' '.join(command)}")
    subprocess.run(command, check=True, cwd=str(PROJECT_ROOT / "TimeMixer"), env=env)

    if args.output:
        selected = output_dir / TASK_TO_FILENAME[args.task]
        if not selected.exists():
            raise FileNotFoundError(f"Expected output missing: {selected}")
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(selected, target)

    if args.manifest:
        manifest_path = Path(args.manifest)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model_name": "TimeMixer",
            "variant": "enhanced_segmented_v2",
            "task": args.task,
            "script_path": str(script_path),
            "data_path": str(data_path),
            "test_start": args.test_start,
            "test_end_exclusive": args.test_end_exclusive,
            "output_dir": str(output_dir),
            "stable_output": str(Path(args.output).resolve()) if args.output else "",
            "scaler_type": args.scaler_type or "standard",
            "enhanced_model": not args.no_enhanced_model,
            "enhanced_loss": not args.no_enhanced_loss,
        }
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
