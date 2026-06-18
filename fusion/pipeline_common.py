from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fusion.metrics import arbitrage_metrics, smape_floor50
from fusion.project_defaults import DEFAULTS
from fusion.run_fixed_window_fusion import _apply_fixed_weights, _fit_fixed_weights
from fusion.run_fixed_window_fusion import (
    ModelArtifact,
    _build_truth_frame,
    _final_result_table,
    _load_normalized_predictions,
)


@dataclass(frozen=True)
class WindowSpec:
    label: str
    start: pd.Timestamp
    end: pd.Timestamp
    train_months: int
    val_ratio: float


@dataclass(frozen=True)
class ModelRunSpec:
    name: str
    task: str
    device_group: str
    output: Path
    command: list[str]
    adapter: str
    adapter_kwargs: dict[str, object]


TASK_MODEL_POOL = {
    "dayahead": ["lightgbm", "timesfm", "timemixer"],
    "realtime": ["rt916", "sgdfnet", "timesfm", "timemixer"],
}


def _resolve_timemixer_candidate(args: argparse.Namespace) -> tuple[Path | None, str | None]:
    if args.timemixer_candidate_config:
        return Path(args.timemixer_candidate_config), "cli"
    if bool(getattr(args, "no_default_timemixer_candidate", False)):
        return None, None
    preferred = getattr(DEFAULTS, "preferred_timemixer_candidate", None)
    if preferred:
        preferred_path = Path(preferred)
        if preferred_path.exists():
            return preferred_path, "default"
    return None, None


def _preferred_python_executable() -> str:
    conda_prefix = (os.environ.get("CONDA_PREFIX") or "").strip()
    if conda_prefix:
        candidate = Path(conda_prefix) / "python.exe"
        if candidate.exists():
            return str(candidate)
    return sys.executable


def build_common_parser(task: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{task} fusion pipeline with meta learner and live progress logs.")
    parser.add_argument("--target-start", required=True, help="Inclusive prediction start date, YYYY-MM-DD.")
    parser.add_argument("--target-end", required=True, help="Inclusive prediction end date, YYYY-MM-DD.")
    parser.add_argument("--work-dir", required=True, help="Output directory for all artifacts.")
    parser.add_argument("--conda-env", default="epf-2", help="Conda env used to run model scripts.")
    parser.add_argument("--data-path-xlsx", default=str(DEFAULTS.data_xlsx))
    parser.add_argument("--data-path-csv", default=str(DEFAULTS.data_csv))
    parser.add_argument("--simulation-train-months", type=int, default=9)
    parser.add_argument("--formal-train-months", type=int, default=9)
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--meta-alpha", type=float, default=2.0)
    parser.add_argument("--reg-1-8", type=float, default=0.50)
    parser.add_argument("--reg-9-16", type=float, default=0.20)
    parser.add_argument("--reg-17-24", type=float, default=0.30)
    parser.add_argument("--weight-lower-bound", type=float, default=-0.5)
    parser.add_argument("--weight-upper-bound", type=float, default=1.2)
    parser.add_argument("--timemixer-device", default="cuda", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--timemixer-epochs", type=int, default=None)
    parser.add_argument("--timemixer-sim-epochs", type=int, default=30)
    parser.add_argument("--timemixer-formal-epochs", type=int, default=30)
    parser.add_argument("--timemixer-batch-size", type=int, default=None)
    parser.add_argument("--timemixer-num-workers", type=int, default=2)
    parser.add_argument(
        "--timemixer-candidate-config",
        default=None,
        help="Optional TimeMixer candidate config JSON passed through to the enhanced export runner.",
    )
    parser.add_argument(
        "--no-default-timemixer-candidate",
        action="store_true",
        help="Disable the preferred TimeMixer candidate config and use the stock enhanced configuration.",
    )
    parser.add_argument("--lightgbm-min-months", type=int, default=9)
    parser.add_argument("--disable-lightgbm", action="store_true")
    parser.add_argument("--sgdfnet-min-train-days", type=int, default=45)
    parser.add_argument("--sgdfnet-formal-lookback-days", type=int, default=270)
    parser.add_argument("--rt916-sim-epochs", type=int, default=30)
    parser.add_argument("--rt916-formal-epochs", type=int, default=30)
    parser.add_argument("--rt916-patience", type=int, default=6)
    parser.add_argument("--rt916-num-workers", type=int, default=2)
    parser.add_argument("--rt916-retrain-daily", action="store_true")
    parser.add_argument("--keep-phase-artifacts", action="store_true")
    parser.add_argument("--train-length-decision", default=None, help="Optional reproduction decision JSON to override train months.")
    return parser


def _active_conda_matches(conda_env: str | None) -> bool:
    if not conda_env:
        return False

    active_name = (os.environ.get("CONDA_DEFAULT_ENV") or "").strip()
    active_prefix = (os.environ.get("CONDA_PREFIX") or "").strip()
    requested = str(conda_env).strip()
    requested_lower = requested.lower()

    if active_name.lower() == requested_lower:
        return True
    if active_prefix:
        prefix_name = Path(active_prefix).name.lower()
        if prefix_name == requested_lower or active_prefix.lower() == requested_lower:
            return True
    return False


def _wrap_command(command: list[str], conda_env: str | None) -> list[str]:
    if not conda_env or _active_conda_matches(conda_env):
        return command
    return ["conda", "run", "-n", conda_env, *command]


def _stream_subprocess(command: list[str], *, conda_env: str | None, cwd: Path, prefix: str) -> None:
    wrapped = _wrap_command(command, conda_env)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    if wrapped[:3] == ["conda", "run", "-n"]:
        env.setdefault("CONDA_NO_PLUGINS", "true")
    process = subprocess.Popen(
        wrapped,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(f"[{prefix}] {line.rstrip()}", flush=True)
    code = process.wait()
    if code != 0:
        raise subprocess.CalledProcessError(code, wrapped)


def _run_specs_with_scheduler(specs: list[ModelRunSpec], *, conda_env: str | None) -> None:
    cpu_specs = [spec for spec in specs if spec.device_group == "cpu"]
    gpu_specs = [spec for spec in specs if spec.device_group == "gpu"]

    def run_one(spec: ModelRunSpec) -> None:
        spec.output.parent.mkdir(parents=True, exist_ok=True)
        print(f"[orchestrator] start {spec.task}:{spec.name} -> {spec.output}")
        _stream_subprocess(spec.command, conda_env=conda_env, cwd=PROJECT_ROOT, prefix=f"{spec.task}/{spec.name}")
        print(f"[orchestrator] done {spec.task}:{spec.name}")

    gpu_thread = None
    gpu_error: list[BaseException] = []

    def gpu_worker() -> None:
        try:
            for spec in gpu_specs:
                run_one(spec)
        except BaseException as exc:  # noqa: BLE001
            gpu_error.append(exc)

    if gpu_specs:
        gpu_thread = threading.Thread(target=gpu_worker, daemon=False)
        gpu_thread.start()

    if cpu_specs:
        with ThreadPoolExecutor(max_workers=min(len(cpu_specs), 2)) as executor:
            futures = [executor.submit(run_one, spec) for spec in cpu_specs]
            for future in futures:
                future.result()

    if gpu_thread:
        gpu_thread.join()
    if gpu_error:
        raise gpu_error[0]


def _days_inclusive(start: pd.Timestamp, end: pd.Timestamp) -> int:
    return int((end.normalize() - start.normalize()).days) + 1


def build_windows(target_start: str, target_end: str, simulation_train_months: int, formal_train_months: int, val_ratio: float) -> tuple[WindowSpec, WindowSpec]:
    target_start_ts = pd.Timestamp(target_start).normalize()
    target_end_ts = pd.Timestamp(target_end).normalize()
    # Simulation window = full validation portion of the training period.
    # With train_months=12, val_ratio=0.2 → ~72 days before target_start.
    # All simulation days will be used for weight fitting (no further split).
    total_train_days = pd.date_range(
        end=target_start_ts - pd.Timedelta(days=1),
        periods=int(simulation_train_months * 30),
        freq="D",
    )
    val_days = max(int(round(len(total_train_days) * val_ratio)), 1)
    simulation_start = (target_start_ts - pd.Timedelta(days=val_days)).normalize()
    simulation_end = target_start_ts - pd.Timedelta(days=1)
    simulation = WindowSpec("simulation", simulation_start, simulation_end, int(simulation_train_months), float(val_ratio))
    formal = WindowSpec("formal", target_start_ts, target_end_ts, int(formal_train_months), float(val_ratio))
    return simulation, formal


def _apply_train_length_decision(task: str, args: argparse.Namespace) -> None:
    decision_path = getattr(args, "train_length_decision", None)
    if not decision_path:
        return
    path = Path(decision_path)
    if not path.exists():
        raise FileNotFoundError(f"Train-length decision file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    unified = payload.get("unified_train_months")
    if unified:
        args.simulation_train_months = int(unified)
        args.formal_train_months = int(unified)
        return
    chosen = payload.get(f"{task}_train_months")
    if chosen:
        args.simulation_train_months = int(chosen)
        args.formal_train_months = int(chosen)


def _sgdfnet_split(window: WindowSpec, min_train_days: int) -> tuple[int, int]:
    total_days = max(5, _days_inclusive(window.start - pd.DateOffset(months=window.train_months), window.start - pd.Timedelta(days=1)))
    val_days = max(1, int(round(total_days * window.val_ratio)))
    train_lookback_days = max(int(min_train_days), total_days - val_days)
    return train_lookback_days, val_days


def build_model_specs(
    task: str,
    window: WindowSpec,
    phase_dir: Path,
    args: argparse.Namespace,
    *,
    previous_phase_dir: Path | None = None,
) -> list[ModelRunSpec]:
    specs: list[ModelRunSpec] = []
    task_pool = TASK_MODEL_POOL[task]

    if "lightgbm" in task_pool and not bool(args.disable_lightgbm):
        output = phase_dir / "lightgbm" / f"{task}.csv"
        lightgbm_months = max(int(window.train_months), int(args.lightgbm_min_months))
        specs.append(
            ModelRunSpec(
                name="lightgbm",
                task=task,
                device_group="cpu",
                output=output,
                command=[
                    _preferred_python_executable(),
                    str(PROJECT_ROOT / "fusion" / "runners" / "run_lightgbm_export.py"),
                    "--task",
                    task,
                    "--forecast-start",
                    window.start.strftime("%Y-%m-%d"),
                    "--forecast-end",
                    window.end.strftime("%Y-%m-%d"),
                    "--data-path",
                    str(args.data_path_xlsx),
                    "--output",
                    str(output),
                    "--training-months",
                    str(lightgbm_months),
                    "--val-ratio",
                    str(window.val_ratio),
                ],
                adapter="lightgbm",
                adapter_kwargs={"task": task},
            )
        )

    if "timesfm" in task_pool:
        output = phase_dir / "timesfm" / f"backtest_{task}.csv"
        specs.append(
            ModelRunSpec(
                name="timesfm",
                task=task,
                device_group="gpu",
                output=output,
                command=[
                    _preferred_python_executable(),
                    str(PROJECT_ROOT / "fusion" / "runners" / "run_timesfm_export.py"),
                    "--task",
                    task,
                    "--start-date",
                    window.start.strftime("%Y-%m-%d"),
                    "--end-date",
                    window.end.strftime("%Y-%m-%d"),
                    "--data-path",
                    str(args.data_path_xlsx),
                    "--output",
                    str(output),
                ],
                adapter="timesfm",
                adapter_kwargs={"task": task, "data_path": str(args.data_path_xlsx)},
            )
        )

    if "timemixer" in task_pool:
        output_dir = phase_dir / "timemixer"
        filename = "predictions_day_ahead_last_month.csv" if task == "dayahead" else "predictions_realtime_last_month.csv"
        output = output_dir / filename
        checkpoint_path = output_dir / "timemixer_checkpoint.pt"
        command = [
            _preferred_python_executable(),
            str(PROJECT_ROOT / "fusion" / "runners" / "run_timemixer_enhanced_export.py"),
            "--task",
            task,
            "--test-start",
            window.start.strftime("%Y-%m-%d"),
            "--test-end-exclusive",
            (window.end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            "--data-path",
            str(args.data_path_csv),
            "--output-dir",
            str(output_dir),
            "--device",
            str(args.timemixer_device),
            "--train-months",
            str(window.train_months),
            "--val-ratio",
            str(window.val_ratio),
            "--save-checkpoint",
            str(checkpoint_path),
            "--num-workers",
            str(int(args.timemixer_num_workers)),
        ]
        epochs = int(
            args.timemixer_epochs
            if args.timemixer_epochs is not None
            else (args.timemixer_sim_epochs if window.label == "simulation" else args.timemixer_formal_epochs)
        )
        command.extend(["--epochs", str(epochs)])
        if args.timemixer_batch_size is not None:
            command.extend(["--batch-size", str(args.timemixer_batch_size)])
        candidate_config, _candidate_source = _resolve_timemixer_candidate(args)
        if candidate_config:
            command.extend(["--candidate-config", str(candidate_config)])
        if window.label == "formal" and previous_phase_dir is not None:
            init_checkpoint = previous_phase_dir / "timemixer" / "timemixer_checkpoint.pt"
            if init_checkpoint.exists():
                command.extend(["--init-checkpoint", str(init_checkpoint)])
        specs.append(
            ModelRunSpec(
                name="timemixer",
                task=task,
                device_group="gpu",
                output=output,
                command=command,
                adapter="timemixer",
                adapter_kwargs={"task": task},
            )
        )

    if "sgdfnet" in task_pool:
        output = phase_dir / "sgdfnet" / "predictions.csv"
        train_lookback_days, val_days = _sgdfnet_split(window, int(args.sgdfnet_min_train_days))
        if window.label == "formal":
            train_lookback_days = max(int(args.sgdfnet_formal_lookback_days), train_lookback_days)
        specs.append(
            ModelRunSpec(
                name="sgdfnet",
                task=task,
                device_group="cpu",
                output=output,
                command=[
                    _preferred_python_executable(),
                    str(PROJECT_ROOT / "fusion" / "runners" / "run_sgdfnet_export.py"),
                    "--forecast-start",
                    window.start.strftime("%Y-%m-%d"),
                    "--forecast-end",
                    window.end.strftime("%Y-%m-%d"),
                    "--data-path",
                    str(args.data_path_xlsx),
                    "--output",
                    str(output),
                    "--val-days",
                    str(val_days),
                    "--train-lookback-days",
                    str(train_lookback_days),
                    "--train-min-rows",
                    str(max(24 * int(args.sgdfnet_min_train_days), 24)),
                ],
                adapter="sgdfnet",
                adapter_kwargs={},
            )
        )

    if "rt916" in task_pool:
        output = phase_dir / "rt916" / "realtime" / "rt916_realtime.csv"
        command = [
            _preferred_python_executable(),
            str(PROJECT_ROOT / "fusion" / "runners" / "run_rt916_export.py"),
            "--task",
            "realtime",
            "--start",
            f"{window.start:%Y-%m-%d} 01:00:00",
            "--end",
            f"{(window.end + pd.Timedelta(days=1)):%Y-%m-%d} 00:00:00",
            "--data-path",
            str(args.data_path_xlsx),
            "--output",
            str(output),
            "--train-months",
            str(window.train_months),
            "--val-ratio",
            str(window.val_ratio),
            "--epochs",
            str(int(args.rt916_sim_epochs if window.label == "simulation" else args.rt916_formal_epochs)),
            "--patience",
            str(int(args.rt916_patience)),
            "--num-workers",
            str(int(args.rt916_num_workers)),
        ]
        if window.label == "formal" and previous_phase_dir is not None:
            export_meta = previous_phase_dir / "rt916" / "realtime" / "export_meta.json"
            if export_meta.exists():
                try:
                    export_data = json.loads(export_meta.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    export_data = {}
                actual_model_root = export_data.get("model_root")
                if actual_model_root:
                    command.extend(["--init-model-root", str(actual_model_root)])
        specs.append(
            ModelRunSpec(
                name="rt916",
                task=task,
                device_group="gpu",
                output=output,
                command=command,
                adapter="rt916",
                adapter_kwargs={"task": "realtime"},
            )
        )

    return specs


def _overwrite_truth_from_source(normalized: pd.DataFrame, data_path_xlsx: str) -> pd.DataFrame:
    truth = _build_truth_frame(data_path_xlsx)
    pred_only = normalized.drop(columns=["y_true"])
    return pred_only.merge(
        truth,
        on=["task", "target_day", "ds", "period", "hour_business"],
        how="inner",
    )


def collect_phase_predictions(phase_dir: Path, specs: list[ModelRunSpec], data_path_xlsx: str, label: str) -> pd.DataFrame:
    artifacts = [
        ModelArtifact(
            model_name=spec.name if spec.name != "lightgbm" else "lightGBM",
            adapter=spec.adapter,
            source=spec.output,
            task=spec.task,
            adapter_kwargs=spec.adapter_kwargs,
        )
        for spec in specs
    ]
    normalized = _load_normalized_predictions(artifacts)
    normalized = _overwrite_truth_from_source(normalized, data_path_xlsx)
    normalized.to_csv(phase_dir / f"{label}_predictions_long.csv", index=False, encoding="utf-8-sig")
    return normalized


def period_summary(df: pd.DataFrame, *, task: str, y_true_col: str = "y_true", y_pred_col: str = "y_fused") -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for period in ["overall", "1_8", "9_16", "17_24"]:
        sub = df if period == "overall" else df[df["period"] == period]
        if sub.empty:
            continue
        rows.append(
            {
                "task": task,
                "period": period,
                "sample_count": int(len(sub)),
                "smape": float(smape_floor50(sub[y_true_col].to_numpy(float), sub[y_pred_col].to_numpy(float))),
            }
        )
    return pd.DataFrame(rows)


def realtime_join(dayahead_df: pd.DataFrame, realtime_df: pd.DataFrame) -> pd.DataFrame:
    left = realtime_df.rename(columns={"y_true": "y_true_rt", "y_fused": "y_pred_rt"})
    right = dayahead_df.rename(columns={"y_true": "y_true_da", "y_fused": "y_pred_da"})
    return left.merge(
        right[["ds", "target_day", "period", "y_true_da", "y_pred_da"]],
        on=["ds", "target_day", "period"],
        how="inner",
    )


def arbitrage_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for period in ["overall", "1_8", "9_16", "17_24"]:
        sub = df if period == "overall" else df[df["period"] == period]
        if sub.empty:
            continue
        rows.append({"task": "realtime", "period": period, **arbitrage_metrics(sub)})
    return pd.DataFrame(rows)


def run_task_pipeline(task: str, args: argparse.Namespace) -> None:
    started = time.time()
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    _apply_train_length_decision(task, args)

    simulation_window, formal_window = build_windows(
        args.target_start,
        args.target_end,
        int(args.simulation_train_months),
        int(args.formal_train_months),
        float(args.val_ratio),
    )

    simulation_dir = work_dir / "simulation"
    formal_dir = work_dir / "formal"
    simulation_dir.mkdir(parents=True, exist_ok=True)
    formal_dir.mkdir(parents=True, exist_ok=True)

    print(f"[pipeline] task={task} phase=simulation window={simulation_window.start:%Y-%m-%d}~{simulation_window.end:%Y-%m-%d}")
    sim_specs = build_model_specs(task, simulation_window, simulation_dir, args)
    _run_specs_with_scheduler(sim_specs, conda_env=args.conda_env)
    sim_long = collect_phase_predictions(simulation_dir, sim_specs, str(args.data_path_xlsx), "simulation")

    sim_days = pd.to_datetime(sim_long["target_day"]).sort_values().drop_duplicates().reset_index(drop=True)
    if sim_days.empty:
        raise RuntimeError(f"No simulation rows found for task={task}.")
    # Use ALL simulation days for weight fitting — the simulation window
    # already covers exactly the validation portion of the training period.
    validation_days = sim_days
    validation_start = pd.Timestamp(validation_days.min()).strftime("%Y-%m-%d")
    validation_end = pd.Timestamp(validation_days.max()).strftime("%Y-%m-%d")
    train_start = validation_start
    train_end = validation_end

    validation_long = sim_long[sim_long["target_day"].isin(validation_days.dt.strftime("%Y-%m-%d"))].copy()
    validation_long.to_csv(work_dir / "validation_predictions_long.csv", index=False, encoding="utf-8-sig")

    reg_map = {
        period: value
        for period, value in {
            "1_8": args.reg_1_8,
            "9_16": args.reg_9_16,
            "17_24": args.reg_17_24,
        }.items()
        if value is not None
    }
    weights_df, fit_report = _fit_fixed_weights(
        sim_long,
        train_start=validation_start,
        train_end=validation_end,
        reg=float(args.meta_alpha),
        reg_map=reg_map or None,
        lower_bound=float(args.weight_lower_bound),
        upper_bound=float(args.weight_upper_bound),
    )
    weights_df.to_csv(work_dir / "weights.csv", index=False, encoding="utf-8-sig")
    fit_report.to_csv(work_dir / "fit_report.csv", index=False, encoding="utf-8-sig")

    print(f"[pipeline] task={task} phase=formal window={formal_window.start:%Y-%m-%d}~{formal_window.end:%Y-%m-%d}")
    formal_specs = build_model_specs(task, formal_window, formal_dir, args, previous_phase_dir=simulation_dir)
    _run_specs_with_scheduler(formal_specs, conda_env=args.conda_env)
    formal_long = collect_phase_predictions(formal_dir, formal_specs, str(args.data_path_xlsx), "formal")
    formal_long.to_csv(work_dir / "formal_predictions_long.csv", index=False, encoding="utf-8-sig")

    print(f"[pipeline] task={task} phase=fixed_weight_predict")
    fused = _apply_fixed_weights(
        formal_long,
        weights_df,
        task=task,
        test_start=formal_window.start.strftime("%Y-%m-%d"),
        test_end=formal_window.end.strftime("%Y-%m-%d"),
    )

    task_dir = work_dir / task
    task_dir.mkdir(parents=True, exist_ok=True)
    fused.to_csv(task_dir / "fused_predictions.csv", index=False, encoding="utf-8-sig")
    metrics = period_summary(fused, task=task)
    metrics.to_csv(task_dir / "metrics_smape.csv", index=False, encoding="utf-8-sig")

    candidate_config, candidate_source = _resolve_timemixer_candidate(args)
    summary = {
        "task": task,
        "target_start": formal_window.start.strftime("%Y-%m-%d"),
        "target_end": formal_window.end.strftime("%Y-%m-%d"),
        "simulation_start": simulation_window.start.strftime("%Y-%m-%d"),
        "simulation_end": simulation_window.end.strftime("%Y-%m-%d"),
        "simulation_train_months": int(simulation_window.train_months),
        "formal_train_months": int(formal_window.train_months),
        "validation_start": validation_start,
        "validation_end": validation_end,
        "weight_lower_bound": float(args.weight_lower_bound),
        "weight_upper_bound": float(args.weight_upper_bound),
        "runtime_seconds": round(time.time() - started, 2),
        "models": TASK_MODEL_POOL[task],
        "timemixer_candidate_config": str(candidate_config) if candidate_config else None,
        "timemixer_candidate_source": candidate_source,
    }
    (work_dir / "runtime_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def save_joint_report(dayahead_work_dir: Path, realtime_work_dir: Path, target_path: Path) -> None:
    da_fused = pd.read_csv(dayahead_work_dir / "dayahead" / "fused_predictions.csv")
    rt_fused = pd.read_csv(realtime_work_dir / "realtime" / "fused_predictions.csv")
    final_table = _final_result_table(da_fused, rt_fused)
    joined_rt = realtime_join(da_fused, rt_fused)
    rt_arb = arbitrage_summary(joined_rt)

    target_path.mkdir(parents=True, exist_ok=True)
    final_table.to_csv(target_path / "final_truth_vs_fusion.csv", index=False, encoding="utf-8-sig")
    joined_rt.to_csv(target_path / "joined_for_arbitrage.csv", index=False, encoding="utf-8-sig")
    rt_arb.to_csv(target_path / "metrics_arbitrage.csv", index=False, encoding="utf-8-sig")


def save_suite_summary(dayahead_work_dir: Path, realtime_work_dir: Path, joint_dir: Path, target_path: Path) -> None:
    da_metrics = pd.read_csv(dayahead_work_dir / "dayahead" / "metrics_smape.csv")
    rt_metrics = pd.read_csv(realtime_work_dir / "realtime" / "metrics_smape.csv")
    arb_metrics = pd.read_csv(joint_dir / "metrics_arbitrage.csv")

    da_runtime = json.loads((dayahead_work_dir / "runtime_summary.json").read_text(encoding="utf-8"))
    rt_runtime = json.loads((realtime_work_dir / "runtime_summary.json").read_text(encoding="utf-8"))

    da_summary = da_metrics.copy()
    da_summary["metric_group"] = "smape"
    da_summary["runtime_seconds"] = float(da_runtime.get("runtime_seconds", 0.0))

    rt_summary = rt_metrics.copy()
    rt_summary["metric_group"] = "smape"
    rt_summary["runtime_seconds"] = float(rt_runtime.get("runtime_seconds", 0.0))

    arb_summary = arb_metrics.copy()
    arb_summary["metric_group"] = "arbitrage"
    arb_summary["runtime_seconds"] = float(rt_runtime.get("runtime_seconds", 0.0))

    target_path.parent.mkdir(parents=True, exist_ok=True)
    pd.concat([da_summary, rt_summary, arb_summary], ignore_index=True, sort=False).to_csv(
        target_path,
        index=False,
        encoding="utf-8-sig",
    )

    # Per-model SMAPE ablation: compute each model's standalone SMAPE on the
    # formal window so we can compare against the fused result.
    _save_per_model_smape(dayahead_work_dir, realtime_work_dir, target_path.parent / "per_model_smape.csv")


def _save_per_model_smape(dayahead_work_dir: Path, realtime_work_dir: Path, target_path: Path) -> None:
    from .metrics import smape_floor50

    rows: list[dict[str, object]] = []
    for task, work_dir in [("dayahead", dayahead_work_dir), ("realtime", realtime_work_dir)]:
        long_path = work_dir / "formal_predictions_long.csv"
        if not long_path.exists():
            continue
        df = pd.read_csv(long_path)
        for (model_name, period), group in df.groupby(["model_name", "period"]):
            y_true = pd.to_numeric(group["y_true"], errors="coerce").to_numpy()
            y_pred = pd.to_numeric(group["y_pred"], errors="coerce").to_numpy()
            mask = np.isfinite(y_true) & np.isfinite(y_pred)
            if mask.sum() == 0:
                continue
            rows.append(
                {
                    "task": task,
                    "model_name": model_name,
                    "period": period,
                    "smape": smape_floor50(y_true[mask], y_pred[mask]),
                    "mae": float(np.mean(np.abs(y_true[mask] - y_pred[mask]))),
                    "sample_count": int(mask.sum()),
                }
            )
    if rows:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(target_path, index=False, encoding="utf-8-sig")
