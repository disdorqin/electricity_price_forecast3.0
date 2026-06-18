from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EPF_ROOT = Path(r"D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\epf")
EPF_ENV_PATH = EPF_ROOT / ".env"
EPF_TF_ROOT = EPF_ROOT / "TF"
EPF_TF_SRC = EPF_TF_ROOT / "src"
DEFAULT_MODEL_DIR = Path(r"D:\作业\science\大创科研时序\代码\elec\models\timesFM")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run historical epf TF script and export a stable CSV.")
    parser.add_argument("--task", required=True, choices=["dayahead", "realtime"])
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--data-path", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-dir", default=str(DEFAULT_MODEL_DIR))
    return parser


def _read_effective_project_root() -> Path:
    if not EPF_ENV_PATH.exists():
        return EPF_ROOT
    for raw_line in EPF_ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != "PROJECT_ROOT":
            continue
        cleaned = value.strip().strip("'").strip('"')
        if cleaned:
            return Path(cleaned)
    return EPF_ROOT


def _ensure_model_dir(source_dir: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    required_files = ["config.json", "model.safetensors"]
    missing = [name for name in required_files if not (target_dir / name).exists()]
    if not missing:
        return
    if not source_dir.exists():
        raise FileNotFoundError(f"TimesFM source model dir missing: {source_dir}")
    shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)


def main() -> None:
    args = build_parser().parse_args()
    if not EPF_TF_ROOT.exists():
        raise FileNotFoundError(f"epf TF root missing: {EPF_TF_ROOT}")
    if not EPF_TF_SRC.exists():
        raise FileNotFoundError(f"epf TF src missing: {EPF_TF_SRC}")

    effective_root = _read_effective_project_root()
    effective_model_dir = effective_root / "models" / "timesFM"
    _ensure_model_dir(Path(args.model_dir), effective_model_dir)

    target_text = "日前" if args.task == "dayahead" else "实时"

    env = os.environ.copy()
    env["PROJECT_ROOT"] = str(effective_root)
    env["DATA_SET_NAME"] = str(Path(args.data_path).resolve())
    env.setdefault("PYTHONUTF8", "1")
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{EPF_TF_SRC.resolve()}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(EPF_TF_SRC.resolve())
    )

    script_path = EPF_TF_ROOT / "price_forecast_copy_分时段预测.py"
    cmd = [
        "conda",
        "run",
        "-n",
        "epf-2",
        "python",
        str(script_path),
        "--target",
        target_text,
        "--dump-csv",
        "--date-range",
        f"{args.start_date}~{args.end_date}",
        "--data",
        str(Path(args.data_path).resolve()),
    ]
    subprocess.run(cmd, check=True, cwd=str(EPF_TF_ROOT), env=env)

    source = effective_root / "output" / f"backtest_{args.task}.csv"
    if not source.exists():
        raise FileNotFoundError(f"Expected epf TF output missing: {source}")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)


if __name__ == "__main__":
    main()
