from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import r2_score
from sklearn.preprocessing import StandardScaler
from torch.utils.data import DataLoader, Dataset

from TimeMixer.backbones import build_backbone


MODEL_NAME = "TimeMixer"


@dataclass
class RunConfig:
    data_path: str
    output_dir: str
    month: str
    test_start: str | None = None
    test_end_exclusive: str | None = None
    pipeline_mode: str = "single_task"
    backbone: str = "timemixer"
    rt_916_backbone: str | None = None
    train_months: int = 12
    val_ratio: float = 0.2
    training_mode: str = "rolling"
    frozen_train_start: str | None = None
    frozen_train_end_exclusive: str | None = None
    decomposition_mode: str = "none"
    seq_len: int = 168
    epochs: int = 30
    batch_size: int = 16
    hidden_dim: int = 64
    blocks: int = 2
    scales: int = 3
    dropout: float = 0.1
    rt_segment_head_mode: str = "none"
    lr: float = 1e-3
    weight_decay: float = 1e-4
    patience: int = 15
    seed: int = 42
    device: str = "auto"
    cutoff_hour_da: int = 15
    cutoff_hour_rt: int = 15
    segment_training: bool = True
    target_mode: str = "direct"
    da_target_mode: str | None = None
    rt_target_mode: str | None = None
    da_calibration_mode: str = "none"
    da_loss_mode: str = "l1"
    da_under_weight_multiplier: float = 1.25
    rt_calibration_mode: str = "none"
    rt_loss_mode: str = "l1"
    rt_risk_profile: str = "baseline"
    rt_peak_weight_multiplier: float = 1.4
    rt_normal_focus_multiplier: float = 1.2
    calibration_shrink: float = 0.5
    affine_clip_min: float = 0.7
    affine_clip_max: float = 1.3
    regime_solar_ratio_threshold: float = 0.28
    regime_bidding_ratio_threshold: float = 0.08
    regime_bidding_space_threshold: float = 4000.0
    peak_da_threshold: float = 300.0
    peak_bidding_space_threshold: float = 22000.0
    peak_solar_ratio_max: float = 0.22
    append_leaderboard: bool = True
    leaderboard_path: str = "TimeMixer/outputs_v2/serial_keepdrop/leaderboard.csv"


class ElectricityDailyDataset(Dataset):
    def __init__(self, past_arr: np.ndarray, future_arr: np.ndarray, y_arr: np.ndarray):
        self.past = torch.tensor(past_arr, dtype=torch.float32)
        self.future = torch.tensor(future_arr, dtype=torch.float32)
        self.y = torch.tensor(y_arr, dtype=torch.float32)

    def __len__(self) -> int:
        return len(self.y)

    def __getitem__(self, idx: int):
        return self.past[idx], self.future[idx], self.y[idx]


SEGMENTS: list[tuple[str, int, int]] = [
    ("1_8", 0, 8),
    ("9_16", 8, 16),
    ("17_24", 16, 24),
]


def set_seed(seed: int = 42) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def read_csv_safely(path: str) -> pd.DataFrame:
    for enc in ["gbk", "utf-8-sig", "utf-8"]:
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path)


def load_data(data_path: str) -> pd.DataFrame:
    df = read_csv_safely(data_path)
    rename_map = {
        "时刻": "ds",
        "日前电价": "day_ahead_clearing_price",
        "日前出清价": "day_ahead_clearing_price",
        "实时电价": "realtime_price",
        "直调负荷预测值": "load",
        "风电总加预测值": "wind",
        "光伏总加预测值": "solar",
        "联络线受电负荷预测值": "interconnect",
        "竞价空间预测值": "bidding_space",
        "新能源总加预测值": "renewable",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    required = [
        "ds",
        "day_ahead_clearing_price",
        "realtime_price",
        "load",
        "wind",
        "solar",
        "interconnect",
        "bidding_space",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"数据缺少必要字段: {missing}")
    if "renewable" not in df.columns:
        df["renewable"] = df["wind"] + df["solar"]

    df = df[
        [
            "ds",
            "day_ahead_clearing_price",
            "realtime_price",
            "load",
            "wind",
            "solar",
            "interconnect",
            "bidding_space",
            "renewable",
        ]
    ].copy()
    df["ds"] = pd.to_datetime(df["ds"])
    for col in df.columns:
        if col != "ds":
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("ds").drop_duplicates("ds").reset_index(drop=True)
    exog_cols = ["load", "wind", "solar", "interconnect", "bidding_space", "renewable"]
    df[exog_cols] = df[exog_cols].ffill().fillna(0)
    return df


def business_hour(ts: pd.Timestamp) -> int:
    hour = pd.Timestamp(ts).hour
    return 24 if hour == 0 else hour


def assign_period(hour_business: int) -> str:
    if 1 <= hour_business <= 8:
        return "1_8"
    if 9 <= hour_business <= 16:
        return "9_16"
    return "17_24"


def smape(pred: np.ndarray, true: np.ndarray) -> float:
    pred = np.asarray(pred, dtype=float).copy()
    true = np.asarray(true, dtype=float).copy()
    pred[pred < 50] = 50
    true[true < 50] = 50
    denom = (np.abs(pred) + np.abs(true)) / 2
    return float(np.mean(np.abs(pred - true) / denom) * 100)


def date_range_days(start: pd.Timestamp, end_exclusive: pd.Timestamp) -> list[pd.Timestamp]:
    return list(
        pd.date_range(
            pd.Timestamp(start),
            pd.Timestamp(end_exclusive) - pd.Timedelta(days=1),
            freq="D",
        )
    )


def month_bounds(month: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(f"{month}-01")
    return start, start + pd.offsets.MonthBegin(1)


def resolve_test_window(cfg: RunConfig) -> tuple[pd.Timestamp, pd.Timestamp]:
    if cfg.test_start and cfg.test_end_exclusive:
        return pd.Timestamp(cfg.test_start), pd.Timestamp(cfg.test_end_exclusive)
    return month_bounds(cfg.month)


def compute_cutoff(target_day: pd.Timestamp, cutoff_hour: int) -> pd.Timestamp:
    return target_day - pd.Timedelta(days=1) + pd.Timedelta(hours=cutoff_hour)


def resolve_task_target_mode(cfg: RunConfig, task: str) -> str:
    if task == "da" and cfg.da_target_mode:
        return cfg.da_target_mode
    if task == "rt" and cfg.rt_target_mode:
        return cfg.rt_target_mode
    return cfg.target_mode


def compute_blend_baseline(
    df: pd.DataFrame,
    target_day: pd.Timestamp,
    target_col: str,
    blend_spec: tuple[tuple[int, float], ...] = ((1, 0.60), (7, 0.25), (14, 0.15)),
) -> np.ndarray:
    cur = df[(df["ds"] >= target_day) & (df["ds"] < target_day + pd.Timedelta(days=1))].copy()
    if len(cur) != 24:
        raise ValueError(f"{target_day.date()} 不足 24 小时")
    weighted_sum = np.zeros(24, dtype=float)
    weight_sum = np.zeros(24, dtype=float)
    idx = df.set_index("ds")
    for lag_days, weight in blend_spec:
        lag_vals = idx.reindex(cur["ds"] - pd.Timedelta(days=lag_days))[target_col].to_numpy(float)
        mask = ~np.isnan(lag_vals)
        weighted_sum[mask] += weight * lag_vals[mask]
        weight_sum[mask] += weight
    fallback = cur["day_ahead_clearing_price"].to_numpy(float) if target_col == "realtime_price" else np.nan
    baseline = np.divide(
        weighted_sum,
        np.where(weight_sum == 0, 1.0, weight_sum),
        out=np.zeros_like(weighted_sum),
        where=weight_sum > 0,
    )
    if target_col == "realtime_price":
        baseline = np.where(weight_sum > 0, baseline, fallback)
    else:
        same_hour_prev_day = idx.reindex(cur["ds"] - pd.Timedelta(days=1))[target_col].to_numpy(float)
        baseline = np.where(weight_sum > 0, baseline, same_hour_prev_day)
    baseline = np.nan_to_num(baseline, nan=0.0)
    return baseline


def make_past_features(
    df: pd.DataFrame,
    cutoff: pd.Timestamp,
    target_col: str,
    seq_len: int,
) -> np.ndarray:
    idx = df.set_index("ds")
    hist = idx.loc[idx.index <= cutoff].tail(seq_len).copy()
    if len(hist) < seq_len:
        raise ValueError("历史窗口不足")
    load = hist["load"].replace(0, np.nan).to_numpy(float)
    wind = hist["wind"].to_numpy(float)
    solar = hist["solar"].to_numpy(float)
    bidding = hist["bidding_space"].to_numpy(float)
    target = hist[target_col].to_numpy(float)
    hours = np.array([business_hour(x) for x in hist.index], dtype=float)

    ramps = np.r_[0.0, np.diff(target)]
    net_load = np.nan_to_num(load - wind - solar)
    target_s = pd.Series(target)
    load_s = pd.Series(hist["load"].to_numpy(float))
    hour_business = np.array([business_hour(x) for x in hist.index], dtype=float)
    is_peak = ((hour_business >= 17) | (hour_business <= 8)).astype(float)
    features = np.vstack(
        [
            target,
            hist["load"].to_numpy(float),
            hist["wind"].to_numpy(float),
            hist["solar"].to_numpy(float),
            hist["interconnect"].to_numpy(float),
            hist["bidding_space"].to_numpy(float),
            hist["renewable"].to_numpy(float),
            net_load,
            np.nan_to_num(solar / load),
            np.nan_to_num(wind / load),
            np.nan_to_num((wind + solar) / load),
            np.nan_to_num(bidding / load),
            ramps,
            target_s.rolling(3, min_periods=1).mean().to_numpy(float),
            target_s.rolling(6, min_periods=1).mean().to_numpy(float),
            target_s.rolling(24, min_periods=1).mean().to_numpy(float),
            target_s.rolling(24, min_periods=1).std().fillna(0).to_numpy(float),
            load_s.rolling(24, min_periods=1).mean().to_numpy(float),
            load_s.rolling(24, min_periods=1).std().fillna(0).to_numpy(float),
            target_s.diff(24).fillna(0).to_numpy(float),
            target_s.diff(168).fillna(0).to_numpy(float),
            (target_s - target_s.rolling(168, min_periods=1).mean()).to_numpy(float),
            (target_s.rank(pct=True)).to_numpy(float),
            is_peak,
            np.sin(2 * np.pi * hours / 24),
            np.cos(2 * np.pi * hours / 24),
        ]
    ).T
    return features


def make_future_features(
    df: pd.DataFrame,
    target_day: pd.Timestamp,
    da_values: np.ndarray | None = None,
    baseline_values: np.ndarray | None = None,
) -> np.ndarray:
    cur = df[(df["ds"] >= target_day) & (df["ds"] < target_day + pd.Timedelta(days=1))].copy()
    if len(cur) != 24:
        raise ValueError(f"{target_day.date()} 不足 24 小时")
    load = cur["load"].replace(0, np.nan).to_numpy(float)
    wind = cur["wind"].to_numpy(float)
    solar = cur["solar"].to_numpy(float)
    bidding = cur["bidding_space"].to_numpy(float)
    hours = np.array([business_hour(x) for x in cur["ds"]], dtype=float)
    if da_values is None:
        da_values = np.zeros(24, dtype=float)
    if baseline_values is None:
        baseline_values = np.zeros(24, dtype=float)
    net_load = np.nan_to_num(load - wind - solar)
    ramp_load = np.r_[0.0, np.diff(cur["load"].to_numpy(float))]
    hour_business = np.array([business_hour(x) for x in cur["ds"]], dtype=float)
    is_peak = ((hour_business >= 17) | (hour_business <= 8)).astype(float)
    is_solar = ((hour_business >= 9) & (hour_business <= 16)).astype(float)
    future = np.vstack(
        [
            cur["load"].to_numpy(float),
            cur["wind"].to_numpy(float),
            cur["solar"].to_numpy(float),
            cur["interconnect"].to_numpy(float),
            cur["bidding_space"].to_numpy(float),
            cur["renewable"].to_numpy(float),
            net_load,
            np.nan_to_num(solar / load),
            np.nan_to_num(wind / load),
            np.nan_to_num((wind + solar) / load),
            np.nan_to_num(bidding / load),
            ramp_load,
            hours,
            hour_business,
            is_peak,
            is_solar,
            np.sin(2 * np.pi * hours / 24),
            np.cos(2 * np.pi * hours / 24),
            np.full(24, target_day.month, dtype=float),
            np.full(24, target_day.dayofweek, dtype=float),
            np.full(24, 1 if target_day.dayofweek >= 5 else 0, dtype=float),
            np.asarray(da_values, dtype=float),
            np.asarray(baseline_values, dtype=float),
        ]
    ).T
    return future


def make_sample(
    df: pd.DataFrame,
    target_day: pd.Timestamp,
    target_col: str,
    seq_len: int,
    cutoff_hour: int,
    da_values: np.ndarray | None = None,
    target_mode: str = "residual_blend",
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    cutoff = compute_cutoff(target_day, cutoff_hour)
    past = make_past_features(df, cutoff, target_col, seq_len)
    baseline = compute_blend_baseline(df, target_day, target_col)
    future = make_future_features(df, target_day, da_values=da_values, baseline_values=baseline)
    cur = df[(df["ds"] >= target_day) & (df["ds"] < target_day + pd.Timedelta(days=1))]
    y = cur[target_col].to_numpy(float)
    if len(y) != 24 or np.isnan(y).any():
        raise ValueError("目标日标签无效")
    if target_mode == "residual_blend":
        y_model = y - baseline
    else:
        y_model = y
    return past, future, y_model, baseline


def slice_segment(
    future: np.ndarray,
    y: np.ndarray,
    segment_start: int,
    segment_end: int,
) -> tuple[np.ndarray, np.ndarray]:
    return future[segment_start:segment_end], y[segment_start:segment_end]


def build_arrays(
    df: pd.DataFrame,
    days: list[pd.Timestamp],
    target_col: str,
    seq_len: int,
    cutoff_hour: int,
    pred_da_map: dict[pd.Timestamp, float] | None = None,
    target_mode: str = "residual_blend",
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    past_list = []
    future_list = []
    y_list = []
    baseline_list = []
    for day in days:
        try:
            da_vals = None
            if target_col == "realtime_price":
                cur = df[(df["ds"] >= day) & (df["ds"] < day + pd.Timedelta(days=1))].copy()
                if pred_da_map is None:
                    da_vals = cur["day_ahead_clearing_price"].to_numpy(float)
                else:
                    da_vals = np.array([pred_da_map.get(ts, np.nan) for ts in cur["ds"]], dtype=float)
                    da_vals = np.where(
                        np.isnan(da_vals),
                        cur["day_ahead_clearing_price"].to_numpy(float),
                        da_vals,
                    )
            past, future, y, baseline = make_sample(
                df,
                day,
                target_col=target_col,
                seq_len=seq_len,
                cutoff_hour=cutoff_hour,
                da_values=da_vals,
                target_mode=target_mode,
            )
            past_list.append(past)
            future_list.append(future)
            y_list.append(y)
            baseline_list.append(baseline)
        except Exception:
            continue
    if not past_list:
        raise ValueError("没有可用样本")
    return (
        np.stack(past_list),
        np.stack(future_list),
        np.stack(y_list),
        np.stack(baseline_list),
    )


def build_segment_arrays(
    df: pd.DataFrame,
    days: list[pd.Timestamp],
    target_col: str,
    seq_len: int,
    cutoff_hour: int,
    segment_start: int,
    segment_end: int,
    pred_da_map: dict[pd.Timestamp, float] | None = None,
    target_mode: str = "residual_blend",
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    past_list = []
    future_list = []
    y_list = []
    baseline_list = []
    for day in days:
        try:
            da_vals = None
            if target_col == "realtime_price":
                cur = df[(df["ds"] >= day) & (df["ds"] < day + pd.Timedelta(days=1))].copy()
                if pred_da_map is None:
                    da_vals = cur["day_ahead_clearing_price"].to_numpy(float)
                else:
                    da_vals = np.array([pred_da_map.get(ts, np.nan) for ts in cur["ds"]], dtype=float)
                    da_vals = np.where(
                        np.isnan(da_vals),
                        cur["day_ahead_clearing_price"].to_numpy(float),
                        da_vals,
                    )
            past, future, y, baseline = make_sample(
                df,
                day,
                target_col=target_col,
                seq_len=seq_len,
                cutoff_hour=cutoff_hour,
                da_values=da_vals,
                target_mode=target_mode,
            )
            future_seg, y_seg = slice_segment(future, y, segment_start, segment_end)
            baseline_seg = baseline[segment_start:segment_end]
            past_list.append(past)
            future_list.append(future_seg)
            y_list.append(y_seg)
            baseline_list.append(baseline_seg)
        except Exception:
            continue
    if not past_list:
        raise ValueError("没有可用样本")
    return (
        np.stack(past_list),
        np.stack(future_list),
        np.stack(y_list),
        np.stack(baseline_list),
    )


def filter_available_days(
    df: pd.DataFrame,
    days: list[pd.Timestamp],
    seq_len: int,
    cutoff_hour_da: int,
    cutoff_hour_rt: int,
    da_target_mode: str,
    rt_target_mode: str,
) -> list[pd.Timestamp]:
    available_days: list[pd.Timestamp] = []
    for day in days:
        try:
            make_sample(
                df,
                day,
                target_col="day_ahead_clearing_price",
                seq_len=seq_len,
                cutoff_hour=cutoff_hour_da,
                target_mode=da_target_mode,
            )
            make_sample(
                df,
                day,
                target_col="realtime_price",
                seq_len=seq_len,
                cutoff_hour=cutoff_hour_rt,
                target_mode=rt_target_mode,
            )
            available_days.append(day)
        except Exception:
            continue
    return available_days


def split_train_valid(days: list[pd.Timestamp], val_ratio: float) -> tuple[list[pd.Timestamp], list[pd.Timestamp]]:
    split = max(1, int(len(days) * (1 - val_ratio)))
    train_days = days[:split]
    valid_days = days[split:] if split < len(days) else days[-1:]
    return train_days, valid_days


def fit_segment_bias_calibrator(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    calibration_mode: str,
    shrink: float = 1.0,
) -> np.ndarray:
    if calibration_mode == "none":
        return np.zeros(y_true.shape[1], dtype=float)
    residual = y_true - y_pred
    if calibration_mode == "segment_bias":
        return np.median(residual, axis=0)
    if calibration_mode == "segment_bias_shrink":
        return np.median(residual, axis=0) * shrink
    if calibration_mode == "hour_bias":
        return np.median(residual, axis=0)
    raise ValueError(f"Unsupported calibration_mode: {calibration_mode}")


def fit_affine_calibrator(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    clip_min: float,
    clip_max: float,
) -> tuple[float, float]:
    x = y_pred.reshape(-1).astype(float)
    y = y_true.reshape(-1).astype(float)
    if np.std(x) < 1e-8:
        return 1.0, float(np.median(y - x))
    a, b = np.polyfit(x, y, deg=1)
    a = float(np.clip(a, clip_min, clip_max))
    b = float(b)
    return a, b


def apply_affine_calibrator(y_pred: np.ndarray, affine: tuple[float, float]) -> np.ndarray:
    a, b = affine
    return y_pred * a + b


def compute_rt_916_regime_mask(
    future_arr: np.ndarray,
    solar_ratio_threshold: float,
    bidding_ratio_threshold: float,
    bidding_space_threshold: float,
) -> np.ndarray:
    solar_ratio = future_arr[:, :, 7]
    bidding_space = future_arr[:, :, 4]
    bidding_ratio = future_arr[:, :, 10]
    return (
        (solar_ratio >= solar_ratio_threshold)
        | (bidding_ratio <= bidding_ratio_threshold)
        | (bidding_space <= bidding_space_threshold)
    )


def compute_rt_916_peak_mask(
    future_arr: np.ndarray,
    stress_mask: np.ndarray,
    da_threshold: float,
    bidding_space_threshold: float,
    solar_ratio_max: float,
) -> np.ndarray:
    da_values = future_arr[:, :, 21]
    bidding_space = future_arr[:, :, 4]
    solar_ratio = future_arr[:, :, 7]
    return (
        (~stress_mask)
        & (da_values >= da_threshold)
        & (bidding_space >= bidding_space_threshold)
        & (solar_ratio <= solar_ratio_max)
    )


def compute_rt_peak_weight_matrix(
    future_arr: np.ndarray,
    cfg: RunConfig,
) -> np.ndarray:
    stress_mask = compute_rt_916_regime_mask(
        future_arr,
        cfg.regime_solar_ratio_threshold,
        cfg.regime_bidding_ratio_threshold,
        cfg.regime_bidding_space_threshold,
    )
    peak_mask = compute_rt_916_peak_mask(
        future_arr,
        stress_mask,
        cfg.peak_da_threshold,
        cfg.peak_bidding_space_threshold,
        cfg.peak_solar_ratio_max,
    )
    weights = np.ones_like(future_arr[:, :, 0], dtype=float)
    weights[peak_mask] = cfg.rt_peak_weight_multiplier
    return weights


def compute_rt_normal_focus_weight_matrix(
    future_arr: np.ndarray,
    cfg: RunConfig,
) -> np.ndarray:
    stress_mask = compute_rt_916_regime_mask(
        future_arr,
        cfg.regime_solar_ratio_threshold,
        cfg.regime_bidding_ratio_threshold,
        cfg.regime_bidding_space_threshold,
    )
    peak_mask = compute_rt_916_peak_mask(
        future_arr,
        stress_mask,
        cfg.peak_da_threshold,
        cfg.peak_bidding_space_threshold,
        cfg.peak_solar_ratio_max,
    )
    normal_mask = (~stress_mask) & (~peak_mask)
    hour_business = future_arr[:, :, 13]
    focus_hours = np.isin(hour_business, [9, 10, 11, 12, 13, 16])
    weights = np.ones_like(future_arr[:, :, 0], dtype=float)
    weights[normal_mask & focus_hours] = cfg.rt_normal_focus_multiplier
    return weights


def fit_regime_affine_calibrator(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    future_arr: np.ndarray,
    cfg: RunConfig,
) -> dict[str, Any]:
    stress_mask = compute_rt_916_regime_mask(
        future_arr,
        cfg.regime_solar_ratio_threshold,
        cfg.regime_bidding_ratio_threshold,
        cfg.regime_bidding_space_threshold,
    )
    normal_mask = ~stress_mask
    stress_count = int(stress_mask.sum())
    normal_count = int(normal_mask.sum())
    calibrator = {
        "stress_affine": (1.0, 0.0),
        "normal_affine": (1.0, 0.0),
        "stress_count": stress_count,
        "normal_count": normal_count,
        "thresholds": {
            "solar_ratio": cfg.regime_solar_ratio_threshold,
            "bidding_ratio": cfg.regime_bidding_ratio_threshold,
            "bidding_space": cfg.regime_bidding_space_threshold,
        },
    }
    if stress_count >= 8:
        calibrator["stress_affine"] = fit_affine_calibrator(
            y_true[stress_mask],
            y_pred[stress_mask],
            cfg.affine_clip_min,
            cfg.affine_clip_max,
        )
    if normal_count >= 8:
        calibrator["normal_affine"] = fit_affine_calibrator(
            y_true[normal_mask],
            y_pred[normal_mask],
            cfg.affine_clip_min,
            cfg.affine_clip_max,
        )
    return calibrator


def compute_rt_916_day_feature_table(future_arr: np.ndarray) -> dict[str, np.ndarray]:
    load = future_arr[:, :, 0]
    solar = future_arr[:, :, 2]
    bidding = future_arr[:, :, 4]
    renewable_ratio = future_arr[:, :, 9]
    da_values = future_arr[:, :, 21]
    return {
        "solar_mean": solar.mean(axis=1),
        "solar_drop": solar.max(axis=1) - solar.min(axis=1),
        "bidding_mean": bidding.mean(axis=1),
        "bidding_min": bidding.min(axis=1),
        "bidding_drop": bidding.max(axis=1) - bidding.min(axis=1),
        "load_mean": load.mean(axis=1),
        "load_ramp": load.max(axis=1) - load.min(axis=1),
        "da_mean": da_values.mean(axis=1),
        "da_max": da_values.max(axis=1),
        "renewable_ratio_mean": renewable_ratio.mean(axis=1),
    }


def expand_day_mask(day_mask: np.ndarray, pred_len: int) -> np.ndarray:
    return np.repeat(np.asarray(day_mask, dtype=bool).reshape(-1, 1), pred_len, axis=1)


def build_spike_day_mask(
    feature_table: dict[str, np.ndarray],
    thresholds: dict[str, float],
) -> np.ndarray:
    return (
        (feature_table["solar_mean"] >= thresholds["solar_mean_min"])
        & (feature_table["bidding_min"] <= thresholds["bidding_min_max"])
        & (feature_table["da_mean"] <= thresholds["da_mean_max"])
    )


def fit_spike_day_affine_calibrator(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    future_arr: np.ndarray,
    cfg: RunConfig,
) -> dict[str, Any]:
    base_calibrator = fit_regime_affine_calibrator(y_true, y_pred, future_arr, cfg)
    base_adjusted = apply_regime_affine_calibrator(y_pred, future_arr, base_calibrator)
    feature_table = compute_rt_916_day_feature_table(future_arr)
    n_days = y_true.shape[0]
    calibrator: dict[str, Any] = {
        "base_calibrator": base_calibrator,
        "selected_rule": None,
        "selected_day_count": 0,
        "selected_hour_count": 0,
        "spike_affine": (1.0, 0.0),
        "scores": [],
    }
    if n_days < 4:
        return calibrator

    solar_candidates = sorted(set(np.quantile(feature_table["solar_mean"], [0.60, 0.70, 0.80]).tolist()))
    bidding_candidates = sorted(set(np.quantile(feature_table["bidding_min"], [0.20, 0.30, 0.40]).tolist()))
    da_candidates = sorted(set(np.quantile(feature_table["da_mean"], [0.20, 0.30, 0.40]).tolist()))

    best_payload: dict[str, Any] | None = None
    for solar_thr in solar_candidates:
        for bidding_thr in bidding_candidates:
            for da_thr in da_candidates:
                thresholds = {
                    "solar_mean_min": float(solar_thr),
                    "bidding_min_max": float(bidding_thr),
                    "da_mean_max": float(da_thr),
                }
                day_mask = build_spike_day_mask(feature_table, thresholds)
                day_count = int(day_mask.sum())
                if day_count < 2 or day_count >= n_days:
                    continue
                hour_mask = expand_day_mask(day_mask, y_true.shape[1])
                spike_affine = fit_affine_calibrator(
                    y_true[hour_mask],
                    base_adjusted[hour_mask],
                    cfg.affine_clip_min,
                    cfg.affine_clip_max,
                )
                adjusted = base_adjusted.copy()
                adjusted[hour_mask] = apply_affine_calibrator(adjusted[hour_mask], spike_affine)
                score = smape(adjusted.reshape(-1), y_true.reshape(-1))
                mae = float(np.mean(np.abs(adjusted.reshape(-1) - y_true.reshape(-1))))
                payload = {
                    "thresholds": thresholds,
                    "day_count": day_count,
                    "hour_count": int(hour_mask.sum()),
                    "spike_affine": spike_affine,
                    "smape": score,
                    "mae": mae,
                }
                calibrator["scores"].append(payload)
                if best_payload is None or (score, mae, day_count) < (
                    best_payload["smape"],
                    best_payload["mae"],
                    best_payload["day_count"],
                ):
                    best_payload = payload

    if best_payload is not None:
        calibrator["selected_rule"] = {
            "type": "solar_mean_high_and_bidding_min_low_and_da_mean_low",
            "thresholds": best_payload["thresholds"],
        }
        calibrator["selected_day_count"] = best_payload["day_count"]
        calibrator["selected_hour_count"] = best_payload["hour_count"]
        calibrator["spike_affine"] = best_payload["spike_affine"]
    return calibrator


def fit_regime_affine_hourbias_calibrator(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    future_arr: np.ndarray,
    cfg: RunConfig,
) -> dict[str, Any]:
    stress_mask = compute_rt_916_regime_mask(
        future_arr,
        cfg.regime_solar_ratio_threshold,
        cfg.regime_bidding_ratio_threshold,
        cfg.regime_bidding_space_threshold,
    )
    normal_mask = ~stress_mask
    normal_bias = np.zeros(y_true.shape[1], dtype=float)
    normal_counts = np.zeros(y_true.shape[1], dtype=int)
    calibrator = {
        "stress_affine": (1.0, 0.0),
        "normal_bias": normal_bias.tolist(),
        "stress_count": int(stress_mask.sum()),
        "normal_count": int(normal_mask.sum()),
        "normal_hour_counts": normal_counts.tolist(),
        "thresholds": {
            "solar_ratio": cfg.regime_solar_ratio_threshold,
            "bidding_ratio": cfg.regime_bidding_ratio_threshold,
            "bidding_space": cfg.regime_bidding_space_threshold,
        },
    }
    if calibrator["stress_count"] >= 8:
        calibrator["stress_affine"] = fit_affine_calibrator(
            y_true[stress_mask],
            y_pred[stress_mask],
            cfg.affine_clip_min,
            cfg.affine_clip_max,
        )
    if calibrator["normal_count"] > 0:
        residual = y_true - y_pred
        for hour_idx in range(y_true.shape[1]):
            hour_mask = normal_mask[:, hour_idx]
            count = int(hour_mask.sum())
            normal_counts[hour_idx] = count
            if count >= 4:
                normal_bias[hour_idx] = float(np.median(residual[:, hour_idx][hour_mask]))
        calibrator["normal_bias"] = normal_bias.tolist()
        calibrator["normal_hour_counts"] = normal_counts.tolist()
    return calibrator


def fit_peak_regime_affine_calibrator(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    future_arr: np.ndarray,
    cfg: RunConfig,
) -> dict[str, Any]:
    stress_mask = compute_rt_916_regime_mask(
        future_arr,
        cfg.regime_solar_ratio_threshold,
        cfg.regime_bidding_ratio_threshold,
        cfg.regime_bidding_space_threshold,
    )
    peak_mask = compute_rt_916_peak_mask(
        future_arr,
        stress_mask,
        cfg.peak_da_threshold,
        cfg.peak_bidding_space_threshold,
        cfg.peak_solar_ratio_max,
    )
    normal_mask = (~stress_mask) & (~peak_mask)
    calibrator = {
        "stress_affine": (1.0, 0.0),
        "peak_affine": (1.0, 0.0),
        "normal_affine": (1.0, 0.0),
        "stress_count": int(stress_mask.sum()),
        "peak_count": int(peak_mask.sum()),
        "normal_count": int(normal_mask.sum()),
        "thresholds": {
            "solar_ratio": cfg.regime_solar_ratio_threshold,
            "bidding_ratio": cfg.regime_bidding_ratio_threshold,
            "bidding_space": cfg.regime_bidding_space_threshold,
            "peak_da": cfg.peak_da_threshold,
            "peak_bidding_space": cfg.peak_bidding_space_threshold,
            "peak_solar_ratio_max": cfg.peak_solar_ratio_max,
        },
    }
    if calibrator["stress_count"] >= 8:
        calibrator["stress_affine"] = fit_affine_calibrator(
            y_true[stress_mask],
            y_pred[stress_mask],
            cfg.affine_clip_min,
            cfg.affine_clip_max,
        )
    if calibrator["peak_count"] >= 8:
        calibrator["peak_affine"] = fit_affine_calibrator(
            y_true[peak_mask],
            y_pred[peak_mask],
            cfg.affine_clip_min,
            cfg.affine_clip_max,
        )
    if calibrator["normal_count"] >= 8:
        calibrator["normal_affine"] = fit_affine_calibrator(
            y_true[normal_mask],
            y_pred[normal_mask],
            cfg.affine_clip_min,
            cfg.affine_clip_max,
        )
    return calibrator


def fit_peak_regime_bias_calibrator(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    future_arr: np.ndarray,
    cfg: RunConfig,
) -> dict[str, Any]:
    stress_mask = compute_rt_916_regime_mask(
        future_arr,
        cfg.regime_solar_ratio_threshold,
        cfg.regime_bidding_ratio_threshold,
        cfg.regime_bidding_space_threshold,
    )
    peak_mask = compute_rt_916_peak_mask(
        future_arr,
        stress_mask,
        cfg.peak_da_threshold,
        cfg.peak_bidding_space_threshold,
        cfg.peak_solar_ratio_max,
    )
    normal_mask = (~stress_mask) & (~peak_mask)
    calibrator = {
        "stress_affine": (1.0, 0.0),
        "peak_bias": np.zeros(y_true.shape[1], dtype=float),
        "normal_affine": (1.0, 0.0),
        "stress_count": int(stress_mask.sum()),
        "peak_count": int(peak_mask.sum()),
        "normal_count": int(normal_mask.sum()),
        "thresholds": {
            "solar_ratio": cfg.regime_solar_ratio_threshold,
            "bidding_ratio": cfg.regime_bidding_ratio_threshold,
            "bidding_space": cfg.regime_bidding_space_threshold,
            "peak_da": cfg.peak_da_threshold,
            "peak_bidding_space": cfg.peak_bidding_space_threshold,
            "peak_solar_ratio_max": cfg.peak_solar_ratio_max,
        },
    }
    if calibrator["stress_count"] >= 8:
        calibrator["stress_affine"] = fit_affine_calibrator(
            y_true[stress_mask],
            y_pred[stress_mask],
            cfg.affine_clip_min,
            cfg.affine_clip_max,
        )
    if calibrator["peak_count"] >= 8:
        peak_residual = y_true[peak_mask] - y_pred[peak_mask]
        calibrator["peak_bias"] = np.median(peak_residual, axis=0) * cfg.calibration_shrink
    if calibrator["normal_count"] >= 8:
        calibrator["normal_affine"] = fit_affine_calibrator(
            y_true[normal_mask],
            y_pred[normal_mask],
            cfg.affine_clip_min,
            cfg.affine_clip_max,
        )
    return calibrator


def apply_regime_affine_calibrator(
    y_pred: np.ndarray,
    future_arr: np.ndarray,
    calibrator: dict[str, Any],
) -> np.ndarray:
    stress_mask = compute_rt_916_regime_mask(
        future_arr,
        calibrator["thresholds"]["solar_ratio"],
        calibrator["thresholds"]["bidding_ratio"],
        calibrator["thresholds"]["bidding_space"],
    )
    adjusted = y_pred.copy()
    if stress_mask.any():
        adjusted[stress_mask] = apply_affine_calibrator(
            adjusted[stress_mask],
            tuple(calibrator["stress_affine"]),
        )
    normal_mask = ~stress_mask
    if normal_mask.any():
        adjusted[normal_mask] = apply_affine_calibrator(
            adjusted[normal_mask],
            tuple(calibrator["normal_affine"]),
        )
    return adjusted


def apply_spike_day_affine_calibrator(
    y_pred: np.ndarray,
    future_arr: np.ndarray,
    calibrator: dict[str, Any],
) -> np.ndarray:
    adjusted = apply_regime_affine_calibrator(
        y_pred,
        future_arr,
        calibrator["base_calibrator"],
    )
    selected_rule = calibrator.get("selected_rule")
    if not selected_rule:
        return adjusted
    feature_table = compute_rt_916_day_feature_table(future_arr)
    day_mask = build_spike_day_mask(feature_table, selected_rule["thresholds"])
    if not day_mask.any():
        return adjusted
    hour_mask = expand_day_mask(day_mask, adjusted.shape[1])
    adjusted[hour_mask] = apply_affine_calibrator(
        adjusted[hour_mask],
        tuple(calibrator["spike_affine"]),
    )
    return adjusted


def apply_regime_affine_hourbias_calibrator(
    y_pred: np.ndarray,
    future_arr: np.ndarray,
    calibrator: dict[str, Any],
) -> np.ndarray:
    stress_mask = compute_rt_916_regime_mask(
        future_arr,
        calibrator["thresholds"]["solar_ratio"],
        calibrator["thresholds"]["bidding_ratio"],
        calibrator["thresholds"]["bidding_space"],
    )
    adjusted = y_pred.copy()
    if stress_mask.any():
        adjusted[stress_mask] = apply_affine_calibrator(
            adjusted[stress_mask],
            tuple(calibrator["stress_affine"]),
        )
    normal_mask = ~stress_mask
    if normal_mask.any():
        normal_bias = np.asarray(calibrator["normal_bias"], dtype=float).reshape(1, -1)
        adjusted = adjusted + normal_mask.astype(float) * normal_bias
    return adjusted


def apply_peak_regime_affine_calibrator(
    y_pred: np.ndarray,
    future_arr: np.ndarray,
    calibrator: dict[str, Any],
) -> np.ndarray:
    stress_mask = compute_rt_916_regime_mask(
        future_arr,
        calibrator["thresholds"]["solar_ratio"],
        calibrator["thresholds"]["bidding_ratio"],
        calibrator["thresholds"]["bidding_space"],
    )
    peak_mask = compute_rt_916_peak_mask(
        future_arr,
        stress_mask,
        calibrator["thresholds"]["peak_da"],
        calibrator["thresholds"]["peak_bidding_space"],
        calibrator["thresholds"]["peak_solar_ratio_max"],
    )
    normal_mask = (~stress_mask) & (~peak_mask)
    adjusted = y_pred.copy()
    if stress_mask.any():
        adjusted[stress_mask] = apply_affine_calibrator(
            adjusted[stress_mask],
            tuple(calibrator["stress_affine"]),
        )
    if peak_mask.any():
        adjusted[peak_mask] = apply_affine_calibrator(
            adjusted[peak_mask],
            tuple(calibrator["peak_affine"]),
        )
    if normal_mask.any():
        adjusted[normal_mask] = apply_affine_calibrator(
            adjusted[normal_mask],
            tuple(calibrator["normal_affine"]),
        )
    return adjusted


def apply_peak_regime_bias_calibrator(
    y_pred: np.ndarray,
    future_arr: np.ndarray,
    calibrator: dict[str, Any],
) -> np.ndarray:
    stress_mask = compute_rt_916_regime_mask(
        future_arr,
        calibrator["thresholds"]["solar_ratio"],
        calibrator["thresholds"]["bidding_ratio"],
        calibrator["thresholds"]["bidding_space"],
    )
    peak_mask = compute_rt_916_peak_mask(
        future_arr,
        stress_mask,
        calibrator["thresholds"]["peak_da"],
        calibrator["thresholds"]["peak_bidding_space"],
        calibrator["thresholds"]["peak_solar_ratio_max"],
    )
    normal_mask = (~stress_mask) & (~peak_mask)
    adjusted = y_pred.copy()
    if stress_mask.any():
        adjusted[stress_mask] = apply_affine_calibrator(
            adjusted[stress_mask],
            tuple(calibrator["stress_affine"]),
        )
    if peak_mask.any():
        adjusted = adjusted + peak_mask.astype(float) * np.asarray(calibrator["peak_bias"], dtype=float).reshape(1, -1)
    if normal_mask.any():
        adjusted[normal_mask] = apply_affine_calibrator(
            adjusted[normal_mask],
            tuple(calibrator["normal_affine"]),
        )
    return adjusted


def apply_rt_916_calibration_mode(
    y_pred: np.ndarray,
    future_arr: np.ndarray,
    calibration_mode: str,
    affine_obj: Any,
    bias: np.ndarray,
    ) -> np.ndarray:
    if calibration_mode == "rt_916_affine":
        return apply_affine_calibrator(y_pred, affine_obj)
    if calibration_mode == "rt_916_regime_affine":
        return apply_regime_affine_calibrator(y_pred, future_arr, affine_obj)
    if calibration_mode == "rt_916_spike_day_affine":
        return apply_spike_day_affine_calibrator(y_pred, future_arr, affine_obj)
    if calibration_mode == "rt_916_regime_affine_hourbias":
        return apply_regime_affine_hourbias_calibrator(y_pred, future_arr, affine_obj)
    if calibration_mode == "rt_916_peak_regime_affine":
        return apply_peak_regime_affine_calibrator(y_pred, future_arr, affine_obj)
    if calibration_mode == "rt_916_peak_regime_bias":
        return apply_peak_regime_bias_calibrator(y_pred, future_arr, affine_obj)
    return apply_bias_calibrator(y_pred, bias)


def fit_rt_916_auto_calibrator(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    future_arr: np.ndarray,
    cfg: RunConfig,
) -> dict[str, Any]:
    candidates: dict[str, dict[str, Any]] = {
        "none": {
            "mode": "none",
            "affine": None,
            "bias": np.zeros(y_true.shape[1], dtype=float),
        },
        "rt_916_affine": {
            "mode": "rt_916_affine",
            "affine": fit_affine_calibrator(
                y_true,
                y_pred,
                cfg.affine_clip_min,
                cfg.affine_clip_max,
            ),
            "bias": np.zeros(y_true.shape[1], dtype=float),
        },
        "rt_916_regime_affine": {
            "mode": "rt_916_regime_affine",
            "affine": fit_regime_affine_calibrator(y_true, y_pred, future_arr, cfg),
            "bias": np.zeros(y_true.shape[1], dtype=float),
        },
        "rt_916_spike_day_affine": {
            "mode": "rt_916_spike_day_affine",
            "affine": fit_spike_day_affine_calibrator(y_true, y_pred, future_arr, cfg),
            "bias": np.zeros(y_true.shape[1], dtype=float),
        },
        "rt_916_regime_affine_hourbias": {
            "mode": "rt_916_regime_affine_hourbias",
            "affine": fit_regime_affine_hourbias_calibrator(y_true, y_pred, future_arr, cfg),
            "bias": np.zeros(y_true.shape[1], dtype=float),
        },
        "rt_916_peak_regime_affine": {
            "mode": "rt_916_peak_regime_affine",
            "affine": fit_peak_regime_affine_calibrator(y_true, y_pred, future_arr, cfg),
            "bias": np.zeros(y_true.shape[1], dtype=float),
        },
        "rt_916_peak_regime_bias": {
            "mode": "rt_916_peak_regime_bias",
            "affine": fit_peak_regime_bias_calibrator(y_true, y_pred, future_arr, cfg),
            "bias": np.zeros(y_true.shape[1], dtype=float),
        },
    }
    scored: list[tuple[float, float, str]] = []
    for name, item in candidates.items():
        adjusted = apply_rt_916_calibration_mode(
            y_pred,
            future_arr,
            item["mode"],
            item["affine"],
            item["bias"],
        )
        score = smape(adjusted.reshape(-1), y_true.reshape(-1))
        mae = float(np.mean(np.abs(adjusted.reshape(-1) - y_true.reshape(-1))))
        scored.append((score, mae, name))
    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    winner = scored[0][2]
    chosen = candidates[winner]
    return {
        "selected_mode": chosen["mode"],
        "affine": chosen["affine"],
        "bias": chosen["bias"],
        "scores": [
            {
                "mode": name,
                "smape": score,
                "mae": mae,
            }
            for score, mae, name in scored
        ],
    }


def serialize_rt_affine_payload(payload: Any) -> Any:
    if isinstance(payload, np.ndarray):
        return payload.tolist()
    if isinstance(payload, tuple):
        return [serialize_rt_affine_payload(x) for x in payload]
    if isinstance(payload, list):
        return [serialize_rt_affine_payload(x) for x in payload]
    if isinstance(payload, dict):
        return {str(k): serialize_rt_affine_payload(v) for k, v in payload.items()}
    if isinstance(payload, (np.floating, np.integer)):
        return payload.item()
    return payload


def apply_bias_calibrator(y_pred: np.ndarray, bias: np.ndarray) -> np.ndarray:
    return y_pred + bias.reshape(1, -1)


def train_model(
    past: np.ndarray,
    future: np.ndarray,
    y: np.ndarray,
    cfg: RunConfig,
    device: torch.device,
    task: str,
    segment_name: str | None = None,
) -> dict[str, Any]:
    n = len(y)
    split = max(1, int(n * (1 - cfg.val_ratio)))
    train_idx = np.arange(0, split)
    valid_idx = np.arange(split, n) if split < n else np.arange(max(0, n - 1), n)

    past_scaler = StandardScaler().fit(past[train_idx].reshape(-1, past.shape[-1]))
    future_scaler = StandardScaler().fit(future[train_idx].reshape(-1, future.shape[-1]))
    y_scaler = StandardScaler().fit(y[train_idx])

    def transform_past(a: np.ndarray) -> np.ndarray:
        return past_scaler.transform(a.reshape(-1, a.shape[-1])).reshape(a.shape)

    def transform_future(a: np.ndarray) -> np.ndarray:
        return future_scaler.transform(a.reshape(-1, a.shape[-1])).reshape(a.shape)

    def transform_y(a: np.ndarray) -> np.ndarray:
        return y_scaler.transform(a)

    train_ds = ElectricityDailyDataset(
        transform_past(past[train_idx]),
        transform_future(future[train_idx]),
        transform_y(y[train_idx]),
    )
    valid_ds = ElectricityDailyDataset(
        transform_past(past[valid_idx]),
        transform_future(future[valid_idx]),
        transform_y(y[valid_idx]),
    )
    train_shuffle = True
    if task == "rt" and cfg.rt_loss_mode == "risk_peak_weighted":
        train_shuffle = False
    train_loader = DataLoader(train_ds, batch_size=cfg.batch_size, shuffle=train_shuffle)
    valid_loader = DataLoader(valid_ds, batch_size=cfg.batch_size, shuffle=False)

    segment_head_mode = "none"
    backbone_name = cfg.backbone
    if task == "rt" and segment_name == "9_16":
        segment_head_mode = cfg.rt_segment_head_mode
        if cfg.rt_916_backbone:
            backbone_name = cfg.rt_916_backbone
    model = build_backbone(
        backbone_name,
        past_dim=past.shape[-1],
        future_dim=future.shape[-1],
        pred_len=y.shape[1],
        hidden_dim=cfg.hidden_dim,
        blocks=cfg.blocks,
        scales=cfg.scales,
        dropout=cfg.dropout,
        segment_head_mode=segment_head_mode,
    ).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)
    best_state = None
    best_valid = float("inf")
    patience_left = cfg.patience
    history = []
    risk_weight = torch.tensor([1.0] * y.shape[1], dtype=torch.float32, device=device)
    peak_weight_train = None
    peak_weight_valid = None
    normal_focus_weight_train = None
    normal_focus_weight_valid = None
    da_hour_weight = None
    da_under_multiplier = 1.0
    if task == "rt" and cfg.rt_loss_mode == "risk_hour_weighted" and y.shape[1] == 8:
        profile_map = {
            "baseline": [1.20, 1.35, 1.15, 1.00, 1.15, 1.35, 1.40, 1.20],
            "solar_focus": [1.30, 1.45, 1.25, 1.10, 1.05, 1.20, 1.20, 1.05],
            "peak_focus": [1.10, 1.20, 1.10, 1.00, 1.20, 1.45, 1.55, 1.30],
        }
        risk_weight = torch.tensor(
            profile_map.get(cfg.rt_risk_profile, profile_map["baseline"]),
            dtype=torch.float32,
            device=device,
        )
    if (
        task == "rt"
        and cfg.rt_loss_mode == "risk_peak_weighted"
        and y.shape[1] == 8
    ):
        profile_map = {
            "baseline": [1.20, 1.35, 1.15, 1.00, 1.15, 1.35, 1.40, 1.20],
            "solar_focus": [1.30, 1.45, 1.25, 1.10, 1.05, 1.20, 1.20, 1.05],
            "peak_focus": [1.10, 1.20, 1.10, 1.00, 1.20, 1.45, 1.55, 1.30],
        }
        risk_weight = torch.tensor(
            profile_map.get(cfg.rt_risk_profile, profile_map["baseline"]),
            dtype=torch.float32,
            device=device,
        )
        peak_weight_train = torch.tensor(
            compute_rt_peak_weight_matrix(future[train_idx], cfg),
            dtype=torch.float32,
            device=device,
        )
        peak_weight_valid = torch.tensor(
            compute_rt_peak_weight_matrix(future[valid_idx], cfg),
            dtype=torch.float32,
            device=device,
        )
        normal_focus_weight_train = torch.tensor(
            compute_rt_normal_focus_weight_matrix(future[train_idx], cfg),
            dtype=torch.float32,
            device=device,
        )
        normal_focus_weight_valid = torch.tensor(
            compute_rt_normal_focus_weight_matrix(future[valid_idx], cfg),
            dtype=torch.float32,
            device=device,
        )
    if task == "da" and cfg.da_loss_mode == "asymmetric_under" and y.shape[1] == 8:
        future_hour_business = future[:, :, 13]
        segment_hour_min = int(np.round(np.nanmin(future_hour_business)))
        segment_hour_max = int(np.round(np.nanmax(future_hour_business)))
        if segment_hour_min >= 9 and segment_hour_max <= 16:
            da_hour_weight = torch.tensor(
                [1.20, 1.15, 1.10, 1.05, 1.10, 1.15, 1.25, 1.10],
                dtype=torch.float32,
                device=device,
            )
        da_under_multiplier = cfg.da_under_weight_multiplier

    def loss_fn(
        pred: torch.Tensor,
        target: torch.Tensor,
        batch_peak_weight: torch.Tensor | None = None,
        batch_normal_focus_weight: torch.Tensor | None = None,
    ) -> torch.Tensor:
        if task == "rt" and cfg.rt_loss_mode == "risk_hour_weighted":
            per_step = torch.abs(pred - target)
            return (per_step * risk_weight).mean()
        if task == "rt" and cfg.rt_loss_mode == "risk_peak_weighted":
            per_step = torch.abs(pred - target) * risk_weight.reshape(1, -1)
            if batch_peak_weight is not None:
                per_step = per_step * batch_peak_weight
            if batch_normal_focus_weight is not None:
                per_step = per_step * batch_normal_focus_weight
            return per_step.mean()
        if task == "da" and cfg.da_loss_mode == "asymmetric_under":
            per_step = torch.abs(pred - target)
            under_mask = (pred < target).float()
            asym_weight = 1.0 + under_mask * (da_under_multiplier - 1.0)
            if da_hour_weight is not None:
                asym_weight = asym_weight * da_hour_weight.reshape(1, -1)
            return (per_step * asym_weight).mean()
        return torch.abs(pred - target).mean()

    for epoch in range(1, cfg.epochs + 1):
        model.train()
        train_loss = 0.0
        for batch_i, (xb, fb, yb) in enumerate(train_loader):
            xb, fb, yb = xb.to(device), fb.to(device), yb.to(device)
            optimizer.zero_grad()
            pred = model(xb, fb)
            batch_peak = None
            batch_normal_focus = None
            if peak_weight_train is not None:
                start = batch_i * cfg.batch_size
                end = start + len(yb)
                batch_peak = peak_weight_train[start:end]
            if normal_focus_weight_train is not None:
                start = batch_i * cfg.batch_size
                end = start + len(yb)
                batch_normal_focus = normal_focus_weight_train[start:end]
            loss = loss_fn(pred, yb, batch_peak, batch_normal_focus)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            train_loss += loss.item() * len(yb)
        train_loss /= len(train_ds)

        model.eval()
        valid_loss = 0.0
        with torch.no_grad():
            for batch_i, (xb, fb, yb) in enumerate(valid_loader):
                xb, fb, yb = xb.to(device), fb.to(device), yb.to(device)
                pred = model(xb, fb)
                batch_peak = None
                batch_normal_focus = None
                if peak_weight_valid is not None:
                    start = batch_i * cfg.batch_size
                    end = start + len(yb)
                    batch_peak = peak_weight_valid[start:end]
                if normal_focus_weight_valid is not None:
                    start = batch_i * cfg.batch_size
                    end = start + len(yb)
                    batch_normal_focus = normal_focus_weight_valid[start:end]
                valid_loss += loss_fn(pred, yb, batch_peak, batch_normal_focus).item() * len(yb)
        valid_loss /= len(valid_ds)
        history.append(
            {
                "epoch": epoch,
                "train_mae_scaled": train_loss,
                "valid_mae_scaled": valid_loss,
            }
        )
        if valid_loss < best_valid - 1e-5:
            best_valid = valid_loss
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            patience_left = cfg.patience
        else:
            patience_left -= 1
            if patience_left <= 0:
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    return {
        "model": model,
        "past_scaler": past_scaler,
        "future_scaler": future_scaler,
        "y_scaler": y_scaler,
        "history": history,
        "best_valid_mae_scaled": best_valid,
        "stopped_early": len(history) < cfg.epochs,
    }


def predict_model(
    bundle: dict[str, Any],
    past: np.ndarray,
    future: np.ndarray,
    device: torch.device,
    batch_size: int,
) -> np.ndarray:
    ps = bundle["past_scaler"]
    fs = bundle["future_scaler"]
    ys = bundle["y_scaler"]
    model = bundle["model"]
    past_t = ps.transform(past.reshape(-1, past.shape[-1])).reshape(past.shape)
    future_t = fs.transform(future.reshape(-1, future.shape[-1])).reshape(future.shape)
    ds = ElectricityDailyDataset(past_t, future_t, np.zeros((len(past), 24), dtype=np.float32))
    loader = DataLoader(ds, batch_size=batch_size, shuffle=False)
    preds = []
    model.eval()
    with torch.no_grad():
        for xb, fb, _ in loader:
            pred = model(xb.to(device), fb.to(device)).cpu().numpy()
            preds.append(pred)
    return ys.inverse_transform(np.vstack(preds))


def restore_target_from_mode(
    pred_model: np.ndarray,
    baseline: np.ndarray,
    target_mode: str,
) -> np.ndarray:
    if target_mode == "residual_blend":
        return pred_model + baseline
    return pred_model


def evaluate_metrics(pred_df: pd.DataFrame, task: str) -> pd.DataFrame:
    pred_col = "y_pred"
    true_col = "y_true"
    rows = []
    for period in ["overall", "1_8", "9_16", "17_24"]:
        sub = pred_df if period == "overall" else pred_df[pred_df["period"] == period]
        pred = sub[pred_col].to_numpy(float)
        true = sub[true_col].to_numpy(float)
        mse = float(np.mean((pred - true) ** 2))
        rows.append(
            {
                "task": task,
                "period": period,
                "n": len(sub),
                "MAE": float(np.mean(np.abs(pred - true))),
                "MSE": mse,
                "RMSE": float(np.sqrt(mse)),
                "R2": float(r2_score(true, pred)) if len(sub) > 1 and np.std(true) > 0 else np.nan,
                "sMAPE": smape(pred, true),
            }
        )
    return pd.DataFrame(rows)


def make_prediction_rows(
    df: pd.DataFrame,
    test_days: list[pd.Timestamp],
    preds: np.ndarray,
    task: str,
    cutoff_hour: int,
    pred_da_map: dict[pd.Timestamp, float] | None = None,
) -> pd.DataFrame:
    rows = []
    target_col = "day_ahead_clearing_price" if task == "da" else "realtime_price"
    for target_day, pred in zip(test_days, preds):
        cur = df[(df["ds"] >= target_day) & (df["ds"] < target_day + pd.Timedelta(days=1))].copy()
        cutoff = compute_cutoff(target_day, cutoff_hour)
        cur["task"] = task
        cur["target_day"] = target_day.date().isoformat()
        cur["decision_day"] = (target_day - pd.Timedelta(days=1)).date().isoformat()
        cur["info_cutoff"] = cutoff.isoformat(sep=" ")
        cur["hour_physical"] = cur["ds"].dt.hour
        cur["hour_business"] = cur["ds"].map(business_hour).astype(int)
        cur["period"] = cur["hour_business"].map(assign_period)
        cur["model_name"] = MODEL_NAME
        cur["y_true"] = cur[target_col].to_numpy(float)
        cur["y_pred"] = pred
        if task == "rt":
            cur["pred_day_ahead_price"] = [pred_da_map[x] for x in cur["ds"]]
            cur["traded"] = (cur["y_pred"] > cur["day_ahead_clearing_price"]).astype(int)
            cur["profit_per_mwh"] = cur["traded"] * (
                cur["realtime_price"] - cur["day_ahead_clearing_price"]
            )
        rows.append(cur)
    return pd.concat(rows, ignore_index=True)


def make_segment_prediction_rows(
    df: pd.DataFrame,
    test_days: list[pd.Timestamp],
    task: str,
    cutoff_hour: int,
    segment_predictions: dict[str, np.ndarray],
    pred_da_map: dict[pd.Timestamp, float] | None = None,
) -> pd.DataFrame:
    stitched_preds = []
    for i, _ in enumerate(test_days):
        day_pred = np.zeros(24, dtype=float)
        for name, start, end in SEGMENTS:
            day_pred[start:end] = segment_predictions[name][i]
        stitched_preds.append(day_pred)
    return make_prediction_rows(
        df=df,
        test_days=test_days,
        preds=np.stack(stitched_preds),
        task=task,
        cutoff_hour=cutoff_hour,
        pred_da_map=pred_da_map,
    )


def plot_prediction(df: pd.DataFrame, out_dir: Path, task: str) -> None:
    plt.figure(figsize=(16, 5))
    plt.plot(df["ds"], df["y_true"], label="actual")
    plt.plot(df["ds"], df["y_pred"], label=f"{MODEL_NAME}_pred")
    plt.legend()
    plt.title(f"{task}_prediction_vs_actual")
    plt.tight_layout()
    plt.savefig(out_dir / f"{task}_prediction_vs_actual.png", dpi=160)
    plt.close()


def audit_protocol(cfg: RunConfig, df: pd.DataFrame) -> list[dict[str, Any]]:
    findings = []
    if cfg.cutoff_hour_da != 15:
        findings.append(
            {
                "severity": "warning",
                "item": "day_ahead_cutoff",
                "detail": f"当前日前 cutoff={cfg.cutoff_hour_da}，不是推荐的 D-1 15:00。",
            }
        )
    if cfg.cutoff_hour_rt != 15:
        findings.append(
            {
                "severity": "warning",
                "item": "realtime_cutoff",
                "detail": f"当前实时 cutoff={cfg.cutoff_hour_rt}，不是推荐的 D-1 15:00。",
            }
        )
    if df["ds"].duplicated().any():
        findings.append(
            {
                "severity": "error",
                "item": "duplicate_timestamp",
                "detail": "输入数据存在重复时间戳。",
            }
        )
    if df["ds"].isna().any():
        findings.append(
            {
                "severity": "error",
                "item": "missing_timestamp",
                "detail": "输入数据存在缺失时间戳。",
            }
        )
    findings.append(
        {
            "severity": "info",
            "item": "pipeline_mode",
            "detail": f"当前运行链已显式标记为 {cfg.pipeline_mode}，结果不得与其他 mode 混用。",
        }
    )
    return findings


def update_leaderboard(
    leaderboard_path: Path,
    cfg: RunConfig,
    da_metrics: pd.DataFrame,
    rt_metrics: pd.DataFrame,
    output_dir: Path,
) -> None:
    leaderboard_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": pd.Timestamp.now().isoformat(sep=" "),
        "month": cfg.month,
        "pipeline_mode": cfg.pipeline_mode,
        "backbone": cfg.backbone,
        "output_dir": str(output_dir),
        "da_smape_overall": float(da_metrics.loc[da_metrics["period"] == "overall", "sMAPE"].iloc[0]),
        "rt_smape_overall": float(rt_metrics.loc[rt_metrics["period"] == "overall", "sMAPE"].iloc[0]),
        "da_smape_17_24": float(da_metrics.loc[da_metrics["period"] == "17_24", "sMAPE"].iloc[0]),
        "rt_smape_17_24": float(rt_metrics.loc[rt_metrics["period"] == "17_24", "sMAPE"].iloc[0]),
    }
    if leaderboard_path.exists():
        old = pd.read_csv(leaderboard_path)
        new_df = pd.concat([old, pd.DataFrame([row])], ignore_index=True)
    else:
        new_df = pd.DataFrame([row])
    new_df.to_csv(leaderboard_path, index=False, encoding="utf-8-sig")


def run_monthly_reproduction(cfg: RunConfig) -> dict[str, Any]:
    set_seed(cfg.seed)
    device = torch.device(
        "cuda" if cfg.device == "auto" and torch.cuda.is_available() else cfg.device
        if cfg.device != "auto"
        else "cpu"
    )
    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    df = load_data(cfg.data_path)
    findings = audit_protocol(cfg, df)

    test_start, test_end = resolve_test_window(cfg)
    if cfg.training_mode == "frozen":
        if not cfg.frozen_train_start or not cfg.frozen_train_end_exclusive:
            raise ValueError("training_mode='frozen' requires frozen_train_start and frozen_train_end_exclusive")
        train_start = pd.Timestamp(cfg.frozen_train_start)
        frozen_end = pd.Timestamp(cfg.frozen_train_end_exclusive)
        train_days_all = date_range_days(train_start, frozen_end)
    else:
        train_start = max(
            df["ds"].min().normalize() + pd.Timedelta(days=8),
            test_start - pd.DateOffset(months=cfg.train_months),
        )
        train_days_all = date_range_days(train_start, test_start)
    train_days, valid_days = split_train_valid(train_days_all, cfg.val_ratio)
    test_days = date_range_days(test_start, test_end)
    da_target_mode = resolve_task_target_mode(cfg, "da")
    rt_target_mode = resolve_task_target_mode(cfg, "rt")
    test_days = filter_available_days(
        df,
        test_days,
        seq_len=cfg.seq_len,
        cutoff_hour_da=cfg.cutoff_hour_da,
        cutoff_hour_rt=cfg.cutoff_hour_rt,
        da_target_mode=da_target_mode,
        rt_target_mode=rt_target_mode,
    )

    if cfg.segment_training:
        da_segment_preds = {}
        da_segment_bias = {}
        for segment_name, start_idx, end_idx in SEGMENTS:
            da_train_past, da_train_future, da_train_y, _ = build_segment_arrays(
                df,
                train_days,
                "day_ahead_clearing_price",
                cfg.seq_len,
                cfg.cutoff_hour_da,
                start_idx,
                end_idx,
                target_mode=da_target_mode,
            )
            da_bundle = train_model(da_train_past, da_train_future, da_train_y, cfg, device, task="da", segment_name=segment_name)
            da_valid_past, da_valid_future, da_valid_y, da_valid_baseline = build_segment_arrays(
                df,
                valid_days,
                "day_ahead_clearing_price",
                cfg.seq_len,
                cfg.cutoff_hour_da,
                start_idx,
                end_idx,
                target_mode=da_target_mode,
            )
            da_valid_pred_model = predict_model(
                da_bundle,
                da_valid_past,
                da_valid_future,
                device,
                cfg.batch_size,
            )
            da_valid_pred = restore_target_from_mode(
                da_valid_pred_model,
                da_valid_baseline,
                da_target_mode,
            )
            da_valid_true = restore_target_from_mode(
                da_valid_y,
                da_valid_baseline,
                da_target_mode,
            )
            da_segment_bias[segment_name] = fit_segment_bias_calibrator(
                da_valid_true,
                da_valid_pred,
                cfg.da_calibration_mode,
                cfg.calibration_shrink,
            )

            da_test_past, da_test_future, _, da_test_baseline = build_segment_arrays(
                df,
                test_days,
                "day_ahead_clearing_price",
                cfg.seq_len,
                cfg.cutoff_hour_da,
                start_idx,
                end_idx,
                target_mode=da_target_mode,
            )
            da_pred_model = predict_model(
                da_bundle,
                da_test_past,
                da_test_future,
                device,
                cfg.batch_size,
            )
            da_segment_preds[segment_name] = restore_target_from_mode(
                da_pred_model,
                da_test_baseline,
                da_target_mode,
            )
            da_segment_preds[segment_name] = apply_bias_calibrator(
                da_segment_preds[segment_name],
                da_segment_bias[segment_name],
            )
        da_pred_df = make_segment_prediction_rows(
            df=df,
            test_days=test_days,
            task="da",
            cutoff_hour=cfg.cutoff_hour_da,
            segment_predictions=da_segment_preds,
        )
        da_bundle_summary = {
            "best_valid_mae_scaled": None,
            "stopped_early": None,
            "segment_bias": {k: v.tolist() for k, v in da_segment_bias.items()},
        }
    else:
        da_target_mode = resolve_task_target_mode(cfg, "da")
        da_train_past, da_train_future, da_train_y, _ = build_arrays(
            df,
            train_days,
            "day_ahead_clearing_price",
            cfg.seq_len,
            cfg.cutoff_hour_da,
            target_mode=da_target_mode,
        )
        da_bundle = train_model(da_train_past, da_train_future, da_train_y, cfg, device, task="da")
        da_valid_past, da_valid_future, da_valid_y, da_valid_baseline = build_arrays(
            df,
            valid_days,
            "day_ahead_clearing_price",
            cfg.seq_len,
            cfg.cutoff_hour_da,
            target_mode=da_target_mode,
        )
        da_valid_pred_model = predict_model(da_bundle, da_valid_past, da_valid_future, device, cfg.batch_size)
        da_valid_pred = restore_target_from_mode(da_valid_pred_model, da_valid_baseline, da_target_mode)
        da_valid_true = restore_target_from_mode(da_valid_y, da_valid_baseline, da_target_mode)
        da_bias = fit_segment_bias_calibrator(
            da_valid_true,
            da_valid_pred,
            cfg.da_calibration_mode,
            cfg.calibration_shrink,
        )

        da_test_past, da_test_future, _, da_test_baseline = build_arrays(
            df,
            test_days,
            "day_ahead_clearing_price",
            cfg.seq_len,
            cfg.cutoff_hour_da,
            target_mode=da_target_mode,
        )
        da_pred_model = predict_model(da_bundle, da_test_past, da_test_future, device, cfg.batch_size)
        da_preds = restore_target_from_mode(da_pred_model, da_test_baseline, da_target_mode)
        da_preds = apply_bias_calibrator(da_preds, da_bias)
        da_pred_df = make_prediction_rows(df, test_days, da_preds, "da", cfg.cutoff_hour_da)
        da_bundle_summary = {**da_bundle, "bias": da_bias.tolist()}
    pred_da_map = da_pred_df.set_index("ds")["y_pred"].to_dict()

    if cfg.segment_training:
        rt_segment_preds = {}
        rt_segment_bias = {}
        rt_segment_affine = {}
        for segment_name, start_idx, end_idx in SEGMENTS:
            rt_train_past, rt_train_future, rt_train_y, _ = build_segment_arrays(
                df,
                train_days,
                "realtime_price",
                cfg.seq_len,
                cfg.cutoff_hour_rt,
                start_idx,
                end_idx,
                pred_da_map=None,
                target_mode=rt_target_mode,
            )
            rt_bundle = train_model(rt_train_past, rt_train_future, rt_train_y, cfg, device, task="rt", segment_name=segment_name)
            rt_valid_past, rt_valid_future, rt_valid_y, rt_valid_baseline = build_segment_arrays(
                df,
                valid_days,
                "realtime_price",
                cfg.seq_len,
                cfg.cutoff_hour_rt,
                start_idx,
                end_idx,
                pred_da_map=None,
                target_mode=rt_target_mode,
            )
            rt_valid_pred_model = predict_model(
                rt_bundle,
                rt_valid_past,
                rt_valid_future,
                device,
                cfg.batch_size,
            )
            rt_valid_pred = restore_target_from_mode(
                rt_valid_pred_model,
                rt_valid_baseline,
                rt_target_mode,
            )
            rt_valid_true = restore_target_from_mode(
                rt_valid_y,
                rt_valid_baseline,
                rt_target_mode,
            )
            if cfg.rt_calibration_mode == "rt_916_affine" and segment_name == "9_16":
                rt_segment_affine[segment_name] = fit_affine_calibrator(
                    rt_valid_true,
                    rt_valid_pred,
                    cfg.affine_clip_min,
                    cfg.affine_clip_max,
                )
                rt_segment_bias[segment_name] = np.zeros(rt_valid_true.shape[1], dtype=float)
            elif cfg.rt_calibration_mode == "rt_916_regime_affine" and segment_name == "9_16":
                rt_segment_affine[segment_name] = fit_regime_affine_calibrator(
                    rt_valid_true,
                    rt_valid_pred,
                    rt_valid_future,
                    cfg,
                )
                rt_segment_bias[segment_name] = np.zeros(rt_valid_true.shape[1], dtype=float)
            elif cfg.rt_calibration_mode == "rt_916_spike_day_affine" and segment_name == "9_16":
                rt_segment_affine[segment_name] = fit_spike_day_affine_calibrator(
                    rt_valid_true,
                    rt_valid_pred,
                    rt_valid_future,
                    cfg,
                )
                rt_segment_bias[segment_name] = np.zeros(rt_valid_true.shape[1], dtype=float)
            elif cfg.rt_calibration_mode == "rt_916_regime_affine_hourbias" and segment_name == "9_16":
                rt_segment_affine[segment_name] = fit_regime_affine_hourbias_calibrator(
                    rt_valid_true,
                    rt_valid_pred,
                    rt_valid_future,
                    cfg,
                )
                rt_segment_bias[segment_name] = np.zeros(rt_valid_true.shape[1], dtype=float)
            elif cfg.rt_calibration_mode == "rt_916_peak_regime_affine" and segment_name == "9_16":
                rt_segment_affine[segment_name] = fit_peak_regime_affine_calibrator(
                    rt_valid_true,
                    rt_valid_pred,
                    rt_valid_future,
                    cfg,
                )
                rt_segment_bias[segment_name] = np.zeros(rt_valid_true.shape[1], dtype=float)
            elif cfg.rt_calibration_mode == "rt_916_peak_regime_bias" and segment_name == "9_16":
                rt_segment_affine[segment_name] = fit_peak_regime_bias_calibrator(
                    rt_valid_true,
                    rt_valid_pred,
                    rt_valid_future,
                    cfg,
                )
                rt_segment_bias[segment_name] = np.zeros(rt_valid_true.shape[1], dtype=float)
            elif cfg.rt_calibration_mode == "rt_916_auto" and segment_name == "9_16":
                auto_result = fit_rt_916_auto_calibrator(
                    rt_valid_true,
                    rt_valid_pred,
                    rt_valid_future,
                    cfg,
                )
                rt_segment_affine[segment_name] = auto_result
                rt_segment_bias[segment_name] = np.asarray(auto_result["bias"], dtype=float)
            elif cfg.rt_calibration_mode in {"rt_916_affine", "rt_916_regime_affine", "rt_916_spike_day_affine", "rt_916_regime_affine_hourbias", "rt_916_peak_regime_affine", "rt_916_peak_regime_bias"}:
                rt_segment_bias[segment_name] = np.zeros(rt_valid_true.shape[1], dtype=float)
            elif cfg.rt_calibration_mode == "rt_916_auto":
                rt_segment_bias[segment_name] = np.zeros(rt_valid_true.shape[1], dtype=float)
            else:
                rt_segment_bias[segment_name] = fit_segment_bias_calibrator(
                    rt_valid_true,
                    rt_valid_pred,
                    cfg.rt_calibration_mode,
                    cfg.calibration_shrink,
                )

            rt_test_past, rt_test_future, _, rt_test_baseline = build_segment_arrays(
                df,
                test_days,
                "realtime_price",
                cfg.seq_len,
                cfg.cutoff_hour_rt,
                start_idx,
                end_idx,
                pred_da_map=pred_da_map,
                target_mode=rt_target_mode,
            )
            rt_pred_model = predict_model(
                rt_bundle,
                rt_test_past,
                rt_test_future,
                device,
                cfg.batch_size,
            )
            rt_segment_preds[segment_name] = restore_target_from_mode(
                rt_pred_model,
                rt_test_baseline,
                rt_target_mode,
            )
            if cfg.rt_calibration_mode == "rt_916_affine" and segment_name == "9_16":
                rt_segment_preds[segment_name] = apply_affine_calibrator(
                    rt_segment_preds[segment_name],
                    rt_segment_affine[segment_name],
                )
            elif cfg.rt_calibration_mode == "rt_916_regime_affine" and segment_name == "9_16":
                rt_segment_preds[segment_name] = apply_regime_affine_calibrator(
                    rt_segment_preds[segment_name],
                    rt_test_future,
                    rt_segment_affine[segment_name],
                )
            elif cfg.rt_calibration_mode == "rt_916_spike_day_affine" and segment_name == "9_16":
                rt_segment_preds[segment_name] = apply_spike_day_affine_calibrator(
                    rt_segment_preds[segment_name],
                    rt_test_future,
                    rt_segment_affine[segment_name],
                )
            elif cfg.rt_calibration_mode == "rt_916_regime_affine_hourbias" and segment_name == "9_16":
                rt_segment_preds[segment_name] = apply_regime_affine_hourbias_calibrator(
                    rt_segment_preds[segment_name],
                    rt_test_future,
                    rt_segment_affine[segment_name],
                )
            elif cfg.rt_calibration_mode == "rt_916_peak_regime_affine" and segment_name == "9_16":
                rt_segment_preds[segment_name] = apply_peak_regime_affine_calibrator(
                    rt_segment_preds[segment_name],
                    rt_test_future,
                    rt_segment_affine[segment_name],
                )
            elif cfg.rt_calibration_mode == "rt_916_peak_regime_bias" and segment_name == "9_16":
                rt_segment_preds[segment_name] = apply_peak_regime_bias_calibrator(
                    rt_segment_preds[segment_name],
                    rt_test_future,
                    rt_segment_affine[segment_name],
                )
            elif cfg.rt_calibration_mode == "rt_916_auto" and segment_name == "9_16":
                auto_mode = rt_segment_affine[segment_name]["selected_mode"]
                rt_segment_preds[segment_name] = apply_rt_916_calibration_mode(
                    rt_segment_preds[segment_name],
                    rt_test_future,
                    auto_mode,
                    rt_segment_affine[segment_name]["affine"],
                    np.asarray(rt_segment_affine[segment_name]["bias"], dtype=float),
                )
            else:
                rt_segment_preds[segment_name] = apply_bias_calibrator(
                    rt_segment_preds[segment_name],
                    rt_segment_bias[segment_name],
                )
        rt_pred_df = make_segment_prediction_rows(
            df=df,
            test_days=test_days,
            task="rt",
            cutoff_hour=cfg.cutoff_hour_rt,
            segment_predictions=rt_segment_preds,
            pred_da_map=pred_da_map,
        )
        rt_bundle_summary = {
            "best_valid_mae_scaled": None,
            "stopped_early": None,
            "segment_bias": {k: v.tolist() for k, v in rt_segment_bias.items()},
            "segment_affine": {
                k: serialize_rt_affine_payload(v)
                for k, v in rt_segment_affine.items()
            },
        }
    else:
        rt_target_mode = resolve_task_target_mode(cfg, "rt")
        rt_train_past, rt_train_future, rt_train_y, _ = build_arrays(
            df,
            train_days,
            "realtime_price",
            cfg.seq_len,
            cfg.cutoff_hour_rt,
            pred_da_map=None,
            target_mode=rt_target_mode,
        )
        rt_bundle = train_model(rt_train_past, rt_train_future, rt_train_y, cfg, device, task="rt")
        rt_valid_past, rt_valid_future, rt_valid_y, rt_valid_baseline = build_arrays(
            df,
            valid_days,
            "realtime_price",
            cfg.seq_len,
            cfg.cutoff_hour_rt,
            pred_da_map=None,
            target_mode=rt_target_mode,
        )
        rt_valid_pred_model = predict_model(rt_bundle, rt_valid_past, rt_valid_future, device, cfg.batch_size)
        rt_valid_pred = restore_target_from_mode(rt_valid_pred_model, rt_valid_baseline, rt_target_mode)
        rt_valid_true = restore_target_from_mode(rt_valid_y, rt_valid_baseline, rt_target_mode)
        rt_affine_obj: Any = None
        if cfg.rt_calibration_mode == "rt_916_affine":
            rt_affine_obj = fit_affine_calibrator(
                rt_valid_true,
                rt_valid_pred,
                cfg.affine_clip_min,
                cfg.affine_clip_max,
            )
            rt_bias = np.zeros(rt_valid_true.shape[1], dtype=float)
        elif cfg.rt_calibration_mode == "rt_916_regime_affine":
            rt_affine_obj = fit_regime_affine_calibrator(
                rt_valid_true,
                rt_valid_pred,
                rt_valid_future,
                cfg,
            )
            rt_bias = np.zeros(rt_valid_true.shape[1], dtype=float)
        elif cfg.rt_calibration_mode == "rt_916_spike_day_affine":
            rt_affine_obj = fit_spike_day_affine_calibrator(
                rt_valid_true,
                rt_valid_pred,
                rt_valid_future,
                cfg,
            )
            rt_bias = np.zeros(rt_valid_true.shape[1], dtype=float)
        elif cfg.rt_calibration_mode == "rt_916_regime_affine_hourbias":
            rt_affine_obj = fit_regime_affine_hourbias_calibrator(
                rt_valid_true,
                rt_valid_pred,
                rt_valid_future,
                cfg,
            )
            rt_bias = np.zeros(rt_valid_true.shape[1], dtype=float)
        elif cfg.rt_calibration_mode == "rt_916_peak_regime_affine":
            rt_affine_obj = fit_peak_regime_affine_calibrator(
                rt_valid_true,
                rt_valid_pred,
                rt_valid_future,
                cfg,
            )
            rt_bias = np.zeros(rt_valid_true.shape[1], dtype=float)
        elif cfg.rt_calibration_mode == "rt_916_peak_regime_bias":
            rt_affine_obj = fit_peak_regime_bias_calibrator(
                rt_valid_true,
                rt_valid_pred,
                rt_valid_future,
                cfg,
            )
            rt_bias = np.zeros(rt_valid_true.shape[1], dtype=float)
        elif cfg.rt_calibration_mode == "rt_916_auto":
            rt_affine_obj = fit_rt_916_auto_calibrator(
                rt_valid_true,
                rt_valid_pred,
                rt_valid_future,
                cfg,
            )
            rt_bias = np.asarray(rt_affine_obj["bias"], dtype=float)
        else:
            rt_bias = fit_segment_bias_calibrator(
                rt_valid_true,
                rt_valid_pred,
                cfg.rt_calibration_mode,
                cfg.calibration_shrink,
            )

        rt_test_past, rt_test_future, _, rt_test_baseline = build_arrays(
            df,
            test_days,
            "realtime_price",
            cfg.seq_len,
            cfg.cutoff_hour_rt,
            pred_da_map=pred_da_map,
            target_mode=rt_target_mode,
        )
        rt_pred_model = predict_model(rt_bundle, rt_test_past, rt_test_future, device, cfg.batch_size)
        rt_preds = restore_target_from_mode(rt_pred_model, rt_test_baseline, rt_target_mode)
        if cfg.rt_calibration_mode == "rt_916_auto":
            rt_preds = apply_rt_916_calibration_mode(
                rt_preds,
                rt_test_future,
                rt_affine_obj["selected_mode"],
                rt_affine_obj["affine"],
                np.asarray(rt_affine_obj["bias"], dtype=float),
            )
        elif cfg.rt_calibration_mode in {
            "rt_916_affine",
            "rt_916_regime_affine",
            "rt_916_spike_day_affine",
            "rt_916_regime_affine_hourbias",
            "rt_916_peak_regime_affine",
            "rt_916_peak_regime_bias",
        }:
            rt_preds = apply_rt_916_calibration_mode(
                rt_preds,
                rt_test_future,
                cfg.rt_calibration_mode,
                rt_affine_obj,
                np.asarray(rt_bias, dtype=float),
            )
        else:
            rt_preds = apply_bias_calibrator(rt_preds, rt_bias)
        rt_pred_df = make_prediction_rows(
            df,
            test_days,
            rt_preds,
            "rt",
            cfg.cutoff_hour_rt,
            pred_da_map=pred_da_map,
        )
        rt_bundle_summary = {
            **rt_bundle,
            "bias": rt_bias.tolist(),
            "segment_affine": serialize_rt_affine_payload(rt_affine_obj),
        }

    da_metrics = evaluate_metrics(da_pred_df, "da")
    rt_metrics = evaluate_metrics(rt_pred_df, "rt")
    predictions_raw = pd.concat([da_pred_df, rt_pred_df], ignore_index=True)
    metrics_by_period = pd.concat([da_metrics, rt_metrics], ignore_index=True)

    predictions_raw.to_csv(out_dir / "predictions_raw.csv", index=False, encoding="utf-8-sig")
    metrics_by_period.to_csv(out_dir / "metrics_by_period.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(findings).to_csv(out_dir / "protocol_audit.csv", index=False, encoding="utf-8-sig")

    manifest = {
        "model_name": MODEL_NAME,
        "pipeline_mode": cfg.pipeline_mode,
        "backbone": cfg.backbone,
        "rt_916_backbone": cfg.rt_916_backbone,
        "month": cfg.month,
        "explicit_test_start": cfg.test_start,
        "explicit_test_end_exclusive": cfg.test_end_exclusive,
        "data_path": cfg.data_path,
        "output_dir": str(out_dir),
        "entry_script": "fusion/runners/run_timemixer_export.py",
        "train_start": str(train_start),
        "valid_start": str(valid_days[0] if valid_days else ""),
        "test_start": str(test_start),
        "test_end_exclusive": str(test_end),
        "train_months": cfg.train_months,
        "training_mode": cfg.training_mode,
        "frozen_train_start": cfg.frozen_train_start,
        "frozen_train_end_exclusive": cfg.frozen_train_end_exclusive,
        "decomposition_mode": cfg.decomposition_mode,
        "val_ratio": cfg.val_ratio,
        "seq_len": cfg.seq_len,
        "segment_training": cfg.segment_training,
        "target_mode": cfg.target_mode,
        "da_target_mode": resolve_task_target_mode(cfg, "da"),
        "rt_target_mode": resolve_task_target_mode(cfg, "rt"),
        "da_calibration_mode": cfg.da_calibration_mode,
        "da_loss_mode": cfg.da_loss_mode,
        "da_under_weight_multiplier": cfg.da_under_weight_multiplier,
        "rt_calibration_mode": cfg.rt_calibration_mode,
        "rt_loss_mode": cfg.rt_loss_mode,
        "rt_risk_profile": cfg.rt_risk_profile,
        "rt_peak_weight_multiplier": cfg.rt_peak_weight_multiplier,
        "rt_normal_focus_multiplier": cfg.rt_normal_focus_multiplier,
        "calibration_shrink": cfg.calibration_shrink,
        "affine_clip_min": cfg.affine_clip_min,
        "affine_clip_max": cfg.affine_clip_max,
        "regime_solar_ratio_threshold": cfg.regime_solar_ratio_threshold,
        "regime_bidding_ratio_threshold": cfg.regime_bidding_ratio_threshold,
        "regime_bidding_space_threshold": cfg.regime_bidding_space_threshold,
        "peak_da_threshold": cfg.peak_da_threshold,
        "peak_bidding_space_threshold": cfg.peak_bidding_space_threshold,
        "peak_solar_ratio_max": cfg.peak_solar_ratio_max,
        "cutoff_hour_da": cfg.cutoff_hour_da,
        "cutoff_hour_rt": cfg.cutoff_hour_rt,
        "device": str(device),
        "seed": cfg.seed,
        "epochs": cfg.epochs,
        "batch_size": cfg.batch_size,
        "hidden_dim": cfg.hidden_dim,
        "blocks": cfg.blocks,
        "scales": cfg.scales,
        "dropout": cfg.dropout,
        "rt_segment_head_mode": cfg.rt_segment_head_mode,
        "lr": cfg.lr,
        "weight_decay": cfg.weight_decay,
        "patience": cfg.patience,
        "da_best_valid_mae_scaled": da_bundle_summary["best_valid_mae_scaled"],
        "rt_best_valid_mae_scaled": rt_bundle_summary["best_valid_mae_scaled"],
        "da_stopped_early": da_bundle_summary["stopped_early"],
        "rt_stopped_early": rt_bundle_summary["stopped_early"],
        "da_segment_bias": da_bundle_summary.get("segment_bias"),
        "rt_segment_bias": rt_bundle_summary.get("segment_bias"),
        "rt_segment_affine": rt_bundle_summary.get("segment_affine"),
        "protocol_findings": findings,
    }
    (out_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    plot_prediction(da_pred_df, out_dir, "da")
    plot_prediction(rt_pred_df, out_dir, "rt")
    if cfg.append_leaderboard:
        update_leaderboard(Path(cfg.leaderboard_path), cfg, da_metrics, rt_metrics, out_dir)

    return {
        "manifest": manifest,
        "da_metrics": da_metrics,
        "rt_metrics": rt_metrics,
        "output_dir": str(out_dir),
    }


def parse_args() -> RunConfig:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-path", default=r"D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\epf\data\shandong_pmos_hourly.csv")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--month", required=True)
    parser.add_argument("--test-start")
    parser.add_argument("--test-end-exclusive")
    parser.add_argument("--pipeline-mode", default="single_task", choices=["single_task", "historical_joint"])
    parser.add_argument("--backbone", default="timemixer", choices=["timemixer", "timesnet"])
    parser.add_argument("--rt-916-backbone", choices=["timemixer", "timesnet"])
    parser.add_argument("--train-months", type=int, default=12)
    parser.add_argument("--training-mode", default="rolling", choices=["rolling", "frozen"])
    parser.add_argument("--frozen-train-start")
    parser.add_argument("--frozen-train-end-exclusive")
    parser.add_argument("--decomposition-mode", default="none", choices=["none", "vmd"])
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--seq-len", type=int, default=168)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--hidden-dim", type=int, default=64)
    parser.add_argument("--blocks", type=int, default=2)
    parser.add_argument("--scales", type=int, default=3)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--rt-segment-head-mode", default="none", choices=["none", "future_residual"])
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--patience", type=int, default=15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--cutoff-hour-da", type=int, default=15)
    parser.add_argument("--cutoff-hour-rt", type=int, default=15)
    parser.add_argument("--disable-segment-training", action="store_true")
    parser.add_argument("--target-mode", default="direct", choices=["residual_blend", "direct"])
    parser.add_argument("--da-target-mode", choices=["residual_blend", "direct"])
    parser.add_argument("--rt-target-mode", choices=["residual_blend", "direct"])
    parser.add_argument("--da-calibration-mode", default="none", choices=["none", "segment_bias", "segment_bias_shrink", "hour_bias"])
    parser.add_argument("--da-loss-mode", default="l1", choices=["l1", "asymmetric_under"])
    parser.add_argument("--da-under-weight-multiplier", type=float, default=1.25)
    parser.add_argument("--rt-calibration-mode", default="none", choices=["none", "segment_bias", "segment_bias_shrink", "hour_bias", "rt_916_affine", "rt_916_regime_affine", "rt_916_spike_day_affine", "rt_916_regime_affine_hourbias", "rt_916_peak_regime_affine", "rt_916_peak_regime_bias", "rt_916_auto"])
    parser.add_argument("--rt-loss-mode", default="l1", choices=["l1", "risk_hour_weighted", "risk_peak_weighted"])
    parser.add_argument("--rt-risk-profile", default="baseline", choices=["baseline", "solar_focus", "peak_focus"])
    parser.add_argument("--rt-peak-weight-multiplier", type=float, default=1.4)
    parser.add_argument("--rt-normal-focus-multiplier", type=float, default=1.2)
    parser.add_argument("--calibration-shrink", type=float, default=0.5)
    parser.add_argument("--affine-clip-min", type=float, default=0.7)
    parser.add_argument("--affine-clip-max", type=float, default=1.3)
    parser.add_argument("--regime-solar-ratio-threshold", type=float, default=0.28)
    parser.add_argument("--regime-bidding-ratio-threshold", type=float, default=0.08)
    parser.add_argument("--regime-bidding-space-threshold", type=float, default=4000.0)
    parser.add_argument("--peak-da-threshold", type=float, default=300.0)
    parser.add_argument("--peak-bidding-space-threshold", type=float, default=22000.0)
    parser.add_argument("--peak-solar-ratio-max", type=float, default=0.22)
    parser.add_argument("--append-leaderboard", action="store_true")
    args = parser.parse_args()
    cfg = RunConfig(
        data_path=args.data_path,
        output_dir=args.output_dir,
        month=args.month,
        test_start=args.test_start,
        test_end_exclusive=args.test_end_exclusive,
        pipeline_mode=args.pipeline_mode,
        backbone=args.backbone,
        rt_916_backbone=args.rt_916_backbone,
        train_months=args.train_months,
        training_mode=args.training_mode,
        frozen_train_start=args.frozen_train_start,
        frozen_train_end_exclusive=args.frozen_train_end_exclusive,
        decomposition_mode=args.decomposition_mode,
        val_ratio=args.val_ratio,
        seq_len=args.seq_len,
        epochs=args.epochs,
        batch_size=args.batch_size,
        hidden_dim=args.hidden_dim,
        blocks=args.blocks,
        scales=args.scales,
        dropout=args.dropout,
        rt_segment_head_mode=args.rt_segment_head_mode,
        lr=args.lr,
        weight_decay=args.weight_decay,
        patience=args.patience,
        seed=args.seed,
        device=args.device,
        cutoff_hour_da=args.cutoff_hour_da,
        cutoff_hour_rt=args.cutoff_hour_rt,
        segment_training=not args.disable_segment_training,
        target_mode=args.target_mode,
        da_target_mode=args.da_target_mode,
        rt_target_mode=args.rt_target_mode,
        da_calibration_mode=args.da_calibration_mode,
        da_loss_mode=args.da_loss_mode,
        da_under_weight_multiplier=args.da_under_weight_multiplier,
        rt_calibration_mode=args.rt_calibration_mode,
        rt_loss_mode=args.rt_loss_mode,
        rt_risk_profile=args.rt_risk_profile,
        rt_peak_weight_multiplier=args.rt_peak_weight_multiplier,
        rt_normal_focus_multiplier=args.rt_normal_focus_multiplier,
        calibration_shrink=args.calibration_shrink,
        affine_clip_min=args.affine_clip_min,
        affine_clip_max=args.affine_clip_max,
        regime_solar_ratio_threshold=args.regime_solar_ratio_threshold,
        regime_bidding_ratio_threshold=args.regime_bidding_ratio_threshold,
        regime_bidding_space_threshold=args.regime_bidding_space_threshold,
        peak_da_threshold=args.peak_da_threshold,
        peak_bidding_space_threshold=args.peak_bidding_space_threshold,
        peak_solar_ratio_max=args.peak_solar_ratio_max,
        append_leaderboard=args.append_leaderboard,
    )
    return cfg


def main() -> None:
    cfg = parse_args()
    result = run_monthly_reproduction(cfg)
    print(json.dumps(result["manifest"], ensure_ascii=False, indent=2))
    print(result["da_metrics"].to_string(index=False))
    print(result["rt_metrics"].to_string(index=False))


if __name__ == "__main__":
    main()
