from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fusion.metrics import arbitrage_metrics, smape_floor50
from fusion.project_defaults import DEFAULTS
from fusion.registry import get_adapter
from fusion.weights import fit_weights_from_long_table
from fusion.contracts import infer_period


@dataclass(frozen=True)
class ModelArtifact:
    model_name: str
    adapter: str
    source: Path
    task: str
    adapter_kwargs: dict[str, object]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run fixed-weight fusion for a single test window.")
    parser.add_argument("--train-start", required=True, help="Inclusive YYYY-MM-DD for weight-learning window.")
    parser.add_argument("--train-end", required=True, help="Inclusive YYYY-MM-DD for weight-learning window.")
    parser.add_argument("--test-start", required=True, help="Inclusive YYYY-MM-DD for fixed-weight test window.")
    parser.add_argument("--test-end", required=True, help="Inclusive YYYY-MM-DD for fixed-weight test window.")
    parser.add_argument("--work-dir", required=True, help="Directory for fusion outputs.")
    parser.add_argument("--reg", type=float, default=0.2)
    parser.add_argument("--reg-1-8", type=float, default=5.0)
    parser.add_argument("--reg-9-16", type=float, default=0.2)
    parser.add_argument("--reg-17-24", type=float, default=0.2)
    parser.add_argument("--weight-lower-bound", type=float, default=-0.5)
    parser.add_argument("--weight-upper-bound", type=float, default=1.2)
    parser.add_argument("--data-path-xlsx", default=str(DEFAULTS.data_xlsx))
    parser.add_argument("--tasks", nargs="+", default=["dayahead", "realtime"], choices=["dayahead", "realtime"], help="Which fusion tasks to run.")
    parser.add_argument("--timesfm-dayahead-source", default=None)
    parser.add_argument("--timesfm-realtime-source", default=None)
    parser.add_argument("--timemixer-dayahead-source", default=None)
    parser.add_argument("--timemixer-realtime-source", default=None)
    parser.add_argument("--sgdfnet-source", default=None)
    parser.add_argument("--rt916-dayahead-source", default=None)
    return parser


def _build_artifacts(args: argparse.Namespace) -> list[ModelArtifact]:
    merged_root = DEFAULTS.merged_dayahead_source_root
    merged_timesfm_da = merged_root / "timesfm_dayahead.csv"
    merged_timemixer_da = merged_root / "timemixer_dayahead.csv"
    merged_rt916_da = merged_root / "rt916_dayahead.csv"

    timesfm_da_source = Path(args.timesfm_dayahead_source) if args.timesfm_dayahead_source else DEFAULTS.timesfm_output / "backtest_dayahead.csv"
    timesfm_rt_source = Path(args.timesfm_realtime_source) if args.timesfm_realtime_source else DEFAULTS.timesfm_output / "backtest_realtime.csv"
    timemixer_da_source = Path(args.timemixer_dayahead_source) if args.timemixer_dayahead_source else DEFAULTS.timemixer_output / "predictions_day_ahead_last_month.csv"
    timemixer_rt_source = Path(args.timemixer_realtime_source) if args.timemixer_realtime_source else DEFAULTS.timemixer_output / "predictions_realtime_last_month.csv"
    sgdfnet_source = Path(args.sgdfnet_source) if args.sgdfnet_source else DEFAULTS.sgdfnet_output / "predictions.csv"
    rt916_da_source = Path(args.rt916_dayahead_source) if args.rt916_dayahead_source else DEFAULTS.rt916_output / "dayahead" / "rt916_dayahead.csv"

    if timesfm_da_source == DEFAULTS.timesfm_output / "backtest_dayahead.csv" and merged_timesfm_da.exists():
        timesfm_da_source = merged_timesfm_da
    if timemixer_da_source == DEFAULTS.timemixer_output / "predictions_day_ahead_last_month.csv" and merged_timemixer_da.exists():
        timemixer_da_source = merged_timemixer_da
    if rt916_da_source == DEFAULTS.rt916_output / "dayahead" / "rt916_dayahead.csv" and merged_rt916_da.exists():
        rt916_da_source = merged_rt916_da

    return [
        ModelArtifact(
            model_name="TimesFM",
            adapter="timesfm",
            source=timesfm_da_source,
            task="dayahead",
            adapter_kwargs={"task": "dayahead", "data_path": str(args.data_path_xlsx)},
        ),
        ModelArtifact(
            model_name="TimesFM",
            adapter="timesfm",
            source=timesfm_rt_source,
            task="realtime",
            adapter_kwargs={"task": "realtime", "data_path": str(args.data_path_xlsx)},
        ),
        ModelArtifact(
            model_name="TimeMixer",
            adapter="timemixer",
            source=timemixer_da_source,
            task="dayahead",
            adapter_kwargs={"task": "dayahead"},
        ),
        ModelArtifact(
            model_name="TimeMixer",
            adapter="timemixer",
            source=timemixer_rt_source,
            task="realtime",
            adapter_kwargs={"task": "realtime"},
        ),
        ModelArtifact(
            model_name="SGDFNet",
            adapter="sgdfnet",
            source=sgdfnet_source,
            task="realtime",
            adapter_kwargs={},
        ),
        ModelArtifact(
            model_name="RT916_SpikeFusionNet",
            adapter="rt916",
            source=rt916_da_source,
            task="dayahead",
            adapter_kwargs={"task": "dayahead"},
        ),
    ]


def _load_normalized_predictions(artifacts: list[ModelArtifact]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for artifact in artifacts:
        adapter_cls = get_adapter(artifact.adapter)
        adapter = adapter_cls(str(artifact.source), **artifact.adapter_kwargs)
        df = adapter.load().copy()
        df["model_name"] = artifact.model_name
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def _build_truth_frame(data_path_xlsx: str) -> pd.DataFrame:
    raw = pd.read_excel(data_path_xlsx)
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
    truth = pd.concat([da, rt], ignore_index=True)
    return truth.dropna(subset=["ds", "y_true"]).drop_duplicates(
        subset=["task", "target_day", "ds", "period", "hour_business"]
    )


def _overwrite_truth_from_source(normalized: pd.DataFrame, data_path_xlsx: str) -> pd.DataFrame:
    truth = _build_truth_frame(data_path_xlsx)
    pred_only = normalized.drop(columns=["y_true"])
    merged = pred_only.merge(
        truth,
        on=["task", "target_day", "ds", "period", "hour_business"],
        how="inner",
    )
    return merged


def _fit_fixed_weights(
    normalized: pd.DataFrame,
    *,
    train_start: str,
    train_end: str,
    reg: float,
    reg_map: dict[str, float] | None = None,
    lower_bound: float = -0.5,
    upper_bound: float = 1.2,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    train_days = pd.to_datetime(normalized["target_day"])
    train_mask = (train_days >= pd.Timestamp(train_start)) & (train_days <= pd.Timestamp(train_end))
    train_df = normalized.loc[train_mask].copy()
    if train_df.empty:
        raise RuntimeError("No training rows found for fixed-weight fitting window.")
    return fit_weights_from_long_table(
        train_df,
        reg=reg,
        reg_map=reg_map,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
    )


def _apply_fixed_weights(
    normalized: pd.DataFrame,
    weights_df: pd.DataFrame,
    *,
    task: str,
    test_start: str,
    test_end: str,
) -> pd.DataFrame:
    task_df = normalized[normalized["task"] == task].copy()
    task_days = pd.to_datetime(task_df["target_day"])
    task_df = task_df[(task_days >= pd.Timestamp(test_start)) & (task_days <= pd.Timestamp(test_end))].copy()
    if task_df.empty:
        raise RuntimeError(f"No test rows found for task={task}.")

    wide_day = (
        task_df.pivot_table(
            index=["task", "target_day", "ds", "period", "hour_business"],
            columns="model_name",
            values="y_pred",
            aggfunc="last",
        )
        .reset_index()
    )
    wide_day.columns.name = None
    truth_day = task_df[
        ["task", "target_day", "ds", "period", "hour_business", "y_true"]
    ].drop_duplicates(subset=["task", "target_day", "ds", "period", "hour_business"])
    fused = truth_day.merge(wide_day, on=["task", "target_day", "ds", "period", "hour_business"], how="left")

    fused_values: list[float] = []
    task_weights = weights_df[weights_df["task"] == task].copy()
    for _, row in fused.iterrows():
        period_weights = task_weights[task_weights["period"] == row["period"]]
        value = 0.0
        for _, weight_row in period_weights.iterrows():
            model_name = weight_row["model_name"]
            if model_name not in fused.columns or pd.isna(row[model_name]):
                continue
            value += float(weight_row["weight"]) * float(row[model_name])
        fused_values.append(value)
    fused["y_fused"] = fused_values
    return fused.sort_values(["target_day", "hour_business"]).reset_index(drop=True)


def _period_summary(df: pd.DataFrame, *, task: str, y_true_col: str, y_pred_col: str) -> pd.DataFrame:
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
    out = pd.DataFrame(rows)
    if not out.empty:
        out["day_ahead_accuracy"] = np.where(out["task"] == "dayahead", 100.0 - out["smape"], np.nan)
    return out


def _realtime_join(dayahead_df: pd.DataFrame, realtime_df: pd.DataFrame) -> pd.DataFrame:
    left = realtime_df.rename(columns={"y_true": "y_true_rt", "y_fused": "y_pred_rt"})
    right = dayahead_df.rename(columns={"y_true": "y_true_da", "y_fused": "y_pred_da"})
    return left.merge(
        right[["ds", "target_day", "period", "y_true_da", "y_pred_da"]],
        on=["ds", "target_day", "period"],
        how="inner",
    )


def _arbitrage_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for period in ["overall", "1_8", "9_16", "17_24"]:
        sub = df if period == "overall" else df[df["period"] == period]
        if sub.empty:
            continue
        rows.append({"task": "realtime", "period": period, **arbitrage_metrics(sub)})
    return pd.DataFrame(rows)


def _final_result_table(dayahead_df: pd.DataFrame, realtime_df: pd.DataFrame) -> pd.DataFrame:
    da = dayahead_df[["target_day", "ds", "period", "hour_business", "y_true", "y_fused"]].copy()
    da = da.rename(columns={"y_true": "true_dayahead", "y_fused": "fused_dayahead"})
    rt = realtime_df[["target_day", "ds", "period", "hour_business", "y_true", "y_fused"]].copy()
    rt = rt.rename(columns={"y_true": "true_realtime", "y_fused": "fused_realtime"})
    return da.merge(rt, on=["target_day", "ds", "period", "hour_business"], how="outer").sort_values(
        ["target_day", "hour_business"]
    )


def main() -> None:
    args = build_parser().parse_args()
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    artifacts = _build_artifacts(args)
    normalized = _load_normalized_predictions(artifacts)
    normalized = _overwrite_truth_from_source(normalized, str(args.data_path_xlsx))
    normalized.to_csv(work_dir / "all_model_predictions_long.csv", index=False, encoding="utf-8-sig")

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
        normalized,
        train_start=args.train_start,
        train_end=args.train_end,
        reg=float(args.reg),
        reg_map=reg_map or None,
        lower_bound=float(args.weight_lower_bound),
        upper_bound=float(args.weight_upper_bound),
    )
    if weights_df.empty:
        raise RuntimeError("No weights were fit for the fixed learning window.")

    da_dir = work_dir / "dayahead"
    rt_dir = work_dir / "realtime"
    da_dir.mkdir(parents=True, exist_ok=True)
    rt_dir.mkdir(parents=True, exist_ok=True)

    weights_df.to_csv(work_dir / "fixed_weights.csv", index=False, encoding="utf-8-sig")
    fit_report.to_csv(work_dir / "weight_fit_report.csv", index=False, encoding="utf-8-sig")
    metrics_frames: list[pd.DataFrame] = []
    final_tables: dict[str, pd.DataFrame] = {}
    fused_results: dict[str, pd.DataFrame] = {}

    if "dayahead" in args.tasks:
        da_fused = _apply_fixed_weights(
            normalized,
            weights_df,
            task="dayahead",
            test_start=args.test_start,
            test_end=args.test_end,
        )
        da_fused.to_csv(da_dir / "fused_predictions.csv", index=False, encoding="utf-8-sig")
        da_metrics = _period_summary(da_fused, task="dayahead", y_true_col="y_true", y_pred_col="y_fused")
        da_metrics.to_csv(da_dir / "metrics_smape.csv", index=False, encoding="utf-8-sig")
        metrics_frames.append(da_metrics)
        fused_results["dayahead"] = da_fused
        final_tables["dayahead"] = da_fused

    if "realtime" in args.tasks:
        rt_fused = _apply_fixed_weights(
            normalized,
            weights_df,
            task="realtime",
            test_start=args.test_start,
            test_end=args.test_end,
        )
        rt_fused.to_csv(rt_dir / "fused_predictions.csv", index=False, encoding="utf-8-sig")
        rt_metrics = _period_summary(rt_fused, task="realtime", y_true_col="y_true", y_pred_col="y_fused")
        rt_metrics.to_csv(rt_dir / "metrics_smape.csv", index=False, encoding="utf-8-sig")
        metrics_frames.append(rt_metrics)
        fused_results["realtime"] = rt_fused
        final_tables["realtime"] = rt_fused

    if "dayahead" in fused_results and "realtime" in fused_results:
        joined_rt = _realtime_join(fused_results["dayahead"], fused_results["realtime"])
        rt_arbitrage = _arbitrage_summary(joined_rt)
        joined_rt.to_csv(rt_dir / "joined_for_arbitrage.csv", index=False, encoding="utf-8-sig")
        rt_arbitrage.to_csv(rt_dir / "metrics_arbitrage.csv", index=False, encoding="utf-8-sig")
        metrics_frames.append(rt_arbitrage)
        _final_result_table(fused_results["dayahead"], fused_results["realtime"]).to_csv(
            work_dir / "final_truth_vs_fusion.csv",
            index=False,
            encoding="utf-8-sig",
        )
    elif "dayahead" in fused_results:
        final_tables["dayahead"].to_csv(work_dir / "final_truth_vs_fusion.csv", index=False, encoding="utf-8-sig")
    elif "realtime" in fused_results:
        final_tables["realtime"].to_csv(work_dir / "final_truth_vs_fusion.csv", index=False, encoding="utf-8-sig")

    if metrics_frames:
        pd.concat(metrics_frames, ignore_index=True, sort=False).to_csv(
            work_dir / "metrics_summary.csv",
            index=False,
            encoding="utf-8-sig",
        )


if __name__ == "__main__":
    main()
