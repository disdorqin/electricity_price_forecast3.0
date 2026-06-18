from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .contracts import infer_period
from .metrics import smape_floor50
from .project_defaults import DEFAULTS


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DAYAHEAD_MODELS = ("lightgbm", "timesfm", "timemixer")
REALTIME_MODELS = ("rt916", "sgdfnet", "timesfm", "timemixer")
TRAIN_MONTH_CHOICES = (6, 9, 12)
TARGET_MONTHS = ("2026-02", "2026-03", "2026-04")

MODEL_DISPLAY_NAMES = {
    "lightgbm": "LightGBM",
    "timesfm": "TimesFM",
    "timemixer": "TimeMixer",
    "rt916": "RT916_SpikeFusionNet",
    "sgdfnet": "SGDFNet",
}

MODEL_PROTOCOL_TAGS = {
    "lightgbm": "fixed_window_monthly_repro",
    "timesfm": "timesfm_monthly_repro",
    "timemixer": "timemixer_monthly_repro",
    "rt916": "D-1_15_joint_daily_backtest",
    "sgdfnet": "B_D15_cutoff_walk_forward",
}

MODEL_DEVICE = {
    "lightgbm": "cpu",
    "timesfm": "gpu",
    "timemixer": "gpu",
    "rt916": "gpu",
    "sgdfnet": "cpu",
}

HISTORICAL_REFERENCE: dict[tuple[str, str, str], float | None] = {
    # LightGBM dayahead: no monthly breakdown from epf 1.0; overall ~29-36% (Feb-May)
    ("LightGBM", "dayahead", "2026-02"): None,
    ("LightGBM", "dayahead", "2026-03"): None,
    ("LightGBM", "dayahead", "2026-04"): None,
    # TimesFM dayahead: no monthly breakdown from epf 1.0; overall ~29% (Feb-May)
    ("TimesFM", "dayahead", "2026-02"): None,
    ("TimesFM", "dayahead", "2026-03"): None,
    ("TimesFM", "dayahead", "2026-04"): None,
    # TimeMixer dayahead: overall 17.55%, no monthly breakdown available
    ("TimeMixer", "dayahead", "2026-02"): None,
    ("TimeMixer", "dayahead", "2026-03"): None,
    ("TimeMixer", "dayahead", "2026-04"): None,
    # RT916 realtime: monthly from RT916_SpikeFusionNet_2026JanMay_月度汇总.csv
    ("RT916_SpikeFusionNet", "realtime", "2026-02"): 30.91,
    ("RT916_SpikeFusionNet", "realtime", "2026-03"): 31.61,
    ("RT916_SpikeFusionNet", "realtime", "2026-04"): 22.98,
    # SGDFNet realtime: corrected no-leakage capped SMAPE from cutoff_safe CSV
    ("SGDFNet", "realtime", "2026-02"): 26.69,
    ("SGDFNet", "realtime", "2026-03"): 26.49,
    ("SGDFNet", "realtime", "2026-04"): 18.96,
    # TimesFM realtime: no monthly breakdown; overall ~45% (Feb-May)
    ("TimesFM", "realtime", "2026-02"): None,
    ("TimesFM", "realtime", "2026-03"): None,
    ("TimesFM", "realtime", "2026-04"): None,
    # TimeMixer realtime: overall 26.04%, no monthly breakdown
    ("TimeMixer", "realtime", "2026-02"): None,
    ("TimeMixer", "realtime", "2026-03"): None,
    ("TimeMixer", "realtime", "2026-04"): None,
}


@dataclass(frozen=True)
class ReproJob:
    model_key: str
    task: str
    month: str
    train_months: int
    start_day: str
    end_day: str
    output_dir: Path


def _preferred_python_executable() -> str:
    conda_prefix = (os.environ.get("CONDA_PREFIX") or "").strip()
    if conda_prefix:
        candidate = Path(conda_prefix) / "python.exe"
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _wrap_command(command: list[str], conda_env: str | None) -> list[str]:
    if not conda_env:
        return command
    active_name = (os.environ.get("CONDA_DEFAULT_ENV") or "").strip().lower()
    if active_name == str(conda_env).strip().lower():
        return command
    return ["conda", "run", "-n", conda_env, *command]


def month_to_range(month: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(f"{month}-01").normalize()
    end = (start + pd.offsets.MonthEnd(1)).normalize()
    return start, end


def build_repro_jobs(
    root_dir: Path,
    *,
    months: tuple[str, ...] | None = None,
    train_month_choices: tuple[int, ...] | None = None,
    tasks: tuple[str, ...] | None = None,
    models: tuple[str, ...] | None = None,
) -> list[ReproJob]:
    allowed_months = set(months or TARGET_MONTHS)
    allowed_train_months = set(train_month_choices or TRAIN_MONTH_CHOICES)
    allowed_tasks = set(tasks or ("dayahead", "realtime"))
    allowed_models = set(models or (*DAYAHEAD_MODELS, *REALTIME_MODELS))
    jobs: list[ReproJob] = []
    for month in TARGET_MONTHS:
        if month not in allowed_months:
            continue
        start, end = month_to_range(month)
        for train_months in TRAIN_MONTH_CHOICES:
            if train_months not in allowed_train_months:
                continue
            for model_key in DAYAHEAD_MODELS:
                if "dayahead" not in allowed_tasks or model_key not in allowed_models:
                    continue
                jobs.append(
                    ReproJob(
                        model_key=model_key,
                        task="dayahead",
                        month=month,
                        train_months=train_months,
                        start_day=start.strftime("%Y-%m-%d"),
                        end_day=end.strftime("%Y-%m-%d"),
                        output_dir=root_dir / "repro_runs" / month / f"{model_key}_{train_months}m_dayahead",
                    )
                )
            for model_key in REALTIME_MODELS:
                if "realtime" not in allowed_tasks or model_key not in allowed_models:
                    continue
                jobs.append(
                    ReproJob(
                        model_key=model_key,
                        task="realtime",
                        month=month,
                        train_months=train_months,
                        start_day=start.strftime("%Y-%m-%d"),
                        end_day=end.strftime("%Y-%m-%d"),
                        output_dir=root_dir / "repro_runs" / month / f"{model_key}_{train_months}m_realtime",
                    )
                )
    return jobs


def _raw_output_path(job: ReproJob) -> Path:
    return job.output_dir / "predictions_raw.csv"


def job_artifacts_complete(job: ReproJob) -> bool:
    return all(
        path.exists()
        for path in (
            job.output_dir / "predictions_raw.csv",
            job.output_dir / "metrics_by_period.csv",
            job.output_dir / "run_manifest.json",
        )
    )


def build_runner_command(job: ReproJob, *, conda_env: str | None = None) -> list[str]:
    output_path = _raw_output_path(job)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    python_exe = _preferred_python_executable()

    if job.model_key == "lightgbm":
        command = [
            python_exe,
            str(PROJECT_ROOT / "fusion" / "runners" / "run_lightgbm_export.py"),
            "--task",
            job.task,
            "--forecast-start",
            job.start_day,
            "--forecast-end",
            job.end_day,
            "--data-path",
            str(DEFAULTS.data_xlsx),
            "--output",
            str(output_path),
            "--training-months",
            str(job.train_months),
            "--val-ratio",
            "0.2",
        ]
    elif job.model_key == "timesfm":
        command = [
            python_exe,
            str(PROJECT_ROOT / "fusion" / "runners" / "run_timesfm_export.py"),
            "--task",
            job.task,
            "--start-date",
            job.start_day,
            "--end-date",
            job.end_day,
            "--data-path",
            str(DEFAULTS.data_xlsx),
            "--output",
            str(output_path),
        ]
    elif job.model_key == "timemixer":
        command = [
            python_exe,
            str(PROJECT_ROOT / "fusion" / "runners" / "run_timemixer_export.py"),
            "--task",
            job.task,
            "--pipeline-mode",
            "single_task",
            "--test-start",
            job.start_day,
            "--test-end-exclusive",
            (pd.Timestamp(job.end_day) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            "--train-months",
            str(job.train_months),
            "--val-ratio",
            "0.2",
            "--data-path",
            str(DEFAULTS.data_csv),
            "--output",
            str(output_path),
        ]
    elif job.model_key == "sgdfnet":
        command = [
            python_exe,
            str(PROJECT_ROOT / "fusion" / "runners" / "run_sgdfnet_export.py"),
            "--forecast-start",
            job.start_day,
            "--forecast-end",
            job.end_day,
            "--data-path",
            str(DEFAULTS.data_xlsx),
            "--output",
            str(output_path),
            "--train-lookback-days",
            str(job.train_months * 30),
            "--val-days",
            "18",
            "--train-min-rows",
            str(max(job.train_months * 30 * 24, 24)),
        ]
    elif job.model_key == "rt916":
        command = [
            python_exe,
            str(PROJECT_ROOT / "fusion" / "runners" / "run_rt916_export.py"),
            "--task",
            "realtime",
            "--start",
            f"{job.start_day} 01:00:00",
            "--end",
            f"{job.end_day} 23:00:00",
            "--data-path",
            str(DEFAULTS.data_xlsx),
            "--output",
            str(output_path),
            "--train-months",
            str(job.train_months),
            "--val-ratio",
            "0.2",
        ]
    else:
        raise KeyError(f"Unsupported model key: {job.model_key}")

    return _wrap_command(command, conda_env)


def run_repro_job(job: ReproJob, *, conda_env: str | None = None, cwd: Path | None = None) -> Path:
    command = build_runner_command(job, conda_env=conda_env)
    subprocess.run(command, cwd=str(cwd or PROJECT_ROOT), check=True)
    return _raw_output_path(job)


def _truth_frame(data_path: Path) -> pd.DataFrame:
    raw = pd.read_excel(data_path, usecols=["时刻", "日前电价", "实时电价"])
    raw["时刻"] = pd.to_datetime(raw["时刻"])
    ds = raw["时刻"]
    target_day = ds.dt.normalize().where(ds.dt.hour != 0, ds.dt.normalize() - pd.Timedelta(days=1))
    hour_business = ds.dt.hour.replace({0: 24}).astype(int)
    da = pd.DataFrame(
        {
            "task": "dayahead",
            "target_day": target_day.dt.strftime("%Y-%m-%d"),
            "ds": ds,
            "period": hour_business.map(infer_period),
            "hour_business": hour_business,
            "y_true": pd.to_numeric(raw["日前电价"], errors="coerce"),
        }
    )
    rt = pd.DataFrame(
        {
            "task": "realtime",
            "target_day": target_day.dt.strftime("%Y-%m-%d"),
            "ds": ds,
            "period": hour_business.map(infer_period),
            "hour_business": hour_business,
            "y_true": pd.to_numeric(raw["实时电价"], errors="coerce"),
        }
    )
    return pd.concat([da, rt], ignore_index=True).dropna(subset=["y_true"]).drop_duplicates(
        subset=["task", "target_day", "ds", "period", "hour_business"]
    )


def _find_prediction_column(df: pd.DataFrame, model_key: str, task: str) -> str:
    candidates_by_model = {
        ("lightgbm", "dayahead"): ["pred_y", "预测日前电价", "pred_dayahead_price", "y_pred"],
        ("lightgbm", "realtime"): ["pred_y", "预测实时电价", "pred_realtime_price", "y_pred"],
        ("timesfm", "dayahead"): ["预测值", "预测日前电价", "pred_day_ahead_price", "y_pred", "pred"],
        ("timesfm", "realtime"): ["预测值", "预测实时电价", "pred_realtime_price", "y_pred", "pred"],
        ("timemixer", "dayahead"): ["预测值", "预测日前电价", "pred_day_ahead_price", "predicted_dayahead_price", "y_pred", "pred"],
        ("timemixer", "realtime"): ["预测值", "预测实时电价", "pred_realtime_price", "predicted_realtime_price", "y_pred", "pred"],
        ("rt916", "realtime"): ["预测实时电价", "pred_realtime_price", "y_pred"],
        ("sgdfnet", "realtime"): ["rt_hat", "y_pred", "预测实时电价"],
    }
    for column in candidates_by_model.get((model_key, task), []):
        if column in df.columns:
            return column
    # Tightened fallback: match "预测电价" or "预测值" but NOT data columns
    # like "地方电厂总加预测值" or "直调负荷预测值" which are raw features.
    for column in df.columns:
        name = str(column)
        if "预测电价" in name or name == "预测值" or name.startswith("pred"):
            return column
    raise KeyError(f"Could not find prediction column for model={model_key}, task={task}.")


def normalize_predictions(raw_path: Path, *, model_key: str, task: str, data_path: Path) -> pd.DataFrame:
    df = pd.read_csv(raw_path)
    if "timestamp" in df.columns:
        ds = pd.to_datetime(df["timestamp"])
    elif "时刻" in df.columns:
        ds = pd.to_datetime(df["时刻"])
    elif "ds" in df.columns:
        ds = pd.to_datetime(df["ds"])
    else:
        raise KeyError(f"Missing timestamp column in {raw_path}")

    pred_col = _find_prediction_column(df, model_key, task)
    # Task-aware truth column lookup: prefer the correct price column for the task,
    # then fall back to generic names.
    if task == "realtime":
        _truth_candidates = ["真实值", "y_true", "实时电价", "realtime_price", "y", "日前电价"]
    else:
        _truth_candidates = ["真实值", "y_true", "日前电价", "day_ahead_clearing_price", "y", "实时电价"]
    true_col = None
    for candidate in _truth_candidates:
        if candidate in df.columns:
            true_col = candidate
            break
    target_day = ds.dt.normalize().where(ds.dt.hour != 0, ds.dt.normalize() - pd.Timedelta(days=1))
    hour_business = ds.dt.hour.replace({0: 24}).astype(int)

    normalized = pd.DataFrame(
        {
            "task": task,
            "model_name": MODEL_DISPLAY_NAMES[model_key],
            "target_day": target_day.dt.strftime("%Y-%m-%d"),
            "ds": ds,
            "period": hour_business.map(infer_period),
            "hour_business": hour_business,
            "y_pred": pd.to_numeric(df[pred_col], errors="coerce"),
        }
    )

    if true_col is not None:
        normalized["y_true"] = pd.to_numeric(df[true_col], errors="coerce")
        merged = normalized
    else:
        truth = _truth_frame(data_path)
        merged = normalized.merge(
            truth,
            on=["task", "target_day", "ds", "period", "hour_business"],
            how="left",
        )
    return merged.dropna(subset=["y_pred", "y_true"]).sort_values("ds").reset_index(drop=True)


def _segment_metrics(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for period in ("overall", "1_8", "9_16", "17_24"):
        sub = df if period == "overall" else df[df["period"] == period]
        if sub.empty:
            continue
        rows.append(
            {
                "period": period,
                "sample_count": int(len(sub)),
                "smape": float(smape_floor50(sub["y_true"].to_numpy(float), sub["y_pred"].to_numpy(float))),
                "mae": float((sub["y_true"] - sub["y_pred"]).abs().mean()),
            }
        )
    return pd.DataFrame(rows)


def write_job_artifacts(job: ReproJob, normalized: pd.DataFrame, *, data_path: Path, output_dir: Path | None = None) -> dict[str, object]:
    output_dir = output_dir or job.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    normalized.to_csv(output_dir / "predictions_raw.csv", index=False, encoding="utf-8-sig")
    metrics_df = _segment_metrics(normalized)
    metrics_df.to_csv(output_dir / "metrics_by_period.csv", index=False, encoding="utf-8-sig")

    start_ts, _ = month_to_range(job.month)
    train_end = start_ts - pd.Timedelta(days=1)
    train_start = start_ts - pd.DateOffset(months=job.train_months)
    total_train_days = max((train_end.normalize() - train_start.normalize()).days + 1, 1)
    val_days = max(int(round(total_train_days * 0.2)), 1)
    val_start = train_end - pd.Timedelta(days=val_days - 1)
    train_fit_end = val_start - pd.Timedelta(days=1)

    entry_script_map = {
        "lightgbm": PROJECT_ROOT / "fusion" / "runners" / "run_lightgbm_export.py",
        "timesfm": PROJECT_ROOT / "fusion" / "runners" / "run_timesfm_export.py",
        "timemixer": PROJECT_ROOT / "fusion" / "runners" / "run_timemixer_export.py",
        "rt916": PROJECT_ROOT / "fusion" / "runners" / "run_rt916_export.py",
        "sgdfnet": PROJECT_ROOT / "fusion" / "runners" / "run_sgdfnet_export.py",
    }

    manifest = {
        "model_name": MODEL_DISPLAY_NAMES[job.model_key],
        "task": job.task,
        "month": job.month,
        "train_months": int(job.train_months),
        "train_start": train_start.strftime("%Y-%m-%d"),
        "train_end": train_fit_end.strftime("%Y-%m-%d"),
        "val_start": val_start.strftime("%Y-%m-%d"),
        "val_end": train_end.strftime("%Y-%m-%d"),
        "test_start": job.start_day,
        "test_end": job.end_day,
        "entry_script": str(entry_script_map[job.model_key]),
        "data_path": str(data_path),
        "conda_env": "epf-2" if MODEL_DEVICE[job.model_key] == "gpu" else os.environ.get("CONDA_DEFAULT_ENV", ""),
        "device": MODEL_DEVICE[job.model_key],
        "protocol_tag": MODEL_PROTOCOL_TAGS[job.model_key],
        "notes": "Monthly reproduction artifact for train-length selection.",
    }
    (output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    metrics_map = {row["period"]: row for row in metrics_df.to_dict("records")}
    overall_smape = float(metrics_map.get("overall", {}).get("smape", float("nan")))
    historical_reference = HISTORICAL_REFERENCE.get((MODEL_DISPLAY_NAMES[job.model_key], job.task, job.month))
    delta_vs_historical = None if historical_reference is None else overall_smape - float(historical_reference)
    pass_flag = bool(overall_smape < 40.0)

    return {
        "model_name": MODEL_DISPLAY_NAMES[job.model_key],
        "task": job.task,
        "month": job.month,
        "train_months": int(job.train_months),
        "overall_smape": overall_smape,
        "smape_1_8": float(metrics_map.get("1_8", {}).get("smape", float("nan"))),
        "smape_9_16": float(metrics_map.get("9_16", {}).get("smape", float("nan"))),
        "smape_17_24": float(metrics_map.get("17_24", {}).get("smape", float("nan"))),
        "mae": float(metrics_map.get("overall", {}).get("mae", float("nan"))),
        "sample_count": int(metrics_map.get("overall", {}).get("sample_count", len(normalized))),
        "historical_reference_smape": historical_reference,
        "delta_vs_historical": delta_vs_historical,
        "pass_flag": pass_flag,
        "artifact_dir": str(output_dir),
    }


def select_train_length(summary_df: pd.DataFrame) -> dict[str, object]:
    observed_months = (
        sorted(summary_df["month"].dropna().astype(str).unique().tolist())
        if "month" in summary_df.columns
        else []
    )
    decision: dict[str, object] = {
        "dayahead_train_months": None,
        "realtime_train_months": None,
        "selection_rule": "Choose the train length with the best average stability and historical alignment across the completed reproduction months; require task-level split only if needed.",
        "evaluated_train_months": list(TRAIN_MONTH_CHOICES),
        "observed_months": observed_months,
        "task_details": {},
    }

    for task in ("dayahead", "realtime"):
        task_df = summary_df[summary_df["task"] == task].copy()
        grouped_rows: list[dict[str, object]] = []
        for train_months, group in task_df.groupby("train_months", sort=True):
            overall_mean = float(group["overall_smape"].mean())
            overall_std = float(group["overall_smape"].std(ddof=0)) if len(group) > 1 else 0.0
            hist_delta = group["delta_vs_historical"].dropna()
            mean_abs_hist_delta = float(hist_delta.abs().mean()) if not hist_delta.empty else None
            fail_count = int((~group["pass_flag"].astype(bool)).sum())
            score = overall_mean + 0.5 * overall_std + 0.25 * fail_count
            if mean_abs_hist_delta is not None:
                score += 0.2 * mean_abs_hist_delta
            grouped_rows.append(
                {
                    "train_months": int(train_months),
                    "overall_smape_mean": overall_mean,
                    "overall_smape_std": overall_std,
                    "mean_abs_hist_delta": mean_abs_hist_delta,
                    "fail_count": fail_count,
                    "score": score,
                }
            )
        grouped_rows = sorted(grouped_rows, key=lambda row: (row["score"], row["train_months"]))
        chosen = grouped_rows[0]["train_months"] if grouped_rows else None
        decision["task_details"][task] = {
            "candidates": grouped_rows,
            "chosen_train_months": chosen,
        }
        decision[f"{task}_train_months"] = chosen

    if decision["dayahead_train_months"] == decision["realtime_train_months"]:
        decision["unified_train_months"] = decision["dayahead_train_months"]
        decision["use_task_specific_train_months"] = False
    else:
        decision["unified_train_months"] = None
        decision["use_task_specific_train_months"] = True
    return decision


def summarize_existing_job(job: ReproJob, *, data_path: Path) -> dict[str, object]:
    normalized_path = job.output_dir / "predictions_raw.csv"
    normalized = normalize_predictions(normalized_path, model_key=job.model_key, task=job.task, data_path=data_path)
    return write_job_artifacts(job, normalized, data_path=data_path)
