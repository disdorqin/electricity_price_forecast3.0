from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

print("[RUNNER] script loaded", flush=True)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fusion.project_defaults import DEFAULTS


TARGET_MAP = {
    "dayahead": "日前电价",
    "realtime": "实时电价",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run RT916 export into stable fusion CSV files.")
    parser.add_argument("--task", required=True, choices=["dayahead", "realtime"])
    parser.add_argument("--start", required=True, help="Inclusive start timestamp, for example 2026-02-01 01:00:00.")
    parser.add_argument("--end", required=True, help="Inclusive end timestamp, for example 2026-02-03 00:00:00.")
    parser.add_argument("--data-path", default=str(DEFAULTS.data_xlsx), help="Excel dataset path.")
    parser.add_argument("--output", default=None, help="Stable CSV output path.")
    parser.add_argument("--mode", default=None, choices=["run", "daily_backtest", "joint_da_rt"])
    parser.add_argument("--mod", default="all", choices=["all", "stage1", "stage2", "stage3"])
    parser.add_argument("--asof-hour", type=int, default=15)
    parser.add_argument("--retrain-daily", action="store_true")
    parser.add_argument("--train-months", type=int, default=None)
    parser.add_argument("--val-ratio", type=float, default=None)
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--patience", type=int, default=None)
    parser.add_argument("--num-workers", type=int, default=None)
    parser.add_argument("--init-model-root", default=None)
    return parser


def _load_core_module(data_path: str):
    os.environ["PROJECT_ROOT"] = str(PROJECT_ROOT)
    os.environ["DATA_SET_NAME"] = str(Path(data_path))

    rt916_src = PROJECT_ROOT / "RT916_SpikeFusionNet" / "src"
    if str(rt916_src) not in sys.path:
        sys.path.insert(0, str(rt916_src))

    # Pre-import torch to avoid CUDA init segfault when core imports it internally
    import torch
    torch.cuda.is_available()

    from rt916_spikefusionnet import core

    return core


def main() -> None:
    print("[RUNNER] main() started", flush=True)
    args = build_parser().parse_args()
    print("[RUNNER] args parsed", flush=True)
    if args.train_months is not None:
        os.environ["SPIKE_TRAIN_MONTHS"] = str(int(args.train_months))
    if args.val_ratio is not None:
        os.environ["SPIKE_VAL_RATIO"] = str(float(args.val_ratio))
    if args.epochs is not None:
        os.environ["SPIKE_EPOCHS"] = str(int(args.epochs))
    if args.patience is not None:
        os.environ["SPIKE_PATIENCE"] = str(int(args.patience))
    if args.num_workers is not None:
        os.environ["SPIKE_NUM_WORKERS"] = str(int(args.num_workers))
    if args.init_model_root:
        os.environ["SPIKE_INIT_MODEL_ROOT"] = str(Path(args.init_model_root))
    print("[RUNNER] loading core...", flush=True)
    core = _load_core_module(args.data_path)
    print("[RUNNER] core loaded", flush=True)

    output = Path(args.output) if args.output else DEFAULTS.rt916_output / args.task / f"rt916_{args.task}.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"[RUNNER] output={output}", flush=True)

    mode = args.mode
    if mode is None:
        mode = "daily_backtest" if args.task == "dayahead" else "joint_da_rt"
    print(f"[RUNNER] mode={mode}", flush=True)

    if mode == "run":
        result = core.run(
            target=TARGET_MAP[args.task],
            start_end_list=[args.start, args.end],
            mod=args.mod,
            asof_ts=None,
            enforce_asof_cutoff=True,
        )
    elif mode == "daily_backtest":
        result = core.run_daily_asof_backtest(
            target=TARGET_MAP[args.task],
            start_end_list=[args.start, args.end],
            mod=args.mod,
            asof_hour=int(args.asof_hour),
            retrain_daily=bool(args.retrain_daily),
        )
    else:
        print("[RUNNER] calling run_joint_da_rt_daily_backtest...", flush=True)
        result = core.run_joint_da_rt_daily_backtest(
            start_end_list=[args.start, args.end],
            mod=args.mod,
            asof_hour=int(args.asof_hour),
        )

    print(f"[RUNNER] result rows={len(result) if result is not None else 'None'}", flush=True)
    if result is None or len(result) == 0:
        raise RuntimeError("RT916 produced no rows.")
    result.to_csv(output, index=False, encoding="utf-8-sig")
    export_meta = {
        "output_csv": str(output),
        "init_model_root": str(args.init_model_root) if args.init_model_root else "",
        "model_root": str(core.CONFIG.get("SAVE_ROOT_DIR", "")),
        "predict_root": str(core.CONFIG.get("PREDICT_RESULT_DIR", "")),
        "run_id": str(core.CONFIG.get("RUN_ID", "")),
    }
    (output.parent / "export_meta.json").write_text(
        json.dumps(export_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
