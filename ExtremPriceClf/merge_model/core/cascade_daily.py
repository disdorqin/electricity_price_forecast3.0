"""
整合后的日滚动两阶段预测逻辑。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .extreme_price_radar.pipeline import RadarPipeline
from .extreme_price_radar.generate_oof_prob_feature import generate_oof_prob_feature
from .stage2_model.lightgbm_model import LightgbmModel
from .stage2_model.xgboost_model import XgboostModel
from .stage2_model.catboost_model import CatboostModel


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


@dataclass
class Stage2Config:
    """二阶段模型配置。"""

    feature_type: str = "预测值"
    model_name: str = "lightgbm"
    threshold: Optional[float] = 0.5
    gray_low: float = 0.13
    gray_high: float = 0.68
    dynamic_gray_enabled: bool = False
    dynamic_window_days: int = 90
    dynamic_min_samples: int = 1000
    dynamic_min_positives: int = 80
    dynamic_recall_min: float = 0.95
    dynamic_precision_min: float = 0.80
    dynamic_coverage_min: float = 0.75
    dynamic_positive_coverage_min: float = 0.66
    dynamic_low_min: float = 0.05
    dynamic_low_max: float = 0.30
    dynamic_high_min: float = 0.50
    dynamic_high_max: float = 0.90
    dynamic_low_step: float = 0.01
    dynamic_high_step: float = 0.01
    dynamic_max_delta: float = 0.02
    dynamic_smooth_alpha: float = 0.30


def set_seed(seed: int = 42) -> None:
    """固定随机种子，保证滚动结果可复现。"""
    np.random.seed(seed)


def read_table(file_path: str) -> pd.DataFrame:
    """读取 CSV 或 Excel 文件为 DataFrame。"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(file_path)
    if ext == ".csv":
        return pd.read_csv(file_path)
    raise ValueError(f"不支持的文件格式: {ext}")


def prepare_dataset(data_file: str, time_col: str = "时刻") -> pd.DataFrame:
    """加载数据并排序，暂不附加 OOF 概率。"""
    # 读取主数据，并确保时间列类型正确。
    data_df = read_table(data_file)
    data_df[time_col] = pd.to_datetime(data_df[time_col])
    data_df = data_df.sort_values(time_col).reset_index(drop=True)
    return data_df


def load_or_init_p1_cache(p1_cache_path: str, time_col: str = "时刻") -> pd.DataFrame:
    """读取或初始化 p1 概率缓存表。"""
    cache_df = pd.DataFrame({
        time_col: pd.Series(dtype="datetime64[ns]"),
        "p1_prob_OOF": pd.Series(dtype="float64"),
        "prob_source": pd.Series(dtype="object"),
    })
    if os.path.exists(p1_cache_path) and os.path.isfile(p1_cache_path):
        try:
            cache_df = read_table(p1_cache_path)
            cache_df[time_col] = pd.to_datetime(cache_df[time_col])
        except Exception as exc:
            logger.warning(f"p1 缓存文件读取失败，将重新初始化: {exc}")
    cache_df = cache_df.drop_duplicates(subset=[time_col], keep="last")
    return cache_df


def save_p1_cache(cache_df: pd.DataFrame, p1_cache_path: str) -> None:
    """保存 p1 概率缓存表。"""
    os.makedirs(os.path.dirname(p1_cache_path), exist_ok=True)
    cache_df.sort_values("时刻").to_excel(p1_cache_path, index=False)


def is_cache_complete(
    cache_df: pd.DataFrame,
    data_df: pd.DataFrame,
    time_col: str,
    start_dt: pd.Timestamp,
    end_dt: pd.Timestamp,
) -> bool:
    """检查缓存是否覆盖指定时间范围的全部时刻且概率非空。"""
    expected_times = data_df[(data_df[time_col] >= start_dt) & (data_df[time_col] <= end_dt)][time_col]
    if expected_times.empty:
        return False
    cache_map = cache_df.set_index(time_col)
    try:
        cache_slice = cache_map.loc[expected_times.values]
    except KeyError:
        return False
    if cache_slice["p1_prob_OOF"].isnull().any():
        return False
    return True


def build_oof_probabilities(
    data_df: pd.DataFrame,
    train_end_time: str,
    extreme_threshold: float,
) -> pd.DataFrame:
    """对训练区间生成 OOF 概率特征（不落盘）。"""
    # 仅使用训练区间数据生成 OOF 概率，避免未来信息泄露。
    oof_df = generate_oof_prob_feature(
        data_df,
        output_folder_path=None,
        end_time=train_end_time,
        extreme_threshold=extreme_threshold,
    )
    return oof_df


def update_cache_with_oof(
    cache_df: pd.DataFrame,
    data_df: pd.DataFrame,
    cutoff_time: pd.Timestamp,
    price_threshold: float,
) -> pd.DataFrame:
    """生成截止时间前的 OOF 概率并写入缓存。"""
    oof_df = build_oof_probabilities(
        data_df,
        train_end_time=cutoff_time.strftime("%Y-%m-%d %H:%M:%S"),
        extreme_threshold=price_threshold,
    )
    oof_df = oof_df.copy()
    oof_df["prob_source"] = "oof"
    merged_cache = pd.concat([cache_df, oof_df], ignore_index=True)
    merged_cache = merged_cache.sort_values("时刻").drop_duplicates(subset=["时刻"], keep="last")
    return merged_cache


def update_cache_with_predictions(
    cache_df: pd.DataFrame,
    time_col: str,
    times: np.ndarray,
    probs: np.ndarray,
) -> pd.DataFrame:
    """将预测概率写入缓存（prob_source=predict）。"""
    update_df = pd.DataFrame({time_col: times, "p1_prob_OOF": probs, "prob_source": "predict"})
    update_df[time_col] = pd.to_datetime(update_df[time_col])
    merged_cache = pd.concat([cache_df, update_df], ignore_index=True)
    merged_cache = merged_cache.sort_values(time_col).drop_duplicates(subset=[time_col], keep="last")
    return merged_cache


def backfill_pred_probabilities(
    df: pd.DataFrame,
    cache_df: pd.DataFrame,
    target_name: str,
    price_threshold: float,
    train_start: pd.Timestamp,
    start_dt: pd.Timestamp,
    end_dt: pd.Timestamp,
    min_precision: float,
) -> pd.DataFrame:
    """对指定区间滚动预测并补齐 p1 概率缓存。"""
    current_dt = start_dt
    while current_dt <= end_dt:
        current_end = current_dt + pd.Timedelta(hours=23)
        current_train_end = current_dt - pd.Timedelta(hours=25)
        train_mask = (df["时刻"] >= train_start) & (df["时刻"] <= current_train_end)
        train_df = df[train_mask].copy()
        infer_mask = df["时刻"] <= current_end
        infer_df = df[infer_mask].tail(24 * 9).copy()
        if len(train_df) < 100 or len(infer_df) < 24 * 9:
            current_dt += pd.Timedelta(days=1)
            continue
        pipeline = RadarPipeline(target_col=target_name, extreme_threshold=price_threshold, min_precision=min_precision)
        pipeline.run_training_pipeline(train_df)
        _, p1_prob = pipeline.run_inference(infer_df)
        actual_times = infer_df.iloc[-24:]["时刻"].values
        cache_df = update_cache_with_predictions(cache_df, "时刻", actual_times, p1_prob)
        current_dt += pd.Timedelta(days=1)
    return cache_df


def advanced_stage2_feature_engineering_real_value(df: pd.DataFrame) -> pd.DataFrame:
    """二阶段灰度区特征工程（实际值特征）。"""
    df = df.drop(columns=[col for col in df.columns if col.endswith("预测值")])
    col_load = "直调负荷实际值"
    col_wind = "风电总加实际值"
    col_solar = "光伏总加实际值"
    df = df.copy()
    if "时刻" in df.columns:
        df["时刻"] = pd.to_datetime(df["时刻"])
        df["hour"] = df["时刻"].dt.hour
        df["month"] = df["时刻"].dt.month
        df["weekday"] = df["时刻"].dt.weekday
    else:
        raise ValueError("输入数据必须包含'时刻'列")
    df["is_中午光伏时段"] = df["hour"].isin([10, 11, 12, 13, 14, 15]).astype(int)
    df["is_凌晨低谷时段"] = df["hour"].isin([0, 1, 2, 3, 4, 5]).astype(int)
    df["净负荷预测"] = df[col_load] - (df[col_wind] + df[col_solar])
    df["新能源渗透率"] = (df[col_wind] + df[col_solar]) / (df[col_load] + 1e-5)
    if "新能源渗透率" in df.columns:
        df["交互_中午_渗透率"] = df["新能源渗透率"] * df["is_中午光伏时段"]
    if col_wind in df.columns:
        df["交互_凌晨_风电"] = df[col_wind] * df["is_凌晨低谷时段"]
    col_line = "联络线受电负荷实际值"
    if col_line in df.columns and col_load in df.columns:
        df["联络线刚性占比"] = df[col_line] / (df[col_load] + 1e-5)
    col_nuclear = "核电总加实际值"
    if col_nuclear in df.columns and col_load in df.columns:
        df["核电刚性占比"] = df[col_nuclear] / (df[col_load] + 1e-5)
    df["净负荷24h滞后"] = df["净负荷预测"].shift(24)
    exclude_list = ["weekday", "hour", "month", "试验机组总加实际值", "is_凌晨低谷时段", "净负荷预测", "新能源渗透率"]
    df = df.drop(columns=[col for col in df.columns if col in exclude_list])
    return df


def advanced_stage2_feature_engineering_pred_value(df: pd.DataFrame) -> pd.DataFrame:
    """二阶段灰度区特征工程（预测值特征）。"""
    df = df.drop(columns=[col for col in df.columns if col.endswith("实际值")])
    col_load = "直调负荷预测值"
    col_wind = "风电总加预测值"
    col_solar = "光伏总加预测值"
    df = df.copy()
    if "时刻" in df.columns:
        df["时刻"] = pd.to_datetime(df["时刻"])
        df["hour"] = df["时刻"].dt.hour
        df["month"] = df["时刻"].dt.month
        df["weekday"] = df["时刻"].dt.weekday
    else:
        raise ValueError("输入数据必须包含'时刻'列")
    df["is_中午光伏时段"] = df["hour"].isin([10, 11, 12, 13, 14, 15]).astype(int)
    df["is_凌晨低谷时段"] = df["hour"].isin([0, 1, 2, 3, 4, 5]).astype(int)
    df["净负荷预测"] = df[col_load] - (df[col_wind] + df[col_solar])
    df["新能源渗透率"] = (df[col_wind] + df[col_solar]) / (df[col_load] + 1e-5)
    if "新能源渗透率" in df.columns:
        df["交互_中午_渗透率"] = df["新能源渗透率"] * df["is_中午光伏时段"]
    if col_wind in df.columns:
        df["交互_凌晨_风电"] = df[col_wind] * df["is_凌晨低谷时段"]
    col_line = "联络线受电负荷预测值"
    if col_line in df.columns and col_load in df.columns:
        df["联络线刚性占比"] = df[col_line] / (df[col_load] + 1e-5)
    col_nuclear = "核电总加预测值"
    if col_nuclear in df.columns and col_load in df.columns:
        df["核电刚性占比"] = df[col_nuclear] / (df[col_load] + 1e-5)
    df["净负荷24h滞后"] = df["净负荷预测"].shift(24)
    exclude_list = ["weekday", "hour", "month", "试验机组总加预测值", "is_凌晨低谷时段", "净负荷预测", "新能源渗透率"]
    df = df.drop(columns=[col for col in df.columns if col in exclude_list])
    return df


def build_stage2_features(df: pd.DataFrame, feature_type: str) -> pd.DataFrame:
    """根据配置选择二阶段特征工程。"""
    # 根据输入的特征类型执行对应的特征工程。
    if feature_type not in ["实际值", "预测值"]:
        raise ValueError("feature_type 参数只能为 '实际值' 或 '预测值'")
    if feature_type == "实际值":
        return advanced_stage2_feature_engineering_real_value(df)
    return advanced_stage2_feature_engineering_pred_value(df)


def _init_stage2_model(model_name: str, spw: float):
    """按名称初始化二阶段模型。"""
    # 统一在此处做模型选择，避免上层重复判断。
    if model_name == "lightgbm":
        return LightgbmModel(spw=spw)
    if model_name == "catboost":
        return CatboostModel(spw=spw)
    if model_name == "xgboost":
        return XgboostModel(spw=spw)
    raise ValueError("model_name 只支持 lightgbm/catboost/xgboost")


def train_stage2_model(
    train_df: pd.DataFrame,
    cache_df: pd.DataFrame,
    feature_type: str,
    price_threshold: float,
    train_start: pd.Timestamp,
    current_day_start: pd.Timestamp,
    gray_low: float,
    gray_high: float,
    model_name: str,
) -> Tuple[Optional[object], Optional[List[str]], float]:
    """训练二阶段模型并返回模型、特征列表与阈值。"""
    # 构造二阶段训练标签并合并缓存概率。
    train_df = train_df.copy()
    train_df = train_df.merge(cache_df[["时刻", "p1_prob_OOF"]], on="时刻", how="left")
    train_df["label"] = (train_df["实时电价"] < price_threshold).astype(int)
    train_df = train_df.dropna(subset=["p1_prob_OOF"])
    # 仅保留灰度区样本做二阶段训练。
    feature_df = build_stage2_features(train_df, feature_type)
    gray_train_df = feature_df[(feature_df["p1_prob_OOF"] > gray_low) & (feature_df["p1_prob_OOF"] < gray_high)].copy()
    gray_train_df = gray_train_df.sort_values("时刻")
    if len(gray_train_df) == 0:
        logger.info("[Stage2] 灰度训练样本为空，跳过训练。")
        return None, None, 0.5
    split_idx = int(len(gray_train_df) * 0.8)
    gray_train_part = gray_train_df.iloc[:split_idx].copy()
    gray_val_part = gray_train_df.iloc[split_idx:].copy()
    # 明确剔除标签与时间列。
    exclude_cols = ["时刻", "日前电价", "实时电价", "label"]
    features_to_use = [col for col in gray_train_df.columns if col not in exclude_cols]
    X_train_s2 = gray_train_part[features_to_use]
    y_train_s2 = gray_train_part["label"]
    spw = (len(y_train_s2) - y_train_s2.sum()) / y_train_s2.sum() if y_train_s2.sum() > 0 else 1.0
    # 初始化二阶段模型并加权训练。
    stage2_model = _init_stage2_model(model_name, spw)
    time_range = (current_day_start - train_start).total_seconds()
    if time_range > 0:
        recency_weight = (gray_train_part["时刻"] - train_start).dt.total_seconds() / time_range
        recency_weight = recency_weight.clip(lower=0, upper=1).values + 1.0
    else:
        recency_weight = np.ones(len(gray_train_part))
    sample_weight = np.sqrt(recency_weight)
    sample_weight = np.clip(sample_weight, 0.5, 2.0)
    sample_weight = sample_weight / sample_weight.mean()
    stage2_model.model.fit(X_train_s2, y_train_s2, sample_weight=sample_weight)
    threshold_s2 = 0.5
    if len(gray_val_part) > 0:
        X_val_s2 = gray_val_part[features_to_use]
        y_val_s2 = gray_val_part["label"].values
        p2_val = stage2_model.predict_proba(X_val_s2)
        candidate_thresholds = np.linspace(0.3, 0.8, 51)
        best_f2 = -1.0
        for th in candidate_thresholds:
            preds = (p2_val > th).astype(int)
            f2 = _fbeta_score(y_val_s2, preds, beta=2)
            if f2 > best_f2:
                best_f2 = f2
                threshold_s2 = float(th)
        logger.info(f"[Stage2] 校准阈值: {threshold_s2:.2f} | F2: {best_f2:.4f}")
    return stage2_model, features_to_use, threshold_s2


def _fbeta_score(y_true: np.ndarray, y_pred: np.ndarray, beta: float = 2.0) -> float:
    """轻量 F-beta 计算，避免引入额外依赖。"""
    # 直接手动实现 F-beta，避免引入 sklearn 依赖。
    tp = np.sum((y_true == 1) & (y_pred == 1))
    fp = np.sum((y_true == 0) & (y_pred == 1))
    fn = np.sum((y_true == 1) & (y_pred == 0))
    if tp == 0:
        return 0.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    beta2 = beta ** 2
    denom = (beta2 * precision + recall)
    if denom == 0:
        return 0.0
    return (1 + beta2) * precision * recall / denom


def _safe_div(numerator: float, denominator: float) -> float:
    """安全除法，分母为 0 时返回 0。"""
    return float(numerator) / float(denominator) if denominator else 0.0


def _evaluate_gray_thresholds(
    probs: np.ndarray,
    labels: np.ndarray,
    low: float,
    high: float,
) -> Optional[Dict[str, float]]:
    """评估一组灰度阈值在窗口内的业务指标。"""
    if low >= high:
        return None
    total_samples = len(probs)
    if total_samples == 0:
        return None
    total_pos = int(labels.sum())
    certain_mask = (probs <= low) | (probs >= high)
    certain_count = int(certain_mask.sum())
    if certain_count == 0:
        return None
    preds = (probs[certain_mask] >= high).astype(int)
    truths = labels[certain_mask]
    tp = int(np.sum((preds == 1) & (truths == 1)))
    fp = int(np.sum((preds == 1) & (truths == 0)))
    fn = int(np.sum((preds == 0) & (truths == 1)))
    precision_certain = _safe_div(tp, tp + fp)
    recall_certain = _safe_div(tp, tp + fn)
    coverage = _safe_div(certain_count, total_samples)
    pos_coverage = _safe_div(tp, total_pos)
    return {
        "gray_low": float(low),
        "gray_high": float(high),
        "certain_precision": precision_certain,
        "certain_recall": recall_certain,
        "certain_coverage": coverage,
        "positive_coverage": pos_coverage,
        "certain_count": float(certain_count),
        "total_count": float(total_samples),
        "positive_count": float(total_pos),
    }


def _search_dynamic_gray_thresholds(
    probs: np.ndarray,
    labels: np.ndarray,
    stage2_config: Stage2Config,
) -> Optional[Dict[str, float]]:
    """按约束搜索最优灰度阈值。"""
    low_grid = np.arange(
        stage2_config.dynamic_low_min,
        stage2_config.dynamic_low_max + stage2_config.dynamic_low_step / 2,
        stage2_config.dynamic_low_step,
    )
    high_grid = np.arange(
        stage2_config.dynamic_high_min,
        stage2_config.dynamic_high_max + stage2_config.dynamic_high_step / 2,
        stage2_config.dynamic_high_step,
    )
    candidates: List[Dict[str, float]] = []
    for low in low_grid:
        low = round(float(low), 4)
        for high in high_grid:
            high = round(float(high), 4)
            metrics = _evaluate_gray_thresholds(probs, labels, low, high)
            if metrics is None:
                continue
            if metrics["certain_recall"] < stage2_config.dynamic_recall_min:
                continue
            if metrics["certain_precision"] < stage2_config.dynamic_precision_min:
                continue
            if metrics["certain_coverage"] < stage2_config.dynamic_coverage_min:
                continue
            if metrics["positive_coverage"] < stage2_config.dynamic_positive_coverage_min:
                continue
            candidates.append(metrics)
    if not candidates:
        return None
    candidates.sort(
        key=lambda x: (
            x["certain_precision"],
            x["positive_coverage"],
            x["certain_coverage"],
            -(x["gray_high"] - x["gray_low"]),
        ),
        reverse=True,
    )
    return candidates[0]


def _stabilize_threshold(
    current: float,
    previous: float,
    max_delta: float,
    alpha: float,
) -> float:
    """对阈值做限幅与指数平滑，降低日间抖动。"""
    # 阈值改变最多previous+-max_delta
    lower_bound = previous - max_delta
    upper_bound = previous + max_delta
    clipped = min(max(current, lower_bound), upper_bound)

    smoothed = alpha * clipped + (1.0 - alpha) * previous
    return float(smoothed)


def select_dynamic_gray_thresholds(
    train_df_stage2: pd.DataFrame,
    cache_df: pd.DataFrame,
    target_name: str,
    price_threshold: float,
    current_train_end: pd.Timestamp,
    stage2_config: Stage2Config,
    prev_gray_low: float,
    prev_gray_high: float,
) -> Tuple[float, float, Dict[str, float]]:
    """基于历史窗口动态选择灰度阈值，带完整性检查与回退，并记录回退原因。"""
    window_candidates = [
        stage2_config.dynamic_window_days,
        max(30, stage2_config.dynamic_window_days // 2),
        30,
    ]
    window_candidates = list(dict.fromkeys([int(w) for w in window_candidates if w > 0]))
    best_metrics: Optional[Dict[str, float]] = None
    selected_window: Optional[int] = None
    fallback_reason = "未知原因"  # 新增：用于记录回退原因

    for window_days in window_candidates:
        window_start = current_train_end - pd.Timedelta(days=window_days) + pd.Timedelta(hours=1)
        window_df = train_df_stage2[(train_df_stage2["时刻"] >= window_start) & (train_df_stage2["时刻"] <= current_train_end)].copy()

        # 原因 1：历史窗口总样本量不足
        if len(window_df) < stage2_config.dynamic_min_samples:
            fallback_reason = f"{window_days}天窗口总样本数不足 ({len(window_df)} < {stage2_config.dynamic_min_samples})"
            continue

        window_df = window_df[["时刻", target_name]].copy()
        window_df["label"] = (window_df[target_name] < price_threshold).astype(int)
        positive_count = int(window_df["label"].sum())

        # 原因 2：历史窗口正样本（极值）数量不足
        if positive_count < stage2_config.dynamic_min_positives:
            fallback_reason = f"{window_days}天窗口极值样本不足 ({positive_count} < {stage2_config.dynamic_min_positives})"
            continue

        cache_slice = cache_df[(cache_df["时刻"] >= window_start) & (cache_df["时刻"] <= current_train_end)][["时刻", "p1_prob_OOF"]].copy()
        merged_df = window_df.merge(cache_slice, on="时刻", how="left")

        # 原因 3：历史 OOF 概率缓存存在缺失
        if merged_df["p1_prob_OOF"].isnull().any():
            fallback_reason = f"{window_days}天窗口内存在缺失的 OOF 概率"
            continue

        probs = merged_df["p1_prob_OOF"].to_numpy(dtype=float)
        labels = merged_df["label"].to_numpy(dtype=int)
        metrics = _search_dynamic_gray_thresholds(probs, labels, stage2_config)

        # 原因 4：计算出的阈值无法同时满足 Precision/Recall 等业务约束
        if metrics is None:
            fallback_reason = f"{window_days}天窗口未找到满足下限约束的阈值组合"
            continue

        best_metrics = metrics
        selected_window = window_days
        break

    if best_metrics is None:
        fallback_info = {
            "source": "fallback_previous",
            "reason": fallback_reason,  # 记录最终的回退原因
            "window_days": 0.0,
            "certain_precision": np.nan,
            "certain_recall": np.nan,
            "certain_coverage": np.nan,
            "positive_coverage": np.nan,
        }
        return prev_gray_low, prev_gray_high, fallback_info

    dynamic_low = _stabilize_threshold(
        current=best_metrics["gray_low"],
        previous=prev_gray_low,
        max_delta=stage2_config.dynamic_max_delta,
        alpha=stage2_config.dynamic_smooth_alpha,
    )

    dynamic_high = _stabilize_threshold(
        current=best_metrics["gray_high"],
        previous=prev_gray_high,
        max_delta=stage2_config.dynamic_max_delta,
        alpha=stage2_config.dynamic_smooth_alpha,
    )

    dynamic_low = min(max(dynamic_low, stage2_config.dynamic_low_min), stage2_config.dynamic_low_max)
    dynamic_high = min(max(dynamic_high, stage2_config.dynamic_high_min), stage2_config.dynamic_high_max)
    min_gap = 0.05

    if dynamic_high - dynamic_low < min_gap:
        dynamic_high = min(stage2_config.dynamic_high_max, dynamic_low + min_gap)
        if dynamic_high - dynamic_low < min_gap:
            dynamic_low = max(stage2_config.dynamic_low_min, dynamic_high - min_gap)

    dynamic_low = round(dynamic_low, 4)
    dynamic_high = round(dynamic_high, 4)

    metrics_info = {
        "source": "dynamic",
        "reason": "成功",  # 成功时记录为成功
        "window_days": float(selected_window or 0),
        "certain_precision": best_metrics["certain_precision"],
        "certain_recall": best_metrics["certain_recall"],
        "certain_coverage": best_metrics["certain_coverage"],
        "positive_coverage": best_metrics["positive_coverage"],
    }
    return dynamic_low, dynamic_high, metrics_info


def _compute_stage1_probabilities(pipeline: RadarPipeline, infer_df: pd.DataFrame, time_col: str) -> pd.DataFrame:
    """对推理窗口内全量样本生成阶段1概率。"""
    # 注意：这里返回的是全量窗口的概率，方便后续与原数据按时间对齐。
    df_features = pipeline.fe.process(infer_df)
    aligned_df = pipeline.dt.drop_actual_features(df_features)
    cols_to_exclude = [time_col, pipeline.target_col, "label"]
    feature_cols = [c for c in aligned_df.columns if c not in cols_to_exclude]
    X_infer = aligned_df[feature_cols]
    p1_prob_all = pipeline.clf1.predict_proba(X_infer)
    prob_df = aligned_df[[time_col]].copy()
    prob_df["p1_prob_stage1"] = p1_prob_all
    return prob_df


def run_rolling_daily_cascade(
    df: pd.DataFrame,
    target_name: str,
    price_threshold: float,
    test_time_range: List[str],
    train_start: str,
    stage2_train_start: str,
    stage2_config: Stage2Config,
    p1_cache_path: str,
    oof_cutoff: str,
    min_precision: float = 0.7,
) -> pd.DataFrame:
    """执行按日滚动的两阶段级联回测。"""
    time_col = "时刻"
    df = df.copy()
    df[time_col] = pd.to_datetime(df[time_col])
    infer_start_dt = pd.to_datetime(test_time_range[0])
    infer_end_dt = pd.to_datetime(test_time_range[1])
    train_start_dt = pd.to_datetime(train_start)
    stage2_train_start_dt = pd.to_datetime(stage2_train_start)
    cutoff_dt = pd.to_datetime(oof_cutoff)
    cache_df = load_or_init_p1_cache(p1_cache_path, time_col=time_col)
    cache_ready = is_cache_complete(cache_df, df, time_col, infer_start_dt, infer_end_dt)
    if not cache_ready:
        cache_df = update_cache_with_oof(cache_df, df[df[time_col] >= stage2_train_start_dt], cutoff_dt, price_threshold)
        pred_fill_start = cutoff_dt + pd.Timedelta(hours=1)
        pred_fill_end = infer_start_dt - pd.Timedelta(hours=1)
        if pred_fill_start <= pred_fill_end:
            cache_df = backfill_pred_probabilities(
                df,
                cache_df,
                target_name,
                price_threshold,
                stage2_train_start_dt,
                pred_fill_start,
                pred_fill_end,
                min_precision,
            )
        save_p1_cache(cache_df, p1_cache_path)
    df_end_time = df[time_col].tolist()[-1]
    if df_end_time < infer_end_dt:
        raise ValueError(f"数据截止到 {df_end_time} 不在数据集内，请检查！")
    total_days = (infer_end_dt - infer_start_dt).days + 1
    logger.info(f"预测范围: {infer_start_dt} 至 {infer_end_dt}，共计滚动 {total_days} 天。")
    all_results: List[Dict[str, object]] = []
    prev_gray_low = stage2_config.gray_low
    prev_gray_high = stage2_config.gray_high
    for i in range(total_days):
        # 每天重置随机种子，确保滚动训练可复现。
        set_seed(42)
        # 计算当日训练窗与推理窗。
        current_infer_start = infer_start_dt + pd.Timedelta(days=i)
        current_infer_end = current_infer_start + pd.Timedelta(hours=23)
        logger.info("===============================================================================")
        logger.info(f"滚动进度: 第 {i + 1}/{total_days} 天 | {current_infer_start} -> {current_infer_end}")
        current_train_end = current_infer_start - pd.Timedelta(hours=25)
        # 切分训练与推理数据。
        train_mask_stage1 = (df[time_col] >= train_start_dt) & (df[time_col] <= current_train_end)
        train_df_stage1 = df[train_mask_stage1].copy()
        train_mask_stage2 = (df[time_col] >= stage2_train_start_dt) & (df[time_col] <= current_train_end)
        train_df_stage2 = df[train_mask_stage2].copy()
        infer_mask = df[time_col] <= current_infer_end
        infer_df = df[infer_mask].tail(24 * 9).copy()
        if len(train_df_stage1) < 100:
            logger.warning("训练集数据量过少，跳过本日。")
            continue
        if len(infer_df) < 24 * 9:
            logger.warning("推理特征流不足 24*9 小时，跳过本日。")
            continue
        if target_name == "实时电价":
            target_name = "实时电价" if "实时电价" in df.columns else "实时价格"
        if target_name == "日前电价":
            target_name = "日前电价" if "日前电价" in df.columns else "日前价格"
        current_gray_low = stage2_config.gray_low
        current_gray_high = stage2_config.gray_high

        # 未开启动态灰度的情况增加默认reason：未开启动态灰度
        threshold_meta: Dict[str, float] = {
            "source": "fixed",
            "reason": "未开启动态灰度",
            "window_days": 0.0,
            "certain_precision": np.nan,
            "certain_recall": np.nan,
            "certain_coverage": np.nan,
            "positive_coverage": np.nan,
        }
        if stage2_config.dynamic_gray_enabled:
            current_gray_low, current_gray_high, threshold_meta = select_dynamic_gray_thresholds(
                train_df_stage2=train_df_stage2,
                cache_df=cache_df,
                target_name=target_name,
                price_threshold=price_threshold,
                current_train_end=current_train_end,
                stage2_config=stage2_config,
                prev_gray_low=prev_gray_low,
                prev_gray_high=prev_gray_high,
            )
            prev_gray_low = current_gray_low
            prev_gray_high = current_gray_high

            logger.info(
                "[GrayThreshold] mode=%s reason=%s window=%s low=%.4f high=%.4f precision=%.4f recall=%.4f coverage=%.4f pos_coverage=%.4f",
                threshold_meta["source"],
                threshold_meta.get("reason", "未知"),
                int(threshold_meta["window_days"]),
                current_gray_low,
                current_gray_high,
                threshold_meta["certain_precision"],
                threshold_meta["certain_recall"],
                threshold_meta["certain_coverage"],
                threshold_meta["positive_coverage"],
            )
        # 每天重新训练阶段1模型。
        pipeline = RadarPipeline(target_col=target_name, extreme_threshold=price_threshold, min_precision=min_precision)
        pipeline.run_training_pipeline(train_df_stage1)
        # 阶段1推理输出全量概率与标签。
        p1_pred, p1_prob = pipeline.run_inference(infer_df)
        # 额外生成阶段1全量概率用于灰度区路由与二阶段特征拼接。
        prob_df = _compute_stage1_probabilities(pipeline, infer_df, time_col)
        infer_df = infer_df.merge(prob_df, on=time_col, how="left")
        infer_df["p1_prob_OOF"] = infer_df["p1_prob_stage1"]
        infer_df = infer_df.drop(columns=["p1_prob_stage1"])
        # 训练二阶段模型（仅灰度区样本）。
        stage2_model, features_to_use, threshold_s2 = train_stage2_model(
            train_df_stage2,
            cache_df,
            stage2_config.feature_type,
            price_threshold,
            stage2_train_start_dt,
            current_infer_start,
            current_gray_low,
            current_gray_high,
            stage2_config.model_name,
        )
        # 如配置固定阈值则覆盖校准阈值。
        threshold_s2 = stage2_config.threshold if stage2_config.threshold is not None else threshold_s2
        # 计算二阶段特征并截取最后 24 小时。
        stage2_features_df = build_stage2_features(infer_df, stage2_config.feature_type)
        stage2_features_df = stage2_features_df.sort_values(time_col).reset_index(drop=True)
        stage2_last24 = stage2_features_df.iloc[-24:].copy()
        if features_to_use is None:
            exclude_cols = ["时刻", "日前电价", "实时电价", "label"]
            features_to_use = [col for col in stage2_last24.columns if col not in exclude_cols]
        X_stage2_last24 = stage2_last24[features_to_use]
        # 初始化二阶段输出数组，仅灰度区填充。
        p2_prob = np.full(24, np.nan)
        p2_pred = np.full(24, np.nan)
        gray_mask = (p1_prob >= current_gray_low) & (p1_prob <= current_gray_high)
        if stage2_model is not None and gray_mask.any():
            p2_prob_gray = stage2_model.predict_proba(X_stage2_last24[gray_mask])
            p2_prob[gray_mask] = p2_prob_gray
            p2_pred[gray_mask] = (p2_prob_gray > threshold_s2).astype(int)
        # 合并阶段1/2结果为最终预测。
        final_pred = np.where(
            p1_prob > current_gray_high,
            1,
            np.where(p1_prob < current_gray_low, 0, (p2_prob > threshold_s2).astype(int)),
        )
        actual_slice = infer_df.iloc[-24:]
        actual_times = actual_slice[time_col].values
        actual_prices = actual_slice[target_name].values
        cache_df = update_cache_with_predictions(cache_df, time_col, actual_times, p1_prob)
        save_p1_cache(cache_df, p1_cache_path)
        for t, a, p1p, p1l, p2p, p2l, fp in zip(
            actual_times,
            actual_prices,
            p1_prob,
            p1_pred,
            p2_prob,
            p2_pred,
            final_pred,
        ):
            all_results.append(
                {
                    "时刻": t,
                    f"{target_name}": a,
                    "真实极值标签": 1 if a < price_threshold else 0,
                    "p1_prob": p1p,
                    "p1_pred": int(p1l),
                    "p2_prob": p2p,
                    "p2_pred": int(p2l) if not np.isnan(p2l) else None,
                    "gray_low": current_gray_low,
                    "gray_high": current_gray_high,
                    "gray_source": threshold_meta["source"],
                    "gray_reason": threshold_meta.get("reason", ""),
                    "final_pred": int(fp),
                }
            )
    # 汇总评估指标并保存结果。
    results_df = pd.DataFrame(all_results)
    if len(results_df) == 0:
        logger.warning("未生成任何结果，请检查时间范围与数据完整性。")
        return
    correct_alarms = np.sum((results_df["final_pred"] == 1) & (results_df["真实极值标签"] == 1))
    missed_alarms = np.sum((results_df["final_pred"] == 0) & (results_df["真实极值标签"] == 1))
    false_alarms = np.sum((results_df["final_pred"] == 1) & (results_df["真实极值标签"] == 0))
    total_extremes = results_df["真实极值标签"].sum()
    logger.info("========== 日滚动回测报告 ==========")
    logger.info(f"目标列: {target_name}")
    logger.info(f"测试范围: {test_time_range[0]} 至 {test_time_range[1]}")
    logger.info(f"共计验证时刻数: {len(results_df)}")
    logger.info(f"真实发生 <{price_threshold} 的极值次数: {total_extremes}")
    logger.info(f"成功拦截 (命中): {correct_alarms}")
    logger.info(f"遗憾漏报 (未命中): {missed_alarms}")
    logger.info(f"虚假警报 (误报): {false_alarms}")
    if (correct_alarms + false_alarms) > 0:
        precision = correct_alarms / (correct_alarms + false_alarms)
        logger.info(f"综合精确率 (Precision): {precision:.2%}")
    if total_extremes > 0:
        recall = correct_alarms / total_extremes
        logger.info(f"综合召回率 (Recall): {recall:.2%}")
        if (correct_alarms + false_alarms) > 0:
            f1 = 2 * precision * recall / (precision + recall)
            f2 = 5 * precision * recall / (4 * precision + recall)
            logger.info(f"F1 分数: {f1:.2%}")
            logger.info(f"F2 分数 (β=2): {f2:.2%}")

    return results_df
