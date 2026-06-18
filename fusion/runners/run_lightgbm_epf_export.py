from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

import pandas as pd


EPF_ROOT = Path(r"D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\epf")
EPF_ENV_PATH = EPF_ROOT / ".env"
EPF_LGBM_ROOT = EPF_ROOT / "lightGBM"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run historical epf LightGBM script and export a stable CSV.")
    parser.add_argument("--task", required=True, choices=["dayahead", "realtime"])
    parser.add_argument("--forecast-start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--forecast-end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--data-path", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--use-predicted-temp", action="store_true")
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


def _normalize_output(df: pd.DataFrame, task: str) -> pd.DataFrame:
    if "pred_y" not in df.columns:
        raise RuntimeError("epf LightGBM output missing pred_y column.")
    work = df.copy()
    keep_cols = ["ds", "y", "pred_y"]
    if "target_day" in work.columns:
        keep_cols.append("target_day")
    if "best_window" in work.columns:
        keep_cols.append("best_window")
    if "use_predicted_temp" in work.columns:
        keep_cols.append("use_predicted_temp")
    work = work[keep_cols].copy()
    work = work.rename(columns={"ds": "时刻", "y": "真实值", "pred_y": "预测值"})
    if task == "dayahead":
        work["task"] = "dayahead"
    else:
        work["task"] = "realtime"
    return work


def main() -> None:
    args = build_parser().parse_args()
    if not EPF_LGBM_ROOT.exists():
        raise FileNotFoundError(f"epf LightGBM root missing: {EPF_LGBM_ROOT}")

    effective_root = _read_effective_project_root()
    env = os.environ.copy()
    env["PROJECT_ROOT"] = str(effective_root)
    env["DATA_SET_NAME"] = str(Path(args.data_path).resolve())
    env.setdefault("PYTHONUTF8", "1")

    task_text = "日前电价" if args.task == "dayahead" else "实时电价"
    script = [
        "conda",
        "run",
        "-n",
        "epf-2",
        "python",
        "-c",
        (
            "from lightGBM.main_fix import run_lgbm_pipeline; "
            f"res=run_lgbm_pipeline(r'{Path(args.data_path).resolve()}', "
            f"'{args.forecast_start}', '{args.forecast_end}', "
            f"target='{task_text}', use_predicted_temp={bool(args.use_predicted_temp)}); "
            "res.to_csv(r'"
            + str(Path(args.output).resolve()).replace("\\", "\\\\")
            + "', index=False, encoding='utf-8-sig')"
        ),
    ]
    subprocess.run(script, check=True, cwd=str(EPF_ROOT), env=env)

    raw_output = Path(args.output)
    if not raw_output.exists():
        raise FileNotFoundError(f"Expected epf LightGBM raw output missing: {raw_output}")
    df = pd.read_csv(raw_output, encoding="utf-8-sig")
    normalized = _normalize_output(df, args.task)
    normalized.to_csv(raw_output, index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
