from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


TIMESTAMP_COL = "时刻"
DA_COL = "日前电价"
RT_COL = "实时电价"

FORECAST_COLS = [
    "地方电厂总加预测值",
    "联络线受电负荷预测值",
    "风电总加预测值",
    "光伏总加预测值",
    "核电总加预测值",
    "自备机组总加预测值",
    "试验机组总加预测值",
    "直调负荷预测值",
    "竞价空间预测值",
    "新能源总加预测值",
]

ACTUAL_COLS = [
    "地方电厂总加实际值",
    "联络线受电负荷实际值",
    "风电总加实际值",
    "光伏总加实际值",
    "核电总加实际值",
    "自备机组总加实际值",
    "试验机组总加实际值",
    "直调负荷实际值",
    "竞价空间实际值",
    "新能源总加实际值",
]

FORECAST_LOCAL_COL = "地方电厂总加预测值"
FORECAST_LINK_COL = "联络线受电负荷预测值"
FORECAST_LOAD_COL = "直调负荷预测值"
FORECAST_SPACE_COL = "竞价空间预测值"
FORECAST_RENEWABLE_COL = "新能源总加预测值"

ACTUAL_LOAD_COL = "直调负荷实际值"
ACTUAL_SPACE_COL = "竞价空间实际值"
ACTUAL_RENEWABLE_COL = "新能源总加实际值"

ACTUAL_TO_FORECAST_MAP = dict(zip(ACTUAL_COLS, FORECAST_COLS))

REQUIRED_COLUMNS = [TIMESTAMP_COL, DA_COL, RT_COL, *FORECAST_COLS, *ACTUAL_COLS]


@dataclass
class FeatureConfig:
    include_forecast_columns: bool = True
    include_actual_history_columns: bool = True
    use_visible_actual_history: bool = True
    include_delta_history_features: bool = True
    include_tf_moving_average_features: bool = False
    include_static_group_graph_features: bool = False
    include_weekly_history_features: bool = False
    include_forecast_residual_history_features: bool = False
    include_segment_local_stats: bool = False
    include_forecast_pressure_interactions: bool = False
    include_calendar_features: bool = True
    include_engineered_forecast_features: bool = True
    forecast_feature_columns: list[str] = field(default_factory=lambda: FORECAST_COLS.copy())


def validate_required_columns(df: pd.DataFrame) -> list[str]:
    return [col for col in REQUIRED_COLUMNS if col not in df.columns]


def load_dataset(path: str | Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    missing = validate_required_columns(df)
    if missing:
        raise ValueError(f"Dataset missing required columns: {missing}")
    df[TIMESTAMP_COL] = pd.to_datetime(df[TIMESTAMP_COL])
    df = df.sort_values(TIMESTAMP_COL).reset_index(drop=True)
    return df


def _segment_from_hour(hour: int) -> str:
    if 1 <= hour <= 8:
        return "1_8"
    if 9 <= hour <= 16:
        return "9_16"
    return "17_24"


def _season_bucket(month_series: pd.Series) -> pd.Series:
    mapping = {
        12: "winter",
        1: "winter",
        2: "winter",
        3: "spring",
        4: "spring",
        5: "spring",
        6: "summer",
        7: "summer",
        8: "summer",
        9: "autumn",
        10: "autumn",
        11: "autumn",
    }
    return month_series.map(mapping)


def add_business_time_columns(frame: pd.DataFrame, timestamp_col: str = "timestamp") -> pd.DataFrame:
    out = frame.copy()
    ts = pd.to_datetime(out[timestamp_col])
    out["business_day"] = (ts - pd.to_timedelta((ts.dt.hour == 0).astype(int), unit="D")).dt.normalize()
    out["target_hour"] = ts.dt.hour.replace({0: 24}).astype(int)
    return out


def _safe_delta_history(delta: pd.Series, lag_hours: int = 24) -> pd.Series:
    """
    Use previous-day aligned delta history so D-day post-cutoff RT truth never
    backflows into D+1 features through adjacent-hour shifts.
    """
    return pd.to_numeric(delta, errors="coerce").shift(lag_hours)


def _safe_hourly_history(values: pd.Series, lag_hours: int = 24) -> pd.Series:
    """
    Generic cutoff-safe hourly history aligned to the previous day.
    Use this for any actual- or residual-derived intraday feature family.
    """
    return pd.to_numeric(values, errors="coerce").shift(lag_hours)


def preprocess_dataframe(
    df: pd.DataFrame,
    feature_config: FeatureConfig,
    *,
    rt_history_col: str | None = None,
    actual_history_source_map: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out[TIMESTAMP_COL])
    out["da_anchor"] = pd.to_numeric(out[DA_COL], errors="coerce")
    out["rt_actual"] = pd.to_numeric(out[RT_COL], errors="coerce")
    out["delta_target"] = out["rt_actual"] - out["da_anchor"]
    out["direction_label"] = (out["delta_target"] > 0).astype(int)
    out = add_business_time_columns(out)

    history_rt_col = RT_COL if rt_history_col is None else rt_history_col
    history_actual_map = {col: col for col in ACTUAL_COLS}
    if actual_history_source_map:
        history_actual_map.update(actual_history_source_map)
    out["_rt_history_source"] = pd.to_numeric(out[history_rt_col], errors="coerce")
    out["_delta_history_source"] = out["_rt_history_source"] - out["da_anchor"]

    ts = out["timestamp"]
    out["hour"] = out["target_hour"].astype(int)
    out["month"] = ts.dt.month.astype(int)
    out["day_of_week"] = ts.dt.dayofweek.astype(int)
    out["day_of_month"] = ts.dt.day.astype(int)
    out["is_weekend"] = (out["day_of_week"] >= 5).astype(int)
    out["segment"] = out["hour"].map(_segment_from_hour)
    out["segment_id"] = out["segment"].map({"1_8": 0, "9_16": 1, "17_24": 2}).astype(int)
    out["season_bucket"] = _season_bucket(out["month"])

    feature_cols: list[str] = []

    if feature_config.include_forecast_columns:
        for col in feature_config.forecast_feature_columns:
            safe_col = f"feat_{col}"
            out[safe_col] = pd.to_numeric(out[col], errors="coerce")
            feature_cols.append(safe_col)

    if feature_config.include_actual_history_columns:
        for col in ACTUAL_COLS:
            safe_col = f"hist_{col}_lag24"
            source_col = history_actual_map[col]
            out[safe_col] = pd.to_numeric(out[source_col], errors="coerce").shift(24)
            feature_cols.append(safe_col)

    pred_load = pd.to_numeric(out[FORECAST_LOAD_COL], errors="coerce")
    pred_space = pd.to_numeric(out[FORECAST_SPACE_COL], errors="coerce")
    pred_renewable = pd.to_numeric(out[FORECAST_RENEWABLE_COL], errors="coerce")
    pred_local = pd.to_numeric(out[FORECAST_LOCAL_COL], errors="coerce")
    pred_link = pd.to_numeric(out[FORECAST_LINK_COL], errors="coerce")
    pred_supply = pred_local + pred_link + pred_renewable
    pred_net_load = pred_load - pred_renewable

    if feature_config.include_engineered_forecast_features:
        out["feat_pred_net_load"] = pred_net_load
        out["feat_pred_supply_sum"] = pred_supply
        out["feat_pred_pressure_ratio"] = pred_space / (pred_renewable.abs() + 1.0)
        out["feat_pred_renewable_share"] = pred_renewable / (pred_load.abs() + 1.0)
        out["feat_pred_load_ramp_1"] = pred_load.diff()
        out["feat_pred_space_ramp_1"] = pred_space.diff()
        feature_cols.extend(
            [
                "feat_pred_net_load",
                "feat_pred_supply_sum",
                "feat_pred_pressure_ratio",
                "feat_pred_renewable_share",
                "feat_pred_load_ramp_1",
                "feat_pred_space_ramp_1",
            ]
        )

    if feature_config.include_forecast_pressure_interactions:
        out["feat_pred_supply_demand_gap"] = pred_supply - pred_load
        out["feat_pred_space_load_ratio"] = pred_space / (pred_load.abs() + 1.0)
        out["feat_pred_space_netload_gap"] = pred_space - pred_net_load
        out["feat_pred_space_x_da"] = pred_space * out["da_anchor"]
        out["feat_pred_gap_x_da"] = out["feat_pred_supply_demand_gap"] * out["da_anchor"]
        out["feat_pred_renewable_space_interaction"] = pred_renewable * pred_space / 1000.0
        feature_cols.extend(
            [
                "feat_pred_supply_demand_gap",
                "feat_pred_space_load_ratio",
                "feat_pred_space_netload_gap",
                "feat_pred_space_x_da",
                "feat_pred_gap_x_da",
                "feat_pred_renewable_space_interaction",
            ]
        )

    if feature_config.include_delta_history_features:
        delta = out["_delta_history_source"]
        safe_delta = _safe_delta_history(delta)
        out["delta_lag_1"] = safe_delta
        out["delta_lag_24"] = delta.shift(24)
        out["delta_roll_mean_6"] = safe_delta.rolling(6, min_periods=1).mean()
        out["delta_roll_mean_24"] = safe_delta.rolling(24, min_periods=1).mean()
        out["delta_roll_std_24"] = safe_delta.rolling(24, min_periods=2).std()
        out["delta_abs_roll_mean_24"] = safe_delta.abs().rolling(24, min_periods=1).mean()
        feature_cols.extend(
            [
                "delta_lag_1",
                "delta_lag_24",
                "delta_roll_mean_6",
                "delta_roll_mean_24",
                "delta_roll_std_24",
                "delta_abs_roll_mean_24",
            ]
        )

    if feature_config.include_tf_moving_average_features:
        lagged_delta = _safe_delta_history(out["_delta_history_source"])
        out["tf_delta_lowfreq_mean_12"] = lagged_delta.rolling(12, min_periods=4).mean()
        out["tf_delta_lowfreq_mean_24"] = lagged_delta.rolling(24, min_periods=8).mean()
        out["tf_delta_highfreq_resid_12"] = lagged_delta - out["tf_delta_lowfreq_mean_12"]
        out["tf_delta_highfreq_resid_24"] = lagged_delta - out["tf_delta_lowfreq_mean_24"]
        out["tf_delta_vol_12"] = lagged_delta.rolling(12, min_periods=4).std()
        out["tf_delta_vol_24"] = lagged_delta.rolling(24, min_periods=8).std()
        out["tf_delta_ramp_3"] = lagged_delta.diff(3)
        out["tf_delta_ramp_6"] = lagged_delta.diff(6)
        out["tf_delta_same_hour_lowfreq_7d"] = out.groupby("hour")["_delta_history_source"].transform(
            lambda s: s.shift(1).rolling(7, min_periods=3).mean()
        )
        out["tf_delta_same_hour_highfreq_7d"] = out["delta_lag_1"] - out["tf_delta_same_hour_lowfreq_7d"]
        feature_cols.extend(
            [
                "tf_delta_lowfreq_mean_12",
                "tf_delta_lowfreq_mean_24",
                "tf_delta_highfreq_resid_12",
                "tf_delta_highfreq_resid_24",
                "tf_delta_vol_12",
                "tf_delta_vol_24",
                "tf_delta_ramp_3",
                "tf_delta_ramp_6",
                "tf_delta_same_hour_lowfreq_7d",
                "tf_delta_same_hour_highfreq_7d",
            ]
        )

    if feature_config.include_static_group_graph_features:
        out["graph_group_da_pressure_gap"] = out["da_anchor"] - pred_space
        out["graph_group_load_supply_gap"] = pred_load - pred_supply
        out["graph_group_load_renewable_gap"] = pred_load - pred_renewable
        out["graph_group_pressure_x_riskhour"] = pred_space * out["hour"].isin([9, 10, 15]).astype(int)
        out["graph_group_deltahist_x_pressure"] = out["delta_roll_mean_24"] * pred_space
        out["graph_group_deltahist_x_loadgap"] = out["delta_roll_mean_24"] * out["graph_group_load_supply_gap"]
        out["graph_group_renewable_share_x_hour"] = pred_renewable / (pred_load.abs() + 1.0) * out["hour"]
        out["graph_group_da_x_segment"] = out["da_anchor"] * (out["segment_id"] + 1)
        feature_cols.extend(
            [
                "graph_group_da_pressure_gap",
                "graph_group_load_supply_gap",
                "graph_group_load_renewable_gap",
                "graph_group_pressure_x_riskhour",
                "graph_group_deltahist_x_pressure",
                "graph_group_deltahist_x_loadgap",
                "graph_group_renewable_share_x_hour",
                "graph_group_da_x_segment",
            ]
        )

    if feature_config.include_weekly_history_features:
        delta = out["_delta_history_source"]
        out["delta_lag_168"] = delta.shift(168)
        out["delta_roll_mean_168"] = _safe_delta_history(delta).rolling(168, min_periods=24).mean()
        out["da_lag_24"] = out["da_anchor"].shift(24)
        out["da_lag_168"] = out["da_anchor"].shift(168)
        out["rt_lag_168"] = out["_rt_history_source"].shift(168)
        feature_cols.extend(
            [
                "delta_lag_168",
                "delta_roll_mean_168",
                "da_lag_24",
                "da_lag_168",
                "rt_lag_168",
            ]
        )

    if feature_config.include_forecast_residual_history_features:
        actual_load = pd.to_numeric(out[history_actual_map[ACTUAL_LOAD_COL]], errors="coerce")
        actual_space = pd.to_numeric(out[history_actual_map[ACTUAL_SPACE_COL]], errors="coerce")
        actual_renewable = pd.to_numeric(out[history_actual_map[ACTUAL_RENEWABLE_COL]], errors="coerce")
        load_resid = actual_load - pred_load
        renewable_resid = actual_renewable - pred_renewable
        space_resid = actual_space - pred_space
        netload_resid = (actual_load - actual_renewable) - pred_net_load
        out["hist_load_resid_lag24"] = load_resid.shift(24)
        out["hist_renewable_resid_lag24"] = renewable_resid.shift(24)
        out["hist_space_resid_lag24"] = space_resid.shift(24)
        out["hist_netload_resid_lag24"] = netload_resid.shift(24)
        out["hist_load_resid_roll_mean_24"] = _safe_hourly_history(load_resid).rolling(24, min_periods=6).mean()
        out["hist_netload_resid_roll_mean_24"] = _safe_hourly_history(netload_resid).rolling(24, min_periods=6).mean()
        feature_cols.extend(
            [
                "hist_load_resid_lag24",
                "hist_renewable_resid_lag24",
                "hist_space_resid_lag24",
                "hist_netload_resid_lag24",
                "hist_load_resid_roll_mean_24",
                "hist_netload_resid_roll_mean_24",
            ]
        )
    else:
        netload_resid = pd.Series(np.nan, index=out.index, dtype=float)
        space_resid = pd.Series(np.nan, index=out.index, dtype=float)

    if feature_config.include_segment_local_stats:
        safe_same_hour_delta = out.groupby("hour")["_delta_history_source"].shift(1)
        out["delta_same_hour_roll_mean_7d"] = out.groupby("hour")["_delta_history_source"].transform(
            lambda s: s.shift(1).rolling(7, min_periods=2).mean()
        )
        out["delta_same_hour_roll_std_7d"] = out.groupby("hour")["_delta_history_source"].transform(
            lambda s: s.shift(1).rolling(7, min_periods=3).std()
        )
        out["delta_same_hour_abs_roll_mean_7d"] = out.groupby("hour")["_delta_history_source"].transform(
            lambda s: s.abs().shift(1).rolling(7, min_periods=2).mean()
        )
        out["delta_same_hour_lag_1d"] = safe_same_hour_delta
        if feature_config.include_forecast_residual_history_features:
            out["netload_resid_same_hour_roll_mean_7d"] = netload_resid.groupby(out["hour"]).transform(
                lambda s: s.shift(1).rolling(7, min_periods=2).mean()
            )
            out["space_resid_same_hour_roll_mean_7d"] = space_resid.groupby(out["hour"]).transform(
                lambda s: s.shift(1).rolling(7, min_periods=2).mean()
            )
        else:
            out["netload_resid_same_hour_roll_mean_7d"] = np.nan
            out["space_resid_same_hour_roll_mean_7d"] = np.nan
        feature_cols.extend(
            [
                "delta_same_hour_lag_1d",
                "delta_same_hour_roll_mean_7d",
                "delta_same_hour_roll_std_7d",
                "delta_same_hour_abs_roll_mean_7d",
                "netload_resid_same_hour_roll_mean_7d",
                "space_resid_same_hour_roll_mean_7d",
            ]
        )

    if feature_config.include_calendar_features:
        feature_cols.extend(["hour", "month", "day_of_week", "day_of_month", "is_weekend", "segment_id", "da_anchor"])

    return out, feature_cols


def build_feature_manifest(feature_columns: Iterable[str]) -> pd.DataFrame:
    rows = []
    for col in feature_columns:
        if col.startswith("feat_") or col.startswith("graph_group_"):
            source = "forecast_or_engineered_forecast"
        elif col.startswith("hist_"):
            source = "actual_history_shifted"
        elif col.startswith("delta_") or col.startswith("tf_"):
            source = "historical_delta_shifted"
        else:
            source = "calendar_or_anchor"
        rows.append({"feature_name": col, "source_family": source})
    return pd.DataFrame(rows)
