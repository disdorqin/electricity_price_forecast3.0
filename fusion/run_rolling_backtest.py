from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if __package__ in {None, ""}:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from fusion.metrics import arbitrage_metrics, smape_floor50
    from fusion.project_defaults import DEFAULTS
    from fusion.registry import get_adapter
    from fusion.weights import fit_weights_from_long_table
else:
    from .metrics import arbitrage_metrics, smape_floor50
    from .project_defaults import DEFAULTS
    from .registry import get_adapter
    from .weights import fit_weights_from_long_table


@dataclass(frozen=True)
class ModelArtifact:
    model_name: str
    adapter: str
    source: Path
    task: str
    runner: list[str] | None
    adapter_kwargs: dict[str, object]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run rolling daily fusion backtest with 12-month weight learning windows.")
    parser.add_argument("--test-start", required=True, help="Inclusive target day start, YYYY-MM-DD.")
    parser.add_argument("--test-end", required=True, help="Inclusive target day end, YYYY-MM-DD.")
    parser.add_argument("--history-days", type=int, default=365, help="Historical days used to learn weights before each target day.")
    parser.add_argument("--data-path-xlsx", default=str(DEFAULTS.data_xlsx))
    parser.add_argument("--data-path-csv", default=str(DEFAULTS.data_csv))
    parser.add_argument("--work-dir", required=True, help="Directory for backtest outputs.")
    parser.add_argument("--reg", type=float, default=0.1, help="Regularization strength for weight fitting.")
    parser.add_argument("--skip-model-runs", action="store_true", help="Reuse existing model output CSVs without rerunning model wrappers.")
    return parser


def _run_command(command: list[str], cwd: Path) -> None:
    subprocess.run(command, check=True, cwd=str(cwd))


def _test_end_exclusive(test_end: str) -> str:
    return (pd.Timestamp(test_end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def _test_end_timestamp(test_end: str) -> str:
    return (pd.Timestamp(test_end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")


def _history_start(test_start: str, history_days: int) -> str:
    return (pd.Timestamp(test_start) - pd.Timedelta(days=history_days)).strftime("%Y-%m-%d")


def _build_artifacts(args: argparse.Namespace) -> list[ModelArtifact]:
    history_start = _history_start(args.test_start, args.history_days)
    end_exclusive = _test_end_exclusive(args.test_end)
    end_timestamp = _test_end_timestamp(args.test_end)

    artifacts = [
        ModelArtifact(
            model_name="TimesFM",
            adapter="timesfm",
            source=DEFAULTS.timesfm_output / "backtest_dayahead.csv",
            task="dayahead",
            runner=[
                sys.executable,
                str(PROJECT_ROOT / "fusion" / "runners" / "run_timesfm_export.py"),
                "--task",
                "dayahead",
                "--start-date",
                history_start,
                "--end-date",
                args.test_end,
                "--data-path",
                str(args.data_path_xlsx),
                "--output",
                str(DEFAULTS.timesfm_output / "backtest_dayahead.csv"),
            ],
            adapter_kwargs={"task": "dayahead", "data_path": str(args.data_path_xlsx)},
        ),
        ModelArtifact(
            model_name="TimesFM",
            adapter="timesfm",
            source=DEFAULTS.timesfm_output / "backtest_realtime.csv",
            task="realtime",
            runner=[
                sys.executable,
                str(PROJECT_ROOT / "fusion" / "runners" / "run_timesfm_export.py"),
                "--task",
                "realtime",
                "--start-date",
                history_start,
                "--end-date",
                args.test_end,
                "--data-path",
                str(args.data_path_xlsx),
                "--output",
                str(DEFAULTS.timesfm_output / "backtest_realtime.csv"),
            ],
            adapter_kwargs={"task": "realtime", "data_path": str(args.data_path_xlsx)},
        ),
        ModelArtifact(
            model_name="TimeMixer",
            adapter="timemixer",
            source=DEFAULTS.timemixer_output / "predictions_day_ahead_last_month.csv",
            task="dayahead",
            runner=[
                sys.executable,
                str(PROJECT_ROOT / "fusion" / "runners" / "run_timemixer_export.py"),
                "--task",
                "realtime",
                "--test-start",
                history_start,
                "--test-end-exclusive",
                end_exclusive,
                "--data-path",
                str(args.data_path_csv),
                "--output-dir",
                str(DEFAULTS.timemixer_output),
            ],
            adapter_kwargs={"task": "dayahead"},
        ),
        ModelArtifact(
            model_name="TimeMixer",
            adapter="timemixer",
            source=DEFAULTS.timemixer_output / "predictions_realtime_last_month.csv",
            task="realtime",
            runner=[
                sys.executable,
                str(PROJECT_ROOT / "fusion" / "runners" / "run_timemixer_export.py"),
                "--task",
                "realtime",
                "--test-start",
                history_start,
                "--test-end-exclusive",
                end_exclusive,
                "--data-path",
                str(args.data_path_csv),
                "--output-dir",
                str(DEFAULTS.timemixer_output),
            ],
            adapter_kwargs={"task": "realtime"},
        ),
        ModelArtifact(
            model_name="SGDFNet",
            adapter="sgdfnet",
            source=DEFAULTS.sgdfnet_output / "predictions.csv",
            task="realtime",
            runner=[
                sys.executable,
                str(PROJECT_ROOT / "fusion" / "runners" / "run_sgdfnet_export.py"),
                "--forecast-start",
                history_start,
                "--forecast-end",
                args.test_end,
                "--data-path",
                str(args.data_path_xlsx),
                "--output",
                str(DEFAULTS.sgdfnet_output / "predictions.csv"),
            ],
            adapter_kwargs={},
        ),
        ModelArtifact(
            model_name="RT916_SpikeFusionNet",
            adapter="rt916",
            source=DEFAULTS.rt916_output / "dayahead" / "rt916_dayahead.csv",
            task="dayahead",
            runner=[
                sys.executable,
                str(PROJECT_ROOT / "fusion" / "runners" / "run_rt916_export.py"),
                "--task",
                "dayahead",
                "--start",
                f"{history_start} 01:00:00",
                "--end",
                end_timestamp,
                "--data-path",
                str(args.data_path_xlsx),
                "--output",
                str(DEFAULTS.rt916_output / "dayahead" / "rt916_dayahead.csv"),
            ],
            adapter_kwargs={"task": "dayahead"},
        ),
    ]

    return artifacts


def _ensure_model_outputs(args: argparse.Namespace, artifacts: list[ModelArtifact]) -> None:
    if args.skip_model_runs:
        return

    executed: set[tuple[str, ...]] = set()
    for artifact in artifacts:
        if artifact.runner is None:
            continue
        key = tuple(artifact.runner)
        if key in executed:
            continue
        _run_command(artifact.runner, PROJECT_ROOT)
        executed.add(key)


def _load_normalized_predictions(artifacts: list[ModelArtifact]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for artifact in artifacts:
        adapter_cls = get_adapter(artifact.adapter)
        adapter = adapter_cls(str(artifact.source), **artifact.adapter_kwargs)
        df = adapter.load().copy()
        df["model_name"] = artifact.model_name
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def _fit_daily_weights(
    df: pd.DataFrame,
    *,
    task: str,
    test_start: str,
    test_end: str,
    history_days: int,
    reg: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    task_df = df[df["task"] == task].copy()
    task_df["target_day"] = pd.to_datetime(task_df["target_day"])

    days = pd.date_range(pd.Timestamp(test_start), pd.Timestamp(test_end), freq="D")
    weights_rows: list[dict[str, object]] = []
    fused_rows: list[pd.DataFrame] = []

    for target_day in days:
        hist_start = target_day - pd.Timedelta(days=history_days)
        hist_end = target_day - pd.Timedelta(days=1)
        hist_df = task_df[(task_df["target_day"] >= hist_start) & (task_df["target_day"] <= hist_end)].copy()
        day_df = task_df[task_df["target_day"] == target_day].copy()
        if hist_df.empty or day_df.empty:
            continue

        weights_df, _ = fit_weights_from_long_table(hist_df, reg=reg)
        day_weights = weights_df[weights_df["task"] == task].copy()
        if day_weights.empty:
            continue
        day_weights["effective_target_day"] = target_day.strftime("%Y-%m-%d")
        weights_rows.append(day_weights)

        wide_day = (
            day_df.pivot_table(
                index=["task", "target_day", "ds", "period", "hour_business"],
                columns="model_name",
                values="y_pred",
                aggfunc="last",
            )
            .reset_index()
        )
        wide_day.columns.name = None
        truth_day = day_df[
            ["task", "target_day", "ds", "period", "hour_business", "y_true"]
        ].drop_duplicates(subset=["task", "target_day", "ds", "period", "hour_business"])
        fused_day = truth_day.merge(wide_day, on=["task", "target_day", "ds", "period", "hour_business"], how="left")

        fused_values: list[float] = []
        for _, row in fused_day.iterrows():
            period_weights = day_weights[day_weights["period"] == row["period"]]
            value = 0.0
            for _, weight_row in period_weights.iterrows():
                model_name = weight_row["model_name"]
                if model_name not in fused_day.columns:
                    continue
                value += float(weight_row["weight"]) * float(row[model_name])
            fused_values.append(value)
        fused_day["y_fused"] = fused_values
        fused_rows.append(fused_day)

    weights_out = pd.concat(weights_rows, ignore_index=True) if weights_rows else pd.DataFrame()
    fused_out = pd.concat(fused_rows, ignore_index=True) if fused_rows else pd.DataFrame()
    if not fused_out.empty:
        fused_out["target_day"] = pd.to_datetime(fused_out["target_day"]).dt.strftime("%Y-%m-%d")
    return weights_out, fused_out


def _period_summary(
    df: pd.DataFrame,
    *,
    task: str,
    y_true_col: str,
    y_pred_col: str,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    period_order = ["overall", "1_8", "9_16", "17_24"]
    for period in period_order:
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
    out = pd.DataFrame(rows)
    if not out.empty:
        out["day_ahead_accuracy"] = np.where(
            out["task"] == "dayahead",
            100.0 - out["smape"],
            np.nan,
        )
    return out


def _realtime_join(dayahead_df: pd.DataFrame, realtime_df: pd.DataFrame) -> pd.DataFrame:
    left = realtime_df.rename(columns={"y_true": "y_true_rt", "y_fused": "y_pred_rt"})
    right = dayahead_df.rename(columns={"y_true": "y_true_da", "y_fused": "y_pred_da"})
    merged = left.merge(
        right[["ds", "target_day", "period", "y_true_da", "y_pred_da"]],
        on=["ds", "target_day", "period"],
        how="inner",
    )
    return merged


def _arbitrage_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for period in ["overall", "1_8", "9_16", "17_24"]:
        sub = df if period == "overall" else df[df["period"] == period]
        if sub.empty:
            continue
        metrics = arbitrage_metrics(sub)
        rows.append({"task": "realtime", "period": period, **metrics})
    return pd.DataFrame(rows)


def _final_result_table(dayahead_df: pd.DataFrame, realtime_df: pd.DataFrame) -> pd.DataFrame:
    da = dayahead_df[["target_day", "ds", "period", "hour_business", "y_true", "y_fused"]].copy()
    da = da.rename(columns={"y_true": "true_dayahead", "y_fused": "fused_dayahead"})
    rt = realtime_df[["target_day", "ds", "period", "hour_business", "y_true", "y_fused"]].copy()
    rt = rt.rename(columns={"y_true": "true_realtime", "y_fused": "fused_realtime"})
    merged = da.merge(rt, on=["target_day", "ds", "period", "hour_business"], how="outer")
    return merged.sort_values(["target_day", "hour_business"]).reset_index(drop=True)


def main() -> None:
    args = build_parser().parse_args()
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    artifacts = _build_artifacts(args)
    _ensure_model_outputs(args, artifacts)

    normalized = _load_normalized_predictions(artifacts)
    normalized.to_csv(work_dir / "all_model_predictions_long.csv", index=False, encoding="utf-8-sig")

    da_weights, da_fused = _fit_daily_weights(
        normalized,
        task="dayahead",
        test_start=args.test_start,
        test_end=args.test_end,
        history_days=int(args.history_days),
        reg=float(args.reg),
    )
    rt_weights, rt_fused = _fit_daily_weights(
        normalized,
        task="realtime",
        test_start=args.test_start,
        test_end=args.test_end,
        history_days=int(args.history_days),
        reg=float(args.reg),
    )

    if da_fused.empty or rt_fused.empty:
        raise RuntimeError("Rolling fusion produced empty fused outputs for at least one task.")

    da_dir = work_dir / "dayahead"
    rt_dir = work_dir / "realtime"
    da_dir.mkdir(parents=True, exist_ok=True)
    rt_dir.mkdir(parents=True, exist_ok=True)

    da_fused.to_csv(da_dir / "fused_predictions.csv", index=False, encoding="utf-8-sig")
    rt_fused.to_csv(rt_dir / "fused_predictions.csv", index=False, encoding="utf-8-sig")
    da_weights.to_csv(da_dir / "daily_weights.csv", index=False, encoding="utf-8-sig")
    rt_weights.to_csv(rt_dir / "daily_weights.csv", index=False, encoding="utf-8-sig")

    da_metrics = _period_summary(da_fused, task="dayahead", y_true_col="y_true", y_pred_col="y_fused")
    rt_metrics = _period_summary(rt_fused, task="realtime", y_true_col="y_true", y_pred_col="y_fused")
    joined_rt = _realtime_join(da_fused, rt_fused)
    rt_arbitrage = _arbitrage_summary(joined_rt)

    da_metrics.to_csv(da_dir / "metrics_smape.csv", index=False, encoding="utf-8-sig")
    rt_metrics.to_csv(rt_dir / "metrics_smape.csv", index=False, encoding="utf-8-sig")
    joined_rt.to_csv(rt_dir / "joined_for_arbitrage.csv", index=False, encoding="utf-8-sig")
    rt_arbitrage.to_csv(rt_dir / "metrics_arbitrage.csv", index=False, encoding="utf-8-sig")
    _final_result_table(da_fused, rt_fused).to_csv(
        work_dir / "final_truth_vs_fusion.csv",
        index=False,
        encoding="utf-8-sig",
    )

    summary_rows = [da_metrics, rt_metrics, rt_arbitrage]
    pd.concat(summary_rows, ignore_index=True, sort=False).to_csv(
        work_dir / "metrics_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )


if __name__ == "__main__":
    main()
