from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fusion.project_defaults import DEFAULTS
from lightGBM.main_fix import run_lgbm_pipeline


TARGET_MAP = {
    "dayahead": "日前电价",
    "realtime": "实时电价",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run LightGBM and export project-standard raw output CSV.")
    parser.add_argument("--task", required=True, choices=["dayahead", "realtime"])
    parser.add_argument("--forecast-start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--forecast-end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--data-path", default=str(DEFAULTS.data_xlsx))
    parser.add_argument("--output", default=None, help="CSV path for raw LightGBM output.")
    parser.add_argument("--manifest", default=None, help="Optional manifest JSON path for this export.")
    parser.add_argument("--use-predicted-temp", action="store_true", help="Enable recursive realtime temperature fill mode.")
    parser.add_argument("--training-months", type=int, default=12, help="Fixed rolling training window in months.")
    parser.add_argument("--val-ratio", type=float, default=0.2, help="Chronological validation ratio inside the training history window.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    output = Path(args.output) if args.output else DEFAULTS.lightgbm_output / f"lightgbm_{args.task}.csv"
    output.parent.mkdir(parents=True, exist_ok=True)

    # The current LightGBM project entrypoint still uses its internal search logic.
    # Keep these values visible through env for future protocol convergence while
    # avoiding unsupported kwargs that would crash the wrapper.
    os.environ["LGBM_FIXED_TRAINING_MONTHS"] = str(int(args.training_months))
    os.environ["LGBM_VAL_RATIO"] = str(float(args.val_ratio))

    df = run_lgbm_pipeline(
        data_path=str(args.data_path),
        forecast_start=args.forecast_start,
        forecast_end=args.forecast_end,
        target=TARGET_MAP[args.task],
        use_predicted_temp=bool(args.use_predicted_temp),
        training_months=int(args.training_months),
        val_ratio=float(args.val_ratio),
    )
    if df is None or len(df) == 0:
        raise RuntimeError("LightGBM produced no rows.")
    df.to_csv(output, index=False, encoding="utf-8-sig")

    if args.manifest:
        manifest_path = Path(args.manifest)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model_name": "LightGBM",
            "task": args.task,
            "script_path": str((PROJECT_ROOT / "lightGBM" / "main_fix.py").resolve()),
            "runner_path": str(Path(__file__).resolve()),
            "data_path": str(Path(args.data_path).resolve()),
            "forecast_start": args.forecast_start,
            "forecast_end": args.forecast_end,
            "stable_output": str(output.resolve()),
            "training_months_requested": int(args.training_months),
            "val_ratio_requested": float(args.val_ratio),
            "use_predicted_temp": bool(args.use_predicted_temp),
            "protocol_note": "Current LightGBM backend is the project-local fixed-window reproduction path. Requested training_months and val_ratio are forwarded into run_lgbm_pipeline, but this remains distinct from the original epf dynamic-search entrypoint.",
        }
        manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
