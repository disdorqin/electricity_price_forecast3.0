import datetime
import gc
import logging
import os

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error

from lightGBM.infer_da_fix import PowerInference as PowerInferenceDA
from lightGBM.infer_fix import PowerInference
from lightGBM.train_da_fix import LGBMPowerPredictor as LGBMPowerPredictorDA
from lightGBM.train_da_fix import ThreeStageLGBM as ThreeStageLGBMDA
from lightGBM.train_fix import LGBMPowerPredictor
from lightGBM.train_fix import ThreeStageLGBM

logger = logging.getLogger(__name__)


VALLEY_HOURS = [1, 2, 3, 4, 5, 6, 7, 8]
SOLAR_HOURS = [9, 10, 11, 12, 13, 14, 15, 16]
PEAK_HOURS = [17, 18, 19, 20, 21, 22, 23, 24]


def _split_history_train_val(history_df, val_ratio=0.2, min_val_rows=24 * 7):
    history_df = history_df.sort_values("ds").copy()
    if history_df.empty:
        raise RuntimeError("LightGBM history window is empty.")
    val_rows = max(int(len(history_df) * float(val_ratio)), int(min_val_rows))
    val_rows = min(val_rows, max(1, len(history_df) - 1))
    split_idx = len(history_df) - val_rows
    train_df = history_df.iloc[:split_idx].copy()
    val_df = history_df.iloc[split_idx:].copy()
    if train_df.empty or val_df.empty:
        raise RuntimeError("LightGBM chronological train/val split failed.")
    return train_df, val_df


def validate_business_day_filled(raw_df, business_day):
    day_start = pd.to_datetime(f"{business_day} 01:00:00")
    day_end = pd.to_datetime(f"{business_day} 23:00:00")
    day_mask = (raw_df["ds"] >= day_start) & (raw_df["ds"] <= day_end)
    day_df = raw_df.loc[day_mask, ["ds", "y"]].copy()
    if day_df.empty:
        raise ValueError(f"Missing business-day rows for {business_day}.")
    y_nan_mask = day_df["y"].isna()
    if y_nan_mask.any():
        bad_times = day_df.loc[y_nan_mask, "ds"].head(5).dt.strftime("%Y-%m-%d %H:%M:%S").tolist()
        raise ValueError(f"Business day {business_day} still contains NaN targets: {bad_times}")


def _fit_realtime_fixed_window(
    predictor,
    data_path,
    history_start_date,
    history_end_date,
    target,
    raw_df=None,
    val_ratio=0.2,
):
    raw_df = raw_df.copy() if raw_df is not None else predictor.load_and_process_data(data_path, target)
    full_df = predictor.feature_engineering(raw_df)
    history_start_dt = pd.to_datetime(history_start_date)
    history_end_dt = pd.to_datetime(history_end_date)
    history_mask = (full_df["ds"] >= history_start_dt) & (full_df["ds"] <= history_end_dt)
    history_df = full_df[history_mask].copy()
    if len(history_df) < 2000:
        raise RuntimeError("Realtime LightGBM fixed-window training set is too small.")

    train_df, test_df_raw = _split_history_train_val(history_df, val_ratio=val_ratio)
    predictor.validate_optimize_dataset(
        test_df_raw,
        str(test_df_raw["ds"].min()),
        str(test_df_raw["ds"].max()),
    )

    train_upper = train_df["y"].quantile(0.995)
    train_df["y_clipped"] = train_df["y"].clip(lower=-100, upper=train_upper)

    train_valley = train_df[train_df["hour"].isin(VALLEY_HOURS)]
    test_valley = test_df_raw[test_df_raw["hour"].isin(VALLEY_HOURS)]
    model_valley_reg = predictor._fit_with_cuda_fallback(
        lgb.LGBMRegressor(
            objective="regression",
            n_estimators=2000,
            learning_rate=0.05,
            num_leaves=31,
            n_jobs=predictor.lgbm_n_jobs,
            device_type=predictor._device_type(),
            verbose=-1,
            random_state=42,
        ),
        train_valley[predictor.features_list],
        train_valley["y_clipped"],
        eval_set=[(test_valley[predictor.features_list], test_valley["y"])],
        eval_metric="l1",
        callbacks=[lgb.early_stopping(100, verbose=False), lgb.log_evaluation(0)],
    )

    train_solar = train_df[train_df["hour"].isin(SOLAR_HOURS)]
    test_solar = test_df_raw[test_df_raw["hour"].isin(SOLAR_HOURS)]
    w_solar = np.ones(len(train_solar))
    y_solar_val = train_solar["y_clipped"].values
    w_solar[y_solar_val < 50] = 2
    w_solar[y_solar_val < 0] = 5
    model_solar_reg = predictor._fit_with_cuda_fallback(
        lgb.LGBMRegressor(
            objective="regression",
            n_estimators=3000,
            learning_rate=0.03,
            num_leaves=63,
            n_jobs=predictor.lgbm_n_jobs,
            device_type=predictor._device_type(),
            verbose=-1,
            random_state=42,
        ),
        train_solar[predictor.features_list],
        train_solar["y_clipped"],
        sample_weight=w_solar,
        eval_set=[(test_solar[predictor.features_list], test_solar["y"])],
        eval_metric="l1",
        callbacks=[lgb.early_stopping(100, verbose=False), lgb.log_evaluation(0)],
    )

    y_solar_class = (y_solar_val < 0).astype(int)
    y_solar_test_class = (test_solar["y"] < 0).astype(int)
    model_solar_clf = predictor._fit_with_cuda_fallback(
        lgb.LGBMClassifier(
            objective="binary",
            n_estimators=1000,
            learning_rate=0.05,
            class_weight="balanced",
            n_jobs=predictor.lgbm_n_jobs,
            device_type=predictor._device_type(),
            verbose=-1,
            random_state=42,
        ),
        train_solar[predictor.features_list],
        y_solar_class,
        eval_set=[(test_solar[predictor.features_list], y_solar_test_class)],
        eval_metric="auc",
        callbacks=[lgb.early_stopping(100, verbose=False), lgb.log_evaluation(0)],
    )

    train_peak = train_df[train_df["hour"].isin(PEAK_HOURS)]
    test_peak = test_df_raw[test_df_raw["hour"].isin(PEAK_HOURS)]
    w_peak = np.ones(len(train_peak))
    high_wind_threshold = train_peak["wind"].quantile(0.8)
    w_peak[train_peak["wind"] > high_wind_threshold] = 3
    model_peak_reg = predictor._fit_with_cuda_fallback(
        lgb.LGBMRegressor(
            objective="regression",
            n_estimators=3000,
            learning_rate=0.03,
            num_leaves=40,
            n_jobs=predictor.lgbm_n_jobs,
            device_type=predictor._device_type(),
            verbose=-1,
            random_state=42,
        ),
        train_peak[predictor.features_list],
        train_peak["y_clipped"],
        sample_weight=w_peak,
        eval_set=[(test_peak[predictor.features_list], test_peak["y"])],
        eval_metric="l1",
        callbacks=[lgb.early_stopping(100, verbose=False), lgb.log_evaluation(0)],
    )

    combined_model = ThreeStageLGBM(model_valley_reg, model_solar_reg, model_solar_clf, model_peak_reg)
    pred = combined_model.predict(test_df_raw[predictor.features_list])
    pred = np.where(pred < -80, -80, pred)
    mae = mean_absolute_error(test_df_raw["y"], pred)
    smape = predictor.calculate_smape(test_df_raw["y"], pred)
    predictor.save_model(
        [{"months_back": 12, "train_start": history_start_dt, "mae": mae, "smape": smape, "model": combined_model}],
        target,
    )
    return {"months_back": 12, "mae": mae, "smape": smape, "model": combined_model}


def _fit_dayahead_fixed_window(
    predictor,
    data_path,
    history_start_date,
    history_end_date,
    raw_df=None,
    val_ratio=0.2,
):
    if raw_df is None:
        raw_df = predictor.load_and_process_data(data_path)
    full_df = predictor.feature_engineering(raw_df)
    history_start_dt = pd.to_datetime(history_start_date)
    history_end_dt = pd.to_datetime(history_end_date)
    history_mask = (full_df["ds"] >= history_start_dt) & (full_df["ds"] <= history_end_dt)
    history_df = full_df[history_mask].copy()
    if len(history_df) < 2000:
        raise RuntimeError("Day-ahead LightGBM fixed-window training set is too small.")

    train_df, test_df_raw = _split_history_train_val(history_df, val_ratio=val_ratio)

    train_upper = train_df["y"].quantile(0.995)
    train_df["y_clipped"] = train_df["y"].clip(lower=-100, upper=train_upper)

    train_valley = train_df[train_df["hour"].isin(VALLEY_HOURS)]
    test_valley = test_df_raw[test_df_raw["hour"].isin(VALLEY_HOURS)]
    model_valley_reg = predictor._fit_with_cuda_fallback(
        lgb.LGBMRegressor(
            objective="regression",
            n_estimators=2000,
            learning_rate=0.05,
            num_leaves=31,
            n_jobs=predictor.lgbm_n_jobs,
            device_type=predictor._device_type(),
            verbose=-1,
            random_state=42,
        ),
        train_valley[predictor.features_list],
        train_valley["y_clipped"],
        eval_set=[(test_valley[predictor.features_list], test_valley["y"])],
        eval_metric="l1",
        callbacks=[lgb.early_stopping(100, verbose=False), lgb.log_evaluation(0)],
    )

    train_solar = train_df[train_df["hour"].isin(SOLAR_HOURS)]
    test_solar = test_df_raw[test_df_raw["hour"].isin(SOLAR_HOURS)]
    model_solar_reg = predictor._fit_with_cuda_fallback(
        lgb.LGBMRegressor(
            objective="regression",
            n_estimators=3000,
            learning_rate=0.03,
            num_leaves=63,
            n_jobs=predictor.lgbm_n_jobs,
            device_type=predictor._device_type(),
            verbose=-1,
            random_state=42,
        ),
        train_solar[predictor.features_list],
        train_solar["y_clipped"],
        eval_set=[(test_solar[predictor.features_list], test_solar["y"])],
        eval_metric="l1",
        callbacks=[lgb.early_stopping(100, verbose=False), lgb.log_evaluation(0)],
    )

    y_solar_class = (train_solar["y_clipped"] < 0).astype(int)
    y_solar_test_class = (test_solar["y"] < 0).astype(int)
    model_solar_clf = predictor._fit_with_cuda_fallback(
        lgb.LGBMClassifier(
            objective="binary",
            n_estimators=1000,
            learning_rate=0.05,
            class_weight="balanced",
            n_jobs=predictor.lgbm_n_jobs,
            device_type=predictor._device_type(),
            verbose=-1,
            random_state=42,
        ),
        train_solar[predictor.features_list],
        y_solar_class,
        eval_set=[(test_solar[predictor.features_list], y_solar_test_class)],
        eval_metric="auc",
        callbacks=[lgb.early_stopping(100, verbose=False), lgb.log_evaluation(0)],
    )

    train_peak = train_df[train_df["hour"].isin(PEAK_HOURS)]
    test_peak = test_df_raw[test_df_raw["hour"].isin(PEAK_HOURS)]
    model_peak_reg = predictor._fit_with_cuda_fallback(
        lgb.LGBMRegressor(
            objective="regression",
            n_estimators=3000,
            learning_rate=0.03,
            num_leaves=40,
            n_jobs=predictor.lgbm_n_jobs,
            device_type=predictor._device_type(),
            verbose=-1,
            random_state=42,
        ),
        train_peak[predictor.features_list],
        train_peak["y_clipped"],
        eval_set=[(test_peak[predictor.features_list], test_peak["y"])],
        eval_metric="l1",
        callbacks=[lgb.early_stopping(100, verbose=False), lgb.log_evaluation(0)],
    )

    combined_model = ThreeStageLGBMDA(model_valley_reg, model_solar_reg, model_solar_clf, model_peak_reg)
    pred = combined_model.predict(test_df_raw[predictor.features_list])
    pred = np.where(pred < -80, -80, pred)
    mae = mean_absolute_error(test_df_raw["y"], pred)
    smape = predictor.calculate_smape(test_df_raw["y"], pred)
    predictor.save_model(
        [{"months_back": 12, "train_start": history_start_dt, "mae": mae, "smape": smape, "model": combined_model}],
        "日前电价",
    )
    return {"months_back": 12, "mae": mae, "smape": smape, "model": combined_model}


def run_precision_simulation(
    data_path,
    forecast_start,
    forecast_end,
    target="实时电价",
    use_predicted_temp=False,
    training_months=12,
    val_ratio=0.2,
):
    predictor = LGBMPowerPredictor()
    inference = PowerInference(model_path=None)
    requested_start_date = pd.to_datetime(forecast_start)
    current_target_date = requested_start_date
    end_target_date = pd.to_datetime(forecast_end)
    working_raw_df = None

    if use_predicted_temp:
        working_raw_df = inference.load_and_process_data(data_path, target)
        last_actual_business_day = inference.get_last_actual_business_day(working_raw_df)
        recursive_start_date = min(
            requested_start_date - datetime.timedelta(days=1),
            last_actual_business_day + datetime.timedelta(days=1),
        )
        if recursive_start_date < current_target_date:
            current_target_date = recursive_start_date

    all_days_preds = []
    while current_target_date <= end_target_date:
        target_day_str = current_target_date.strftime("%Y-%m-%d")
        decision_day_dt = current_target_date - datetime.timedelta(days=1)
        val_end_str = decision_day_dt.strftime("%Y-%m-%d 14:00:00")
        val_start_str = (decision_day_dt - pd.DateOffset(months=int(training_months))).strftime("%Y-%m-%d 01:00:00")

        best_res = None
        try:
            best_res = _fit_realtime_fixed_window(
                predictor=predictor,
                data_path=data_path,
                history_start_date=val_start_str,
                history_end_date=val_end_str,
                target=target,
                raw_df=working_raw_df,
                val_ratio=val_ratio,
            )
            inference_start = current_target_date.strftime("%Y-%m-%d 01:00:00")
            inference_end = (current_target_date + datetime.timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")
            inference.model = best_res["model"]

            if use_predicted_temp:
                day_use_predicted_temp = current_target_date > recursive_start_date
                day_result_df, working_raw_df = inference.predict_range(
                    data_path,
                    inference_start,
                    inference_end,
                    target=target,
                    raw_df=working_raw_df,
                    use_predicted_temp=day_use_predicted_temp,
                    update_history_with_predictions=True,
                )
            else:
                day_result_df = inference.predict_range(data_path, inference_start, inference_end, target=target)

            if day_result_df is not None and current_target_date >= requested_start_date:
                day_result_df["target_day"] = target_day_str
                day_result_df["best_window"] = int(training_months)
                day_result_df["use_predicted_temp"] = int(use_predicted_temp)
                all_days_preds.append(day_result_df)

            if use_predicted_temp and working_raw_df is not None:
                validate_business_day_filled(working_raw_df, target_day_str)
        except Exception as e:
            logger.error("%s failed: %s", target_day_str, e, exc_info=True)

        current_target_date += datetime.timedelta(days=1)
        if best_res is not None:
            del best_res
        gc.collect()

    if all_days_preds:
        return pd.concat(all_days_preds, axis=0)
    return None


def run_precision_simulation_da(
    data_path,
    forecast_start,
    forecast_end,
    target="日前电价",
    training_months=12,
    val_ratio=0.2,
):
    predictor = LGBMPowerPredictorDA()
    inference = PowerInferenceDA(model_path=None)
    requested_start_date = pd.to_datetime(forecast_start)
    current_target_date = requested_start_date
    end_target_date = pd.to_datetime(forecast_end)
    all_days_preds = []

    history_end_str = requested_start_date.strftime("%Y-%m-%d 00:00:00")
    history_start_str = (requested_start_date - pd.DateOffset(months=int(training_months))).strftime("%Y-%m-%d 01:00:00")

    best_res = None
    try:
        raw_df = predictor.load_and_process_data(data_path)
        best_res = _fit_dayahead_fixed_window(
            predictor=predictor,
            data_path=data_path,
            history_start_date=history_start_str,
            history_end_date=history_end_str,
            raw_df=raw_df,
            val_ratio=val_ratio,
        )
        inference.model = best_res["model"]

        while current_target_date <= end_target_date:
            target_day_str = current_target_date.strftime("%Y-%m-%d")
            try:
                inference_start = current_target_date.strftime("%Y-%m-%d 01:00:00")
                inference_end = (current_target_date + datetime.timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")
                day_result_df = inference.predict_range(data_path, inference_start, inference_end, target=target, raw_df=raw_df)

                if day_result_df is not None:
                    day_result_df["target_day"] = target_day_str
                    day_result_df["best_window"] = int(training_months)
                    all_days_preds.append(day_result_df)
            except Exception as e:
                logger.error("[%s] dayahead predict failed: %s", target_day_str, e, exc_info=True)

            current_target_date += datetime.timedelta(days=1)
            gc.collect()
    except Exception as e:
        logger.error("dayahead batch fit failed: %s", e, exc_info=True)
    finally:
        if best_res is not None:
            del best_res
        gc.collect()

    if all_days_preds:
        return pd.concat(all_days_preds, axis=0)
    return None


def run_lgbm_pipeline(
    data_path,
    forecast_start,
    forecast_end,
    target="实时电价",
    use_predicted_temp=False,
    training_months=12,
    val_ratio=0.2,
):
    if "日前" in target:
        return run_precision_simulation_da(
            data_path=data_path,
            forecast_start=forecast_start,
            forecast_end=forecast_end,
            target=target,
            training_months=training_months,
            val_ratio=val_ratio,
        )
    return run_precision_simulation(
        data_path=data_path,
        forecast_start=forecast_start,
        forecast_end=forecast_end,
        target=target,
        use_predicted_temp=use_predicted_temp,
        training_months=training_months,
        val_ratio=val_ratio,
    )


if __name__ == "__main__":
    PROJECT_ROOT = os.getenv("PROJECT_ROOT", ".")
    DATA_SET_NAME = os.getenv("DATA_SET_NAME")
    data_path = os.path.join(PROJECT_ROOT, DATA_SET_NAME)
    result = run_lgbm_pipeline(
        data_path=data_path,
        forecast_start="2026-02-01",
        forecast_end="2026-02-03",
        target="实时电价",
        use_predicted_temp=True,
        training_months=12,
        val_ratio=0.2,
    )
    if result is not None:
        result.to_csv("test.csv", index=False)
        print(result)
