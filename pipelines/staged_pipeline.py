from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from fusion.classifier_bridge import run_classifier_pipeline
from fusion.contracts import infer_period, standardize_prediction_table
from fusion.run_fixed_window_fusion import _apply_fixed_weights
from fusion.weights import fit_weights_from_long_table
from runners.registry import get_model_pipeline
from utils.daily_run_layout import build_daily_run_layout


RT916_MODEL_KEY = "rt916"
DAYAHEAD_MODELS = ["lightgbm", "timesfm", "timemixer"]
REALTIME_MODELS = ["timesfm", "timemixer", "sgdfnet"]
FORMAL_DAYAHEAD_MODELS = ["lightgbm", "timesfm", "timemixer"]
FORMAL_REALTIME_MODELS = ["timesfm", "timemixer", "sgdfnet", RT916_MODEL_KEY]
logger = logging.getLogger(__name__)


def _resolve_run_date(args) -> str:
    run_date = args.date or args.start
    if not run_date:
        raise ValueError("staged pipelines require --date or --start")
    return pd.Timestamp(run_date).strftime("%Y-%m-%d")


def _resolve_daily_runs_root(args) -> Path:
    root = getattr(args, "daily_run_root", None) or "daily_runs"
    return Path(root)


def _resolve_models_for_target(target: str, stage_models: str) -> list[str]:
    raw = (stage_models or "formal").strip().lower()
    if raw == "formal":
        if target == "dayahead":
            return FORMAL_DAYAHEAD_MODELS.copy()
        if target == "realtime":
            return FORMAL_REALTIME_MODELS.copy()
    if raw == "all":
        if target == "dayahead":
            return DAYAHEAD_MODELS.copy()
        if target == "realtime":
            return REALTIME_MODELS.copy() + [RT916_MODEL_KEY]
    explicit = [item.strip().lower() for item in stage_models.split(",") if item.strip()]
    if explicit:
        return explicit
    if target == "dayahead":
        return DAYAHEAD_MODELS.copy()
    if target == "realtime":
        return REALTIME_MODELS.copy() + [RT916_MODEL_KEY]
    raise ValueError(f"Unsupported staged target: {target}")


def _target_column(target: str) -> str:
    if target == "dayahead":
        return "日前电价"
    if target == "realtime":
        return "实时电价"
    raise ValueError(f"Unsupported target: {target}")


def _read_truth_frame(data_path: str, target: str) -> pd.DataFrame:
    raw = pd.read_excel(data_path, engine="openpyxl")
    target_col = _target_column(target)
    if "时刻" not in raw.columns or target_col not in raw.columns:
        raise ValueError(f"Dataset missing required columns for {target}: 时刻, {target_col}")
    truth = raw[["时刻", target_col]].copy()
    truth["时刻"] = pd.to_datetime(truth["时刻"], errors="coerce")
    truth["真实值"] = pd.to_numeric(truth[target_col], errors="coerce")
    truth = truth.drop(columns=[target_col]).dropna(subset=["时刻", "真实值"])
    return truth.drop_duplicates(subset=["时刻"], keep="last").sort_values("时刻").reset_index(drop=True)


def _prediction_frame_from_result(result, model_name: str, target: str, run_date: str, source: str) -> pd.DataFrame:
    frame = result.frame.copy()
    ts_col = frame.columns[0]
    pred_col = frame.columns[1]
    out = pd.DataFrame(
        {
            "时刻": pd.to_datetime(frame[ts_col], errors="coerce"),
            "预测值": pd.to_numeric(frame[pred_col], errors="coerce"),
            "model_name": model_name,
            "target": target,
            "run_date": run_date,
            "source": source,
        }
    )
    return out.dropna(subset=["时刻", "预测值"]).sort_values("时刻").reset_index(drop=True)


def _attach_truth(pred_df: pd.DataFrame, truth_df: pd.DataFrame) -> pd.DataFrame:
    merged = pred_df.merge(truth_df, on="时刻", how="left")
    return merged.dropna(subset=["真实值"]).reset_index(drop=True)


def _attach_optional_truth(pred_df: pd.DataFrame, truth_df: pd.DataFrame) -> pd.DataFrame:
    merged = pred_df.merge(truth_df, on="时刻", how="left")
    return merged.reset_index(drop=True)


def _model_dir(layout, model_name: str) -> Path:
    model_dir = layout.model_outputs_dir / model_name
    model_dir.mkdir(parents=True, exist_ok=True)
    return model_dir


def _run_model_for_range(
    model_name: str, *, target: str, start: str, end: str,
    predict_date: str, args
):
    """Run a model's predict_range with explicit start/end for the date range.

    predict_date controls the training-window reference point.
    start/end control the prediction output range.
    """
    pipeline = get_model_pipeline(model_name)
    kwargs = {
        "target": target,
        "predict_date": predict_date,
        "start": start,
        "end": end,
        "data_path": args.data_path,
        "output_root": args.output_root,
        "training_months": args.training_months,
        "val_ratio": args.val_ratio,
        "use_predicted_temp": args.use_predicted_temp,
        "segment_count": args.segment_count,
        "seed": args.seed,
        "deterministic": args.deterministic,
    }
    return pipeline.predict_range(**kwargs)


def run_model_stage(args):
    """Efficient model stage: train once, predict for validation period + forecast day."""
    run_date = _resolve_run_date(args)
    targets = ["dayahead", "realtime"] if args.target == "both" else [args.target]
    root = _resolve_daily_runs_root(args)
    outputs: list[str] = []

    # ── Compute the validation window ──
    # validation-days controls the validation period length (default 30 days).
    # 720 hourly rows for stable SLSQP weight fitting,
    # DA training ~11 months, prediction horizon 1-30 days (acceptable for cyclical prices).
    run_ts = pd.Timestamp(run_date)
    training_months = int(getattr(args, "training_months", 12))
    val_days = max(int(getattr(args, "validation_days", 30) or 30), 1)
    val_start = run_ts - pd.Timedelta(days=val_days)
    val_end = run_ts - pd.Timedelta(days=1)          # last validation day

    val_start_str = val_start.strftime("%Y-%m-%d")
    val_end_str = val_end.strftime("%Y-%m-%d")
    forecast_str = run_ts.strftime("%Y-%m-%d")

    logger.info(
        "Val period: %s → %s (%d days) | Forecast: %s | DA training ~%d months",
        val_start_str, val_end_str, val_days, forecast_str, training_months,
    )

    # Track model execution status for summary reporting
    model_status: dict[str, str] = {}  # "{target}/{model}" -> "ok" | "skip" | "FAIL: reason"

    for target in targets:
        layout = build_daily_run_layout(root, run_date, target)
        truth_df = _read_truth_frame(args.data_path, target)
        requested_models = _resolve_models_for_target(target, getattr(args, "stage_models", "formal"))
        for model_name in requested_models:
            model_key = f"{target}/{model_name}"
            model_dir = _model_dir(layout, model_name)

            # ── Skip if outputs already exist (resume support) ──
            _forecast_csv = model_dir / "forecast_predictions.csv"
            _val_csv = model_dir / "val_predictions.csv"
            if _forecast_csv.exists() and _val_csv.exists():
                # Verify the files are non-empty (guard against empty files from prior failures)
                try:
                    _fc_head = pd.read_csv(_forecast_csv, nrows=1, encoding="utf-8-sig")
                    _vl_head = pd.read_csv(_val_csv, nrows=1, encoding="utf-8-sig")
                    if len(_fc_head) > 0 and len(_vl_head) > 0:
                        logger.info("SKIP %s/%s — outputs already exist", target, model_name)
                        model_status[model_key] = "skip (outputs exist)"
                        continue
                    else:
                        logger.info("REDO %s/%s — existing outputs are empty, re-running", target, model_name)
                except Exception:
                    logger.info("REDO %s/%s — existing outputs unreadable, re-running", target, model_name)

            # ── Single predict_range call for validation period ──
            val_df = pd.DataFrame(columns=["时刻", "预测值", "model_name", "target", "run_date", "source"])
            val_error = None
            try:
                val_result = _run_model_for_range(
                    model_name, target=target,
                    start=val_start_str, end=val_end_str,
                    predict_date=val_start_str,
                    args=args,
                )
                if val_result is not None:
                    val_df = _prediction_frame_from_result(val_result, model_name, target, run_date, "validation")
            except Exception as exc:  # noqa: BLE001
                val_error = str(exc)
                logger.error("FAILED validation %s/%s: %s", target, model_name, exc)

            # ── Single predict_range call for forecast day ──
            forecast_df = pd.DataFrame(columns=["时刻", "预测值", "model_name", "target", "run_date", "source"])
            fc_error = None
            try:
                fc_result = _run_model_for_range(
                    model_name, target=target,
                    start=forecast_str, end=forecast_str,
                    predict_date=forecast_str,
                    args=args,
                )
                if fc_result is not None:
                    forecast_df = _prediction_frame_from_result(fc_result, model_name, target, run_date, "forecast")
            except Exception as exc:  # noqa: BLE001
                fc_error = str(exc)
                logger.error("FAILED forecast %s/%s: %s", target, model_name, exc)

            if val_df.empty and forecast_df.empty:
                error_detail = val_error or fc_error or "both val and forecast produced no output"
                model_status[model_key] = f"FAIL: {error_detail}"
                continue

            val_path = model_dir / "val_predictions.csv"
            forecast_path = model_dir / "forecast_predictions.csv"

            if not val_df.empty:
                val_ready = _attach_truth(val_df, truth_df)
                val_ready.to_csv(val_path, index=False, encoding="utf-8-sig")
                outputs.append(str(val_path))
                logger.info("%s/%s val: %d rows", target, model_name, len(val_ready))

            if not forecast_df.empty:
                forecast_ready = _attach_optional_truth(forecast_df, truth_df)
                forecast_ready.to_csv(forecast_path, index=False, encoding="utf-8-sig")
                outputs.append(str(forecast_path))
                logger.info("%s/%s forecast: %d rows", target, model_name, len(forecast_ready))

            model_status[model_key] = "ok"

    # ── Summary: report which models succeeded/failed ──
    if model_status:
        ok_list = [k for k, v in model_status.items() if v == "ok" or v.startswith("skip")]
        fail_list = [(k, v) for k, v in model_status.items() if v.startswith("FAIL")]
        logger.info("=" * 60)
        logger.info("model_stage summary for %s:", run_date)
        for key in sorted(model_status):
            status = model_status[key]
            logger.info("  %-40s %s", key, status)
        if fail_list:
            logger.error("!! %d model(s) FAILED:", len(fail_list))
            for key, reason in fail_list:
                logger.error("   %s: %s", key, reason)
        logger.info("=" * 60)

    return outputs


def _collect_stage_predictions(layout, *, file_name: str) -> pd.DataFrame:
    if not layout.model_outputs_dir.exists():
        raise FileNotFoundError(f"Model outputs directory not found: {layout.model_outputs_dir}")
    frames: list[pd.DataFrame] = []
    for model_dir in sorted(layout.model_outputs_dir.iterdir()):
        candidate = model_dir / file_name
        if not candidate.exists():
            continue
        df = pd.read_csv(candidate, encoding="utf-8-sig")
        if not df.empty:
            frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No stage prediction files found for {file_name} under {layout.model_outputs_dir}")
    return pd.concat(frames, ignore_index=True)


def _to_contract_long_table(df: pd.DataFrame, *, target: str) -> pd.DataFrame:
    out = df.copy()
    out["ds"] = pd.to_datetime(out["时刻"], errors="coerce")
    out["target_day"] = out["ds"].dt.normalize().where(out["ds"].dt.hour != 0, out["ds"].dt.normalize() - pd.Timedelta(days=1))
    out["hour_business"] = out["ds"].dt.hour.replace({0: 24}).astype(int)
    out["period"] = out["hour_business"].map(infer_period)
    out["task"] = target
    out["y_true"] = pd.to_numeric(out["真实值"], errors="coerce")
    out["y_pred"] = pd.to_numeric(out["预测值"], errors="coerce")
    long_df = out[
        ["task", "model_name", "target_day", "ds", "period", "hour_business", "y_true", "y_pred"]
    ].copy()
    long_df["target_day"] = pd.to_datetime(long_df["target_day"]).dt.strftime("%Y-%m-%d")
    return standardize_prediction_table(long_df.dropna(subset=["y_true", "y_pred"]))


def run_learner_stage(args):
    run_date = _resolve_run_date(args)
    targets = ["dayahead", "realtime"] if args.target == "both" else [args.target]
    root = _resolve_daily_runs_root(args)
    outputs: list[str] = []

    for target in targets:
        layout = build_daily_run_layout(root, run_date, target)
        val_df = _collect_stage_predictions(layout, file_name="val_predictions.csv")
        raw_val_path = layout.learner_inputs_dir / "validation_predictions.csv"
        val_df.to_csv(raw_val_path, index=False, encoding="utf-8-sig")

        # Report which models are included in fusion
        included_models = sorted(val_df["model_name"].dropna().unique().tolist()) if "model_name" in val_df.columns else []
        model_row_counts = {}
        if "model_name" in val_df.columns:
            for m in included_models:
                model_row_counts[m] = int((val_df["model_name"] == m).sum())
        logger.info(
            "learner_stage %s/%s: %d models included in fusion: %s",
            target, run_date, len(included_models),
            ", ".join(f"{m}({model_row_counts.get(m, 0)}rows)" for m in included_models),
        )
        if not included_models:
            raise RuntimeError(f"No model predictions found for {target}/{run_date} — cannot fit fusion weights")

        contract_df = _to_contract_long_table(val_df, target=target)
        contract_path = layout.learner_inputs_dir / "validation_long_table.csv"
        contract_df.to_csv(contract_path, index=False, encoding="utf-8-sig")

        weights_df, report_df = fit_weights_from_long_table(
            contract_df,
            reg=0.1,
            lower_bound=float(getattr(args, "weight_lower_bound", -0.5)),
            upper_bound=float(getattr(args, "weight_upper_bound", 1.2)),
        )
        if weights_df.empty:
            raise RuntimeError(f"No learner weights produced for {target}/{run_date}")
        weights_df = _ensure_complete_period_weights(weights_df, contract_df, target=target)

        weights_path = layout.learner_outputs_dir / "weights.csv"
        report_path = layout.learner_outputs_dir / "fit_report.csv"
        weights_df.to_csv(weights_path, index=False, encoding="utf-8-sig")
        report_df.to_csv(report_path, index=False, encoding="utf-8-sig")
        outputs.extend([str(weights_path), str(report_path)])

    return outputs


def _ensure_complete_period_weights(weights_df: pd.DataFrame, contract_df: pd.DataFrame, *, target: str) -> pd.DataFrame:
    model_names = sorted(contract_df["model_name"].dropna().astype(str).unique().tolist())
    if not model_names:
        return weights_df
    existing = {
        (str(row.task), str(row.period), str(row.model_name))
        for row in weights_df[["task", "period", "model_name"]].itertuples(index=False)
    }
    rows: list[dict[str, object]] = []
    default_weight = 1.0 / len(model_names)
    lower_bound = float(weights_df["weight_lower_bound"].iloc[0]) if "weight_lower_bound" in weights_df.columns and not weights_df.empty else -0.5
    upper_bound = float(weights_df["weight_upper_bound"].iloc[0]) if "weight_upper_bound" in weights_df.columns and not weights_df.empty else 1.2
    for period in ["1_8", "9_16", "17_24"]:
        period_has_weight = ((weights_df["task"] == target) & (weights_df["period"] == period)).any()
        if period_has_weight:
            continue
        for model_name in model_names:
            key = (target, period, model_name)
            if key in existing:
                continue
            rows.append(
                {
                    "task": target,
                    "period": period,
                    "model_name": model_name,
                    "weight": default_weight,
                    "sample_count": 0,
                    "weight_lower_bound": lower_bound,
                    "weight_upper_bound": upper_bound,
                }
            )
    if rows:
        weights_df = pd.concat([weights_df, pd.DataFrame(rows)], ignore_index=True)
    return weights_df.sort_values(["task", "period", "model_name"]).reset_index(drop=True)


def _build_forecast_long_with_truth(forecast_df: pd.DataFrame, *, target: str) -> pd.DataFrame:
    work = forecast_df.copy()
    work["时刻"] = pd.to_datetime(work["时刻"], errors="coerce")
    work["预测值"] = pd.to_numeric(work["预测值"], errors="coerce")
    work["真实值"] = pd.to_numeric(work["真实值"], errors="coerce")
    work = work.dropna(subset=["时刻", "预测值"])

    ds = pd.to_datetime(work["时刻"])
    hour_business = ds.dt.hour.replace({0: 24}).astype(int)
    return pd.DataFrame(
        {
            "task": target,
            "target_day": ds.dt.normalize().where(ds.dt.hour != 0, ds.dt.normalize() - pd.Timedelta(days=1)).dt.strftime("%Y-%m-%d"),
            "model_name": work["model_name"].astype(str),
            "ds": ds,
            "period": hour_business.map(infer_period),
            "hour_business": hour_business,
            "y_true": work["真实值"],
            "y_pred": work["预测值"],
        }
    )


def run_fuse_stage(args):
    run_date = _resolve_run_date(args)
    targets = ["dayahead", "realtime"] if args.target == "both" else [args.target]
    root = _resolve_daily_runs_root(args)
    outputs: list[str] = []

    for target in targets:
        layout = build_daily_run_layout(root, run_date, target)
        forecast_df = _collect_stage_predictions(layout, file_name="forecast_predictions.csv")
        weights_path = layout.learner_outputs_dir / "weights.csv"
        if not weights_path.exists():
            raise FileNotFoundError(f"Learner weights not found: {weights_path}")
        weights_df = pd.read_csv(weights_path, encoding="utf-8-sig")
        normalized = _build_forecast_long_with_truth(forecast_df, target=target)
        fused = _apply_fixed_weights(
            normalized,
            weights_df,
            task=target,
            test_start=run_date,
            test_end=run_date,
        )
        output_path = layout.final_dir / "fused_predictions.csv"
        fused.to_csv(output_path, index=False, encoding="utf-8-sig")
        outputs.append(str(output_path))

    return outputs


def run_classifier_stage(args):
    run_date = _resolve_run_date(args)
    if getattr(args, "target", "realtime") == "dayahead":
        return {"status": "skipped", "reason": "classifier_only_supports_realtime"}
    root = _resolve_daily_runs_root(args)
    layout = build_daily_run_layout(root, run_date, "realtime")

    compat_root = layout.target_dir / "compat_fusion"
    realtime_dir = compat_root / "realtime"
    realtime_dir.mkdir(parents=True, exist_ok=True)
    fused_src = layout.final_dir / "fused_predictions.csv"
    if not fused_src.exists():
        raise FileNotFoundError(f"Realtime fused predictions not found: {fused_src}")
    fused_dst = realtime_dir / "fused_predictions.csv"
    fused_dst.write_bytes(fused_src.read_bytes())

    project_root = Path(__file__).resolve().parents[1]
    default_clf_data = project_root / "ExtremPriceClf" / "data" / "260525.xlsx"
    clf_data_path = Path(args.clf_data) if args.clf_data else default_clf_data

    result = run_classifier_pipeline(
        fusion_work_dir=compat_root,
        project_root=project_root,
        start_date=run_date,
        end_date=run_date,
        clf_data_path=clf_data_path,
    )

    corrected_src = realtime_dir / "fused_predictions_corrected.csv"
    if corrected_src.exists():
        corrected_dst = layout.final_dir / "fused_predictions_corrected.csv"
        corrected_dst.write_bytes(corrected_src.read_bytes())
    return result


def run_full_pipeline(args):
    """One-command full pipeline: model_stage → learner_stage → fuse_stage → classifier_stage."""
    run_date = _resolve_run_date(args)
    logger.info("=" * 60)
    logger.info("FULL PIPELINE START — date=%s target=%s stage_models=%s", run_date, args.target, args.stage_models)
    logger.info("=" * 60)

    # Stage 1: Model predictions
    logger.info("── Stage 1/4: model_stage ──")
    model_outputs = run_model_stage(args)
    logger.info("model_stage produced %d output files", len(model_outputs))

    # Stage 2: Learn fusion weights
    logger.info("── Stage 2/4: learner_stage ──")
    learner_outputs = run_learner_stage(args)
    logger.info("learner_stage produced %d output files", len(learner_outputs))

    # Stage 3: Apply fusion weights
    logger.info("── Stage 3/4: fuse_stage ──")
    fuse_outputs = run_fuse_stage(args)
    logger.info("fuse_stage produced %d output files", len(fuse_outputs))

    # Stage 4: Classifier (only for realtime)
    logger.info("── Stage 4/4: classifier_stage ──")
    if args.target in ("both", "realtime"):
        classifier_result = run_classifier_stage(args)
        logger.info("classifier_stage result: %s", classifier_result)
    else:
        logger.info("classifier_stage skipped (target=dayahead only)")
        classifier_result = {"status": "skipped", "reason": "dayahead_only"}

    logger.info("=" * 60)
    logger.info("FULL PIPELINE COMPLETE — date=%s", run_date)
    logger.info("  model outputs: %d files", len(model_outputs))
    logger.info("  learner outputs: %d files", len(learner_outputs))
    logger.info("  fuse outputs: %d files", len(fuse_outputs))
    logger.info("  classifier: %s", classifier_result)
    logger.info("=" * 60)

    return {
        "model_stage": model_outputs,
        "learner_stage": learner_outputs,
        "fuse_stage": fuse_outputs,
        "classifier_stage": classifier_result,
    }
