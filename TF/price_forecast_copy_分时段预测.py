"""
TimesFM 电价预测程序 - 分时段预测版本

本程序是电力价格预测系统的核心模块之一，使用Google的TimesFM预训练模型进行电价预测。
在项目中的角色：
- 支持日前电价和实时电价预测
- 支持分时段预测策略（将24小时分成多段分别预测）
- 提供历史回测和实际预测两种模式
- 作为runners/run_timesfm.py的底层实现

与LightGBM的区别：
- TimesFM是预训练大模型，无需训练，直接推理
- 使用外生变量（协变量）而非传统特征工程
- 支持端到端序列预测（一次预测24个点）

作者: AI Assistant
日期: 2025
"""

from __future__ import annotations

import argparse
import os
import random
from dotenv import load_dotenv

# 设置HuggingFace镜像站，加速模型下载
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
import re
import sys
from dataclasses import dataclass
from typing import Dict, List

import numpy as np
import pandas as pd

from pathlib import Path

# 加载环境变量（PROJECT_ROOT, DATA_SET_NAME等）
load_dotenv(override=True)

# =============================================================================
# 全局配置常量
# =============================================================================

# 目标变量配置：定义如何识别数据集中的列
TARGET_CFG = {
    "dayahead": {
        "keywords": ["日前电价", "日前"],  # 用于自动识别日前电价列
        "exclude": ["实时", "价差"],       # 排除包含这些关键词的列
    },
    "realtime": {
        "keywords": ["实时电价", "实时"],  # 用于自动识别实时电价列
        "exclude": ["日前", "价差"],
    }
}

# 目标变量别名映射：支持中文简写输入
TARGET_ALIASES = {
    "日前": "dayahead",
    "实时": "realtime"
}

# 业务日起始小时：电力市场习惯，01:00为一天开始
DAY_START_HOUR = 1

# 电价下限：山东电力市场规则，电价不能低于-80元/MWh
PRICE_FLOOR_DA_RT = -80.0


# =============================================================================
# 可复现性设置
# =============================================================================

def set_reproducibility(seed: int, deterministic: bool = True) -> None:
    """
    设置随机种子，确保实验结果可复现
    
    在电力预测项目中，可复现性很重要：
    - 便于调试和对比不同模型的表现
    - 确保同样的输入产生同样的输出
    
    Args:
        seed: 随机种子
        deterministic: 是否使用确定性算法（可能影响性能，但结果更稳定）
    """
    os.environ["PYTHONHASHSEED"] = str(seed)
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

    random.seed(seed)
    np.random.seed(seed)

    import torch

    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.set_float32_matmul_precision("highest")
    if deterministic:
        torch.use_deterministic_algorithms(True)


# =============================================================================
# 数据加载与预处理工具函数
# =============================================================================

def _clean_numeric(s: pd.Series) -> pd.Series:
    """
    清洗数值列：去除千分位逗号、空格等非数字字符
    
    电力市场数据经常包含格式化的数字（如"1,234.56"），需要清洗后才能计算
    
    Args:
        s: 输入的pandas Series
        
    Returns:
        清洗后的数值Series
    """
    return pd.to_numeric(
        s.astype(str)
        .str.replace(",", "", regex=False)      # 去除千分位逗号
        .str.replace("\u00a0", "", regex=False)  # 去除不间断空格
        .str.replace(" ", "", regex=False),      # 去除普通空格
        errors="coerce",  # 无法转换的设为NaN
    )


def _find_column(df: pd.DataFrame, keywords: List[str]) -> str:
    """
    根据关键词自动查找列名
    
    电力市场数据文件的列名可能不统一，通过关键词匹配实现自动识别
    
    Args:
        df: 数据DataFrame
        keywords: 关键词列表
        
    Returns:
        匹配到的列名
        
    Raises:
        ValueError: 找不到匹配的列
        
    示例:
        >>> _find_column(df, ["日前电价", "日前"])
        '日前电价(元/MWh)'
    """
    for col in df.columns:
        if any(k in col for k in keywords):
            return col
    raise ValueError(f"找不到列: {keywords}")


def _read_table(
    path: str,
    *,
    encoding: str | None = None,
    sheet_name: str | int | None = 0,
) -> pd.DataFrame:
    """
    读取数据文件（Excel或CSV）
    
    电力市场数据通常以Excel或CSV格式提供，本函数自动识别格式并读取
    对于CSV文件，自动尝试多种编码（处理中文编码问题）
    
    Args:
        path: 文件路径
        encoding: CSV编码（可选，自动检测）
        sheet_name: Excel的sheet名称或索引
        
    Returns:
        读取的DataFrame
    """
    ext = os.path.splitext(path)[1].lower()
    
    # Excel文件
    if ext in {".xlsx", ".xls", ".xlsm", ".xlsb", ".ods"}:
        return pd.read_excel(path, sheet_name=sheet_name)
    
    # CSV文件，指定了编码
    if encoding:
        return pd.read_csv(path, encoding=encoding)
    
    # CSV文件，自动检测编码
    last_err: Exception | None = None
    for enc in ("utf-8", "utf-8-sig", "gb18030", "gbk", "cp936"):
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError as e:
            last_err = e
    
    raise UnicodeDecodeError(
        "utf-8",
        getattr(last_err, "object", b""),
        getattr(last_err, "start", 0),
        getattr(last_err, "end", 0),
        f"无法解码文件 {path}；请显式指定 --encoding（常见：gbk / gb18030 / utf-8-sig）",
    )


def load_features(
    path: str,
    *,
    encoding: str | None = None,
    sheet_name: str | int | None = 0,
) -> pd.DataFrame:
    """
    加载特征数据（核心数据加载函数）
    
    在项目中的角色：
    - 所有模型（TimesFM、LightGBM等）的数据入口
    - 统一处理时间列、数值清洗、去重等
    
    处理流程：
    1. 读取文件（Excel/CSV）
    2. 自动识别时间列
    3. 清洗数值列
    4. 设置为时间索引
    5. 去除重复时间点
    
    Args:
        path: 数据文件路径
        encoding: CSV编码
        sheet_name: Excel sheet名称
        
    Returns:
        处理后的DataFrame，以时间为索引
    """
    df = _read_table(path, encoding=encoding, sheet_name=sheet_name)
    df.columns = [str(c).strip() for c in df.columns]

    # 自动识别时间列：优先找"时刻"列，否则找包含时间关键词的列
    time_col = next((c for c in df.columns if c == "时刻"), None) or next(
        (c for c in df.columns if any(k in c.lower() for k in ["时刻", "时间", "timestamp", "date"])),
        None,
    )
    if not time_col:
        raise ValueError("缺少时间列")
    
    # 转换时间为datetime格式
    df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
    df = df.dropna(subset=[time_col]).sort_values(time_col)

    # 清洗所有数值列
    for col in df.columns:
        if col == time_col:
            continue
        df[col] = _clean_numeric(df[col])

    # 设置时间索引，并处理到小时精度
    df = df.set_index(time_col)
    df.index = df.index.to_series().dt.floor("h")
    
    # 去除重复时间点（保留最后一个）
    df = df[~df.index.duplicated(keep="last")]
    return df


def _build_target(
    df: pd.DataFrame, target_key: str
) -> tuple[pd.DataFrame, str]:
    """
    构建目标变量列
    
    支持三种预测目标：
    - dayahead: 日前电价
    - realtime: 实时电价
    - spread: 价差（实时 - 日前）
    
    Args:
        df: 输入数据
        target_key: 目标类型（"dayahead"/"realtime"/"spread"）
        
    Returns:
        (处理后的DataFrame, 目标列名)
    """
    if target_key in {"dayahead", "realtime"}:
        # 日前或实时电价：直接找对应列
        target_col = _find_column(df, TARGET_CFG[target_key]["keywords"])
        df = df.dropna(subset=[target_col])  # 删除目标值为空的行
        return df, target_col

    if target_key == "spread":
        # 价差：实时电价 - 日前电价
        da_col = _find_column(df, TARGET_CFG["dayahead"]["keywords"])
        rt_col = _find_column(df, TARGET_CFG["realtime"]["keywords"])
        df = df.dropna(subset=[da_col, rt_col]).copy()
        target_col = "_价差"
        df[target_col] = df[rt_col] - df[da_col]  # 计算价差
        return df, target_col

    raise ValueError(f"未知目标: {target_key}")


def load_dataset(
    path: str,
    target_key: str,
    *,
    encoding: str | None = None,
    sheet_name: str | int | None = 0,
) -> tuple[pd.DataFrame, str]:
    """
    加载完整数据集（特征+目标）
    
    这是数据加载的入口函数，在项目中被：
    - runners/run_timesfm.py 调用
    - 直接运行本脚本时调用
    
    Args:
        path: 数据文件路径
        target_key: 目标类型
        encoding: CSV编码
        sheet_name: Excel sheet名称
        
    Returns:
        (DataFrame, 目标列名)
    """
    df = load_features(path, encoding=encoding, sheet_name=sheet_name)
    return _build_target(df, target_key)


# =============================================================================
# 特征工程函数
# =============================================================================

def build_time_features(index: pd.Index) -> Dict[str, np.ndarray]:
    """
    构建时间特征（正弦/余弦编码）
    
    在TimesFM中，时间特征作为外生变量（协变量）输入模型。
    使用正弦/余弦编码可以保留时间的周期性（如23点和0点接近）。
    
    Args:
        index: 时间索引
        
    Returns:
        时间特征字典，包含hour_sin, hour_cos, dow_sin, dow_cos
        
    示例:
        >>> build_time_features(pd.date_range('2025-01-01', periods=24, freq='h'))
        {
            'hour_sin': array([0.0, 0.26, ...]),  # 小时的正弦编码
            'hour_cos': array([1.0, 0.96, ...]),  # 小时的余弦编码
            'dow_sin': array([...]),              # 星期几的正弦编码
            'dow_cos': array([...])               # 星期几的余弦编码
        }
    """
    series = pd.Index(index).to_series()
    hours = series.dt.hour.to_numpy()
    dows = series.dt.dayofweek.to_numpy()
    return {
        "hour_sin": np.sin(2 * np.pi * hours / 24.0).astype(np.float32),
        "hour_cos": np.cos(2 * np.pi * hours / 24.0).astype(np.float32),
        "dow_sin": np.sin(2 * np.pi * dows / 7.0).astype(np.float32),
        "dow_cos": np.cos(2 * np.pi * dows / 7.0).astype(np.float32),
    }


# =============================================================================
# 评估指标函数
# =============================================================================

def smape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """
    计算对称平均绝对百分比误差（sMAPE）
    
    公式：sMAPE = mean(|y_true - y_pred| / ((|y_true| + |y_pred|) / 2)) * 100
    
    特殊处理：
    - 电价<50时，按50计算（避免低电价时的极端百分比误差）
    
    在项目中的角色：
    - 与LightGBM、Prophet等模型使用统一的评估标准
    - 准确率 = max(0, 100 - sMAPE)
    
    Args:
        y_true: 真实值数组
        y_pred: 预测值数组
        
    Returns:
        sMAPE值（百分比）
    """
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    
    # 特殊处理：电价<50时，按50计算
    y_true = np.maximum(y_true, 50)
    y_pred = np.maximum(y_pred, 50)
    
    # 计算sMAPE
    denom = (np.abs(y_true) + np.abs(y_pred)) / 2.0
    denom = np.where(denom == 0, 1.0, denom)  # 避免除零

    smape = np.mean(np.abs(y_true - y_pred) / denom) * 100.0
    return float(smape)


def spread_weighted_accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """
    计算价差加权准确率
    
    用于价差预测评估，考虑价差大小的加权准确率。
    大价差的预测正确对结果的贡献更大。
    
    Args:
        y_true: 真实价差数组
        y_pred: 预测价差数组
        
    Returns:
        加权准确率（百分比）
    """
    weights = np.abs(y_true)  # 用价差的绝对值作为权重
    denom = np.sum(weights)
    if denom == 0:
        return float("nan")
    
    # 判断方向是否正确（正负号是否相同）
    matches = np.sign(y_true) == np.sign(y_pred)
    
    # 加权平均
    return float(np.sum(matches * weights) / denom * 100.0)


def spread_direction_accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """
    计算价差方向准确率
    
    用于价差预测评估，只判断价差的正负方向是否正确。
    
    Args:
        y_true: 真实价差数组
        y_pred: 预测价差数组
        
    Returns:
        方向准确率（百分比）
    """
    if y_true.size == 0:
        return float("nan")
    
    # 判断方向是否正确
    matches = np.sign(y_true) == np.sign(y_pred)
    return float(matches.mean() * 100.0)


def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray, target: str) -> dict:
    """
    计算评估指标
    
    根据预测目标类型（日前/实时/价差）计算相应的评估指标。
    
    Args:
        y_true: 真实值数组
        y_pred: 预测值数组
        target: 目标类型（"dayahead"/"realtime"/"spread"）
        
    Returns:
        指标字典
    """
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    
    # 基础指标
    mse = float(np.mean((y_true - y_pred) ** 2))
    mae = float(np.mean(np.abs(y_true - y_pred)))
    rmse = float(np.sqrt(mse))
    mape = float(
        np.mean(np.abs((y_true - y_pred) / np.clip(np.abs(y_true), 1e-8, None))) * 100.0
    )
    
    # R²决定系数
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan")

    results = {"MSE": mse, "MAE": mae, "RMSE": rmse, "MAPE%": mape, "R2": r2}

    if target in {"dayahead", "realtime"}:
        # 日前/实时电价：使用sMAPE和准确率
        s = smape(y_true, y_pred)
        results["SMAPE%"] = s
        results["全天电价预测准确率%"] = max(0.0, 100.0 - s)
    else:
        # 价差预测：使用方向准确率
        results["价差方向准确率%"] = spread_direction_accuracy(y_true, y_pred)
        results["价差加权准确率%"] = spread_weighted_accuracy(y_true, y_pred)
        
    return results


# =============================================================================
# 业务时间处理函数
# =============================================================================

def _compute_trading_day_hour(
    index: pd.DatetimeIndex, *, day_start_hour: int = DAY_START_HOUR
) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """
    计算业务日期和业务小时
    
    电力市场习惯：一天从01:00开始，到次日00:00结束（共24小时）。
    物理时间的00:00归属前一天的24:00。
    
    与LightGBM的1秒偏移法效果相同，但实现方式不同：
    - LightGBM：减去1秒
    - TimesFM：减去1小时（可配置）
    
    Args:
        index: 时间索引
        day_start_hour: 业务日起始小时（默认1）
        
    Returns:
        (业务日期数组, 业务小时数组[1-24])
        
    示例:
        >>> _compute_trading_day_hour(pd.DatetimeIndex(['2025-04-23 00:00', '2025-04-23 01:00']))
        (DatetimeIndex(['2025-04-22', '2025-04-23']), array([24, 1]))
    """
    if not isinstance(index, pd.DatetimeIndex):
        index = pd.DatetimeIndex(index)
    
    # 减去起始小时，实现业务时间偏移
    shifted = index - pd.Timedelta(hours=day_start_hour)
    
    # 业务日期：取整到日期
    trading_day = shifted.normalize()
    
    # 业务小时：0-23 → 1-24
    trading_hour = (shifted.hour + 1).astype(np.int16)
    
    return trading_day, trading_hour


def _complete_trading_days(index: pd.DatetimeIndex) -> List[pd.Timestamp]:
    """
    找出完整的交易日（有24个小时的数据）
    
    在历史回测时使用，筛选出数据完整的天数进行评估。
    避免评估数据不完整的天（如今天只到14:00）。
    
    在项目中的角色：
    - 评估模式：筛选可评估的完整天数
    - 预测模式：验证预测日期是否完整
    
    Args:
        index: 时间索引
        
    Returns:
        完整交易日的日期列表
    """
    trading_day, trading_hour = _compute_trading_day_hour(index)
    df = pd.DataFrame({"d": trading_day, "h": trading_hour}, index=index)
    
    # 按业务日期分组统计
    g = df.groupby("d")["h"]
    stats = g.agg(["count", "min", "max", "nunique"])
    
    # 筛选完整的交易日：
    # - count == 24: 有24条记录
    # - nunique == 24: 覆盖24个不同的小时
    # - min == 1: 从1点开始
    # - max == 24: 到24点结束
    ok = stats[
        (stats["count"] == 24)
        & (stats["nunique"] == 24)
        & (stats["min"] == 1)
        & (stats["max"] == 24)
    ].index
    
    return [pd.Timestamp(d).normalize() for d in ok.sort_values()]


def _select_eval_days(
    df_index: pd.DatetimeIndex,
    *,
    eval_days: int,
    date_range: str | None,
) -> List[pd.Timestamp]:
    """
    选择评估日期
    
    支持两种方式：
    1. 指定日期范围（--date-range）
    2. 取最近N天（--eval-days）
    
    Args:
        df_index: 数据时间索引
        eval_days: 评估天数（取最近N天）
        date_range: 日期范围字符串（如"2026-01-01~2026-01-31"）
        
    Returns:
        评估日期列表
    """
    complete_days = _complete_trading_days(df_index)
    if not complete_days:
        return []

    if date_range:
        # 指定日期范围
        start, end = _parse_date_range(date_range)
        requested = pd.date_range(start, end, freq="D")
        allowed = set(complete_days)
        selected = [pd.Timestamp(d).normalize() for d in requested if pd.Timestamp(d).normalize() in allowed]
        if not selected:
            return []
        return selected

    if eval_days <= 0:
        return []
    
    # 取最近N天
    return complete_days[-eval_days:]


# =============================================================================
# 分时段预测工具函数
# =============================================================================

def _build_segments(segment_count: int) -> List[tuple[int, int]]:
    """
    将24小时分成若干段
    
    分时段预测策略：将一天分成多段分别预测，再拼接成完整的24小时。
    与LightGBM的三段式（Valley/Solar/Peak）不同，这里是等分。
    
    Args:
        segment_count: 分段数量（1=不分段，3=分3段等）
        
    Returns:
        时段列表，每个元素为(起始小时, 结束小时)
        
    示例:
        >>> _build_segments(1)
        [(1, 24)]
        >>> _build_segments(3)
        [(1, 8), (9, 16), (17, 24)]
    """
    if segment_count <= 0:
        raise ValueError("--segment-count 必须为正整数")
    
    if segment_count == 1:
        return [(1, 24)]  # 不分段
    
    # 计算每段长度
    base = 24 // segment_count      # 基础长度
    rem = 24 % segment_count        # 余数（前rem段多1小时）
    
    segments = []
    cur = 1
    for k in range(segment_count):
        length = base + 1 if k < rem else base
        end = cur + length - 1
        segments.append((cur, end))
        cur = end + 1
    
    # 验证覆盖完整性
    if not segments or segments[0][0] != 1 or segments[-1][1] != 24:
        raise ValueError("分段计算错误：未覆盖 1..24")
    
    return segments


def _delta_hours(index: pd.DatetimeIndex) -> np.ndarray:
    """
    计算相邻时间点的间隔（小时）
    
    作为外生变量输入模型，帮助模型处理不规则时间序列或缺失数据。
    
    Args:
        index: 时间索引
        
    Returns:
        时间间隔数组（小时）
    """
    if not isinstance(index, pd.DatetimeIndex):
        index = pd.DatetimeIndex(index)
    if len(index) == 0:
        return np.zeros((0,), dtype=np.float32)
    
    # 计算相邻时间的差值
    diffs = index.to_series().diff().dt.total_seconds().div(3600.0).to_numpy()
    diffs = diffs.copy()
    
    # 第一个点设为1小时
    diffs[0] = 1.0
    
    # 处理异常值
    diffs = np.nan_to_num(diffs, nan=1.0, posinf=1.0, neginf=1.0).astype(np.float32)
    diffs = np.clip(diffs, 1.0, 1e6)  # 限制范围
    
    return diffs


def _timestamps_for_trading_day(day: pd.Timestamp) -> pd.DatetimeIndex:
    """
    生成某交易日的24个小时级时间戳
    
    Args:
        day: 交易日期
        
    Returns:
        24个小时的时间索引（01:00-24:00）
    """
    start = pd.Timestamp(day).normalize() + pd.Timedelta(hours=DAY_START_HOUR)
    return pd.date_range(start=start, periods=24, freq="h")


def _segment_start_ts(day: pd.Timestamp, segment_start_hour: int) -> pd.Timestamp:
    """
    计算时段起始时间戳
    
    Args:
        day: 交易日期
        segment_start_hour: 时段起始小时（1-24）
        
    Returns:
        时段起始时间戳
    """
    return pd.Timestamp(day).normalize() + pd.Timedelta(hours=segment_start_hour)


# =============================================================================
# 预测核心数据结构
# =============================================================================

@dataclass(frozen=True)
class _WindowResult:
    """
    预测结果数据类
    
    存储某一天的预测结果，包括：
    - day: 业务日期
    - ts: 时间戳序列（24个小时）
    - y_true: 真实值（评估时有，预测时可能为NaN）
    - y_pred: 预测值
    """
    day: pd.Timestamp
    ts: pd.DatetimeIndex
    y_true: np.ndarray
    y_pred: np.ndarray


# =============================================================================
# 核心预测函数
# =============================================================================

def _predict_segment_windows(
    *,
    model,
    df: pd.DataFrame,
    target_col: str,
    target_key: str,
    segment: tuple[int, int],
    eval_days: List[pd.Timestamp],
    skip_style: str,
    exog_mode: str,
    emit_warnings: bool = True,
) -> List[_WindowResult]:
    """
    对指定时段进行预测（核心预测函数）
    
    这是TimesFM预测的核心逻辑，对某一时段（如1-8点）的所有评估日进行预测。
    
    在项目中的角色：
    - 被forecast_next_day调用（预测未来一天）
    - 被forecast调用（历史回测多天）
    
    Args:
        model: TimesFM模型实例
        df: 数据DataFrame
        target_col: 目标列名
        target_key: 目标类型（"dayahead"/"realtime"/"spread"）
        segment: 时段元组(起始小时, 结束小时)
        eval_days: 评估日期列表
        skip_style: 预测策略（"normal"/"gap"/"stitch"）
        exog_mode: 外生变量模式（"pred"/"actual"）
        emit_warnings: 是否输出警告
        
    Returns:
        预测结果列表
    """
    seg_start_h, seg_end_h = segment
    seg_len = int(seg_end_h - seg_start_h + 1)

    # 提取该时段的数据
    _, trading_hour = _compute_trading_day_hour(df.index)
    mask = (trading_hour >= seg_start_h) & (trading_hour <= seg_end_h)
    df_seg = df.loc[mask]
    if df_seg.empty:
        return []

    y_seg = df_seg[target_col].astype(np.float32).to_numpy()
    idx_seg = df_seg.index

    # 构建外生变量（协变量）
    hist_exog, pred_exog = build_exog_sources_single(
        df_seg,
        target_col,
        target_key,
        exog_mode=exog_mode,
    )
    
    # 构建时间特征
    time_feats = build_time_features(idx_seg)
    delta_arr = _delta_hours(idx_seg)

    # 确定预测策略
    skip_style = str(skip_style).strip().lower()
    skip_days = 1 if skip_style in {"gap", "stitch"} else 0
    span = seg_len * (skip_days + 1)
    horizon_total = span if skip_style != "stitch" else seg_len

    results: List[_WindowResult] = []
    
    for day in eval_days:
        # 计算时段起点位置
        start_ts = _segment_start_ts(day, seg_start_h)
        pos = int(idx_seg.searchsorted(start_ts))
        if pos >= len(idx_seg) or idx_seg[pos] != start_ts:
            if emit_warnings:
                print(f"跳过评估日 {day.date()}：缺少该段起点 {start_ts}", file=sys.stderr)
            continue
        
        # 计算历史上下文起始位置
        i = pos - skip_days * seg_len
        if i < 0:
            if emit_warnings:
                print(f"跳过评估日 {day.date()}：该段历史不足（需要回看 {skip_days} 天）", file=sys.stderr)
            continue
        if i + span > len(y_seg):
            if emit_warnings:
                print(f"跳过评估日 {day.date()}：该段窗口越界", file=sys.stderr)
            continue

        # 提取历史上下文（用于模型输入）
        context = y_seg[:i]
        if context.size == 0 or not np.isfinite(context).all():
            if emit_warnings:
                print(f"跳过评估日 {day.date()}：该段目标历史存在缺失", file=sys.stderr)
            continue
        
        # 构建动态协变量
        dyn_cov: Dict[str, list[np.ndarray]] = {}
        
        if skip_style == "stitch":
            # Stitch模式：将目标日协变量拼接到历史后
            start = skip_days * seg_len
            future_index = idx_seg[i + start : i + start + seg_len]
            stitched_index = idx_seg[:i].append(future_index)
            stitched_time_feats = build_time_features(stitched_index)
            stitched_delta = _delta_hours(stitched_index)

            for name, arr in stitched_time_feats.items():
                dyn_cov[name] = [arr]
            dyn_cov["delta_hours"] = [stitched_delta]
            for name, hist_arr in hist_exog.items():
                pred_arr = pred_exog[name]
                dyn_cov[name] = [
                    np.concatenate(
                        [hist_arr[:i], pred_arr[i + start : i + start + seg_len]]
                    ).astype(np.float32)
                ]
        else:
            # Normal/Gap模式
            end_cov = i + horizon_total
            for name, arr in time_feats.items():
                dyn_cov[name] = [arr[:end_cov]]
            dyn_cov["delta_hours"] = [delta_arr[:end_cov]]
            for name, hist_arr in hist_exog.items():
                pred_arr = pred_exog[name]
                dyn_cov[name] = [
                    np.concatenate(
                        [hist_arr[:i], pred_arr[i : i + horizon_total]]
                    ).astype(np.float32)
                ]

        # 调用TimesFM模型进行预测
        pf_xreg, _ = model.forecast_with_covariates(
            inputs=[context],  # 历史电价序列
            dynamic_numerical_covariates=dyn_cov,  # 动态协变量
            dynamic_categorical_covariates=None,
            static_numerical_covariates=None,
            static_categorical_covariates=None,
            xreg_mode="xreg + timesfm",  # 外生变量 + TimesFM
            normalize_xreg_target_per_input=True,
            ridge=1e-2,
            max_rows_per_col=0,
            force_on_cpu=False,
        )
        
        # 处理预测结果
        y_hat = np.asarray(pf_xreg[0]).reshape(-1)
        if y_hat.size > horizon_total:
            y_hat = y_hat[-horizon_total:]
        elif y_hat.size < horizon_total:
            pad_val = y_hat[-1] if y_hat.size else 0.0
            y_hat = np.pad(
                y_hat,
                (0, horizon_total - y_hat.size),
                constant_values=pad_val,
            )

        # 如果是gap模式，取后一段作为预测结果
        if skip_days > 0 and skip_style != "stitch":
            start = skip_days * seg_len
            end = start + seg_len
            y_hat = y_hat[start:end]

        # 应用电价下限约束
        if target_key in {"dayahead", "realtime"}:
            y_hat = np.maximum(y_hat, PRICE_FLOOR_DA_RT)

        # 提取真实值（评估时使用）
        seg_ts = idx_seg[pos : pos + seg_len]
        if len(seg_ts) != seg_len:
            if emit_warnings:
                print(f"跳过评估日 {day.date()}：该段数据不完整", file=sys.stderr)
            continue
        y_true = df_seg.loc[seg_ts, target_col].astype(np.float32).to_numpy()
        
        results.append(_WindowResult(
            day=pd.Timestamp(day).normalize(),
            ts=seg_ts,
            y_true=y_true,
            y_pred=y_hat.astype(np.float32)
        ))

    return results


def _stitch_day_from_segments(
    day: pd.Timestamp,
    *,
    segments: List[tuple[int, int]],
    seg_results_by_segment: List[List[_WindowResult]],
) -> _WindowResult | None:
    """
    将各时段预测结果拼接成完整的24小时
    
    分时段预测后，需要将各段结果拼接成完整的一天。
    
    Args:
        day: 业务日期
        segments: 时段列表
        seg_results_by_segment: 各时段的预测结果
        
    Returns:
        拼接后的完整日预测结果，失败返回None
    """
    full_ts = _timestamps_for_trading_day(day)
    y_true_full = np.full((24,), np.nan, dtype=np.float32)
    y_pred_full = np.full((24,), np.nan, dtype=np.float32)

    # 遍历各时段，填充到完整数组
    for (seg_start, seg_end), results in zip(segments, seg_results_by_segment):
        seg_len = seg_end - seg_start + 1
        by_day = {r.day: r for r in results}
        r = by_day.get(pd.Timestamp(day).normalize())
        if r is None:
            return None
        offset = seg_start - 1
        if len(r.y_pred) != seg_len or len(r.y_true) != seg_len:
            return None
        y_true_full[offset : offset + seg_len] = r.y_true
        y_pred_full[offset : offset + seg_len] = r.y_pred

    # 检查完整性
    if np.isnan(y_true_full).any() or np.isnan(y_pred_full).any():
        return None
    
    return _WindowResult(
        day=pd.Timestamp(day).normalize(),
        ts=full_ts,
        y_true=y_true_full,
        y_pred=y_pred_full
    )


def _stitch_pred_day_from_segments(
    day: pd.Timestamp,
    *,
    segments: List[tuple[int, int]],
    seg_results_by_segment: List[np.ndarray],
) -> np.ndarray | None:
    """
    将各时段预测值拼接成完整的24小时（纯预测模式）
    
    与_stitch_day_from_segments类似，但只返回预测值（不包含真实值）。
    用于forecast模式（预测未来，无真实值）。
    
    Args:
        day: 业务日期
        segments: 时段列表
        seg_results_by_segment: 各时段的预测值数组
        
    Returns:
        拼接后的24小时预测值，失败返回None
    """
    y_pred_full = np.full((24,), np.nan, dtype=np.float32)
    
    for (seg_start, seg_end), y_pred in zip(segments, seg_results_by_segment):
        seg_len = seg_end - seg_start + 1
        if y_pred.shape[0] != seg_len:
            return None
        offset = seg_start - 1
        y_pred_full[offset : offset + seg_len] = y_pred
    
    if np.isnan(y_pred_full).any():
        return None
    
    return y_pred_full


# =============================================================================
# 外生变量（协变量）处理
# =============================================================================

def _strip_value_suffix(col: str) -> tuple[str, str]:
    """
    去除列名后缀，识别变量类型
    
    电力市场数据列名通常有后缀：
    - "实际值"：实际发生的值
    - "预测值"：预测的值
    - 无后缀：原始值
    
    Args:
        col: 列名
        
    Returns:
        (基础列名, 类型)
        
    示例:
        >>> _strip_value_suffix("直调负荷预测值")
        ("直调负荷", "pred")
        >>> _strip_value_suffix("直调负荷实际值")
        ("直调负荷", "actual")
    """
    if col.endswith("实际值"):
        return col[: -len("实际值")], "actual"
    if col.endswith("预测值"):
        return col[: -len("预测值")], "pred"
    return col, "raw"


def build_exog_sources_single(
    df: pd.DataFrame,
    target_col: str,
    target_key: str,
    *,
    exog_mode: str = "pred",
) -> tuple[Dict[str, np.ndarray], Dict[str, np.ndarray]]:
    """
    构建外生变量（协变量）来源
    
    这是TimesFM的核心特色：使用外生变量（协变量）而非传统特征工程。
    外生变量包括负荷、风电、光伏等，作为时间序列输入模型。
    
    两种模式：
    - "pred"：历史和预测都用预测值（实际部署时用）
    - "actual"：历史用实际值，预测用预测值（评估时用）
    
    Args:
        df: 数据DataFrame
        target_col: 目标列名（排除）
        target_key: 目标类型
        exog_mode: 外生变量模式（"pred"/"actual"）
        
    Returns:
        (历史外生变量字典, 预测外生变量字典)
        
    示例:
        >>> hist_exog, pred_exog = build_exog_sources_single(df, "日前电价", "dayahead", exog_mode="pred")
        >>> hist_exog.keys()
        dict_keys(['直调负荷', '风电', '光伏', '联络线'])
    """
    # 排除目标列和其他目标类型的列
    exclude_cols = {target_col}
    for key, cfg in TARGET_CFG.items():
        if key == target_key:
            continue
        try:
            col = _find_column(df, cfg["keywords"])
            exclude_cols.add(col)
        except ValueError:
            continue

    for col in df.columns:
        if any(tag in col for tag in TARGET_CFG[target_key]["exclude"]):
            exclude_cols.add(col)

    # 筛选数值列
    numeric_cols = [
        c
        for c in df.columns
        if c not in exclude_cols and np.issubdtype(df[c].dtype, np.number)
    ]

    # 按基础名称分组
    grouped: Dict[str, Dict[str, str]] = {}
    for col in numeric_cols:
        base, kind = _strip_value_suffix(col)
        grouped.setdefault(base, {})[kind] = col

    # 构建外生变量
    hist_exog: Dict[str, np.ndarray] = {}
    pred_exog: Dict[str, np.ndarray] = {}
    
    for base, kinds in grouped.items():
        if exog_mode == "pred":
            # 都用预测值（或实际值，或原始值）
            use_col = kinds.get("pred") or kinds.get("actual") or kinds.get("raw")
            if not use_col:
                continue
            hist_arr = (
                df[use_col].astype(np.float32).ffill().bfill().fillna(0.0).to_numpy()
            )
            pred_arr = hist_arr
        elif exog_mode == "actual":
            # 历史用实际值，预测用预测值
            hist_col = kinds.get("actual") or kinds.get("raw") or kinds.get("pred")
            pred_col = kinds.get("pred") or kinds.get("raw")
            if not hist_col or not pred_col:
                continue
            hist_arr = df[hist_col].astype(np.float32).ffill().bfill().fillna(0.0).to_numpy()
            pred_arr = df[pred_col].astype(np.float32).ffill().bfill().fillna(0.0).to_numpy()
        else:
            raise ValueError(f"未知 exog_mode: {exog_mode}（可选：pred/actual）")

        hist_exog[base] = hist_arr
        pred_exog[base] = pred_arr

    return hist_exog, pred_exog


# =============================================================================
# 工具函数
# =============================================================================

def _parse_date_range(s: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """
    解析日期范围字符串
    
    支持多种格式：
    - "2026-01-01-2026-01-31"
    - "2026-01-01~2026-01-31"
    - "2026-01-01至2026-01-31"
    
    Args:
        s: 日期范围字符串
        
    Returns:
        (开始日期, 结束日期)
    """
    s = str(s).strip()
    if not s:
        raise ValueError("空的 --date-range")

    # 尝试匹配 "YYYY-MM-DD-YYYY-MM-DD" 格式
    m = re.match(
        r"^\s*(\d{4}[/-]\d{1,2}[/-]\d{1,2})\s*-\s*(\d{4}[/-]\d{1,2}[/-]\d{1,2})\s*$",
        s,
    )
    if m:
        start_s, end_s = m.group(1), m.group(2)
    else:
        # 尝试其他分隔符
        for sep in ("~", "至", "—", "–"):
            if sep in s:
                start_s, end_s = [p.strip() for p in s.split(sep, 1)]
                break
        else:
            if " - " in s:
                start_s, end_s = [p.strip() for p in s.split(" - ", 1)]
            else:
                raise ValueError(
                    "无法解析 --date-range；示例：2026/01/06-2026/01/25 或 2026-01-06 ~ 2026-01-25"
                )

    start = pd.to_datetime(start_s, errors="raise").normalize()
    end = pd.to_datetime(end_s, errors="raise").normalize()
    if end < start:
        raise ValueError("--date-range 结束日期早于开始日期")
    
    return start, end


def _import_timesfm():
    """
    导入TimesFM模块
    
    处理模块路径问题，支持从本地src目录导入。
    """
    try:
        import timesfm  # type: ignore
        return timesfm
    except ModuleNotFoundError:
        src_path = os.path.join(os.path.dirname(__file__), "src")
        if os.path.isdir(src_path) and src_path not in sys.path:
            sys.path.insert(0, src_path)
        import timesfm  # type: ignore
        return timesfm


def _slice_or_pad(arr: np.ndarray, start: int, length: int) -> np.ndarray:
    """
    切片或填充数组到指定长度
    
    Args:
        arr: 输入数组
        start: 起始位置
        length: 目标长度
        
    Returns:
        切片或填充后的数组
    """
    part = np.asarray(arr[start : start + length], dtype=np.float32)
    if part.size < length:
        pad_val = float(arr[-1]) if arr.size else 0.0
        part = np.pad(part, (0, length - part.size), constant_values=pad_val)
    return part


# =============================================================================
# 模型加载与预测
# =============================================================================

def _build_model():
    """
    构建并加载TimesFM模型
    
    在项目中的角色：
    - 加载Google的TimesFM-2.5-200M预训练模型
    - 支持本地缓存，避免重复下载
    - 配置模型参数（上下文长度、预测范围等）
    
    Returns:
        编译好的TimesFM模型实例
    """
    timesfm = _import_timesfm()
    import huggingface_hub as _hfhub
    from huggingface_hub import snapshot_download

    project_root = os.getenv("PROJECT_ROOT", ".")
    model_dir = Path(project_root) / "models" / "timesFM"

    # 首次运行：下载模型
    if not model_dir.exists() or not any(model_dir.iterdir()):
        print(f"首次运行，正在下载模型到 {model_dir.resolve()} ...", file=sys.stderr)
        model_dir.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            repo_id="google/timesfm-2.5-200m-pytorch",
            local_dir=str(model_dir),
        )
        print("模型下载完成。", file=sys.stderr)

    # 临时替换hf_hub_download函数，避免强制网络请求
    _tfm_mod = sys.modules.get("timesfm.timesfm_2p5.timesfm_2p5_torch")
    _orig = getattr(_tfm_mod, "hf_hub_download", None) if _tfm_mod else None

    def _local_only_download(repo_id, filename, **kwargs):
        if kwargs.get("force_download"):
            return None  # 跳过强制网络下载
        return _hfhub.hf_hub_download(repo_id, filename, **kwargs)

    if _tfm_mod and _orig is not None:
        _tfm_mod.hf_hub_download = _local_only_download

    try:
        model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(str(model_dir))
    finally:
        if _tfm_mod and _orig is not None:
            _tfm_mod.hf_hub_download = _orig
    
    # 编译模型配置
    model.compile(
        timesfm.ForecastConfig(
            max_context=16000,      # 最大上下文长度
            max_horizon=256,        # 最大预测范围
            normalize_inputs=True,  # 归一化输入
            use_continuous_quantile_head=True,
            force_flip_invariance=True,
            infer_is_positive=True,
            fix_quantile_crossing=True,
            return_backcast=True,
        )
    )
    return model


def _forecast_from_history_window(
    *,
    model,
    df: pd.DataFrame,
    target_col: str,
    target_key: str,
    forecast_day: pd.Timestamp,
    segments: List[tuple[int, int]],
    skip_style: str,
    exog_mode: str,
    emit_warnings: bool = False,
) -> np.ndarray | None:
    """
    从历史窗口预测指定日期（内部函数）
    
    Args:
        model: TimesFM模型
        df: 数据DataFrame
        target_col: 目标列名
        target_key: 目标类型
        forecast_day: 预测日期
        segments: 时段列表
        skip_style: 预测策略
        exog_mode: 外生变量模式
        emit_warnings: 是否输出警告
        
    Returns:
        24小时预测值数组，失败返回None
    """
    seg_results_by_segment: List[List[_WindowResult]] = []
    
    for seg in segments:
        seg_results = _predict_segment_windows(
            model=model,
            df=df,
            target_col=target_col,
            target_key=target_key,
            segment=seg,
            eval_days=[pd.Timestamp(forecast_day).normalize()],
            skip_style=skip_style,
            exog_mode=exog_mode,
            emit_warnings=emit_warnings,
        )
        seg_results_by_segment.append(seg_results)

    # 拼接各时段结果
    stitched = _stitch_day_from_segments(
        pd.Timestamp(forecast_day).normalize(),
        segments=segments,
        seg_results_by_segment=seg_results_by_segment,
    )
    
    if stitched is None:
        return None
    
    return stitched.y_pred.astype(np.float32)


# =============================================================================
# 主预测函数
# =============================================================================

def forecast_next_day(args: argparse.Namespace) -> pd.DataFrame:
    """
    预测未来一天的电价（forecast模式）
    
    在项目中的角色：
    - 实际陪跑/部署时使用
    - 预测指定日期的24小时电价
    - 被infer.py调用
    
    与backtest模式的区别：
    - forecast：预测未来，无真实值，不计算指标
    - backtest：历史回测，有真实值，计算指标
    
    Args:
        args: 命令行参数
        
    Returns:
        预测结果DataFrame（时刻, 预测值）
    """
    # 加载数据（保留未来行的目标列为NaN）
    df = load_features(args.data, encoding=args.encoding, sheet_name=args.sheet)
    target_col = _find_column(df, TARGET_CFG[args.target]["keywords"])

    # 加载模型
    model = _build_model()

    # 构建时段分段
    segment_count = int(getattr(args, "segment_count", 1))
    segments = _build_segments(segment_count)
    
    skip_style = str(getattr(args, "skip_style", "gap")).strip().lower()
    horizon = int(getattr(args, "horizon", 24))
    
    if horizon != 24 and (segment_count != 1 or getattr(args, "dump_csv", False)):
        raise ValueError("分时段/导出模式下仅支持 --horizon 24（一天 24 点）")

    forecast_day = pd.to_datetime(args.forecast_date, errors="raise").normalize()
    exog_mode = str(getattr(args, "exog_mode", "pred"))

    # 验证预测日期是否完整
    complete_days = set(_complete_trading_days(df.index))
    if forecast_day not in complete_days:
        raise ValueError(
            f"forecast-date={forecast_day.date()} 不是完整交易日（按 01:00~次日00:00 定义），"
            "且 forecast 模式不做外推。"
        )

    # 分时段预测
    seg_preds_by_segment: List[np.ndarray] = []
    missing_segments: List[str] = []
    
    for seg_start_h, seg_end_h in segments:
        seg_results = _predict_segment_windows(
            model=model,
            df=df,
            target_col=target_col,
            target_key=args.target,
            segment=(seg_start_h, seg_end_h),
            eval_days=[forecast_day],
            skip_style=skip_style,
            exog_mode=exog_mode,
            emit_warnings=False,
        )
        if not seg_results:
            missing_segments.append(f"{seg_start_h:02d}-{seg_end_h:02d}")
            continue
        seg_preds_by_segment.append(seg_results[0].y_pred.astype(np.float32))

    if missing_segments:
        seg_msg = "、".join(missing_segments)
        raise ValueError(
            f"forecast-date={forecast_day.date()} 无法构造完整窗口（缺失分段: {seg_msg}）。"
            "forecast 模式不做外推。"
        )

    # 拼接完整日预测
    y_pred_full = _stitch_pred_day_from_segments(
        forecast_day,
        segments=segments,
        seg_results_by_segment=seg_preds_by_segment,
    )
    
    if y_pred_full is None:
        raise ValueError(
            f"forecast-date={forecast_day.date()} 无法拼接完整 24 点预测结果。"
            "forecast 模式不做外推。"
        )

    # 返回结果DataFrame
    return pd.DataFrame({
        "时刻": _timestamps_for_trading_day(forecast_day),
        "预测值": y_pred_full
    })


def forecast(args: argparse.Namespace) -> dict:
    """
    历史回测评估（backtest模式）
    
    在项目中的角色：
    - 评估模型在历史数据上的表现
    - 用于模型选择、参数调优
    - 计算MAE、sMAPE等评估指标
    
    与forecast_next_day的区别：
    - 评估多天（而非单天）
    - 有真实值，可以计算指标
    - 支持导出结果到CSV
    
    Args:
        args: 命令行参数
        
    Returns:
        评估指标字典
    """
    # 加载模型和数据
    model = _build_model()
    df, target_col = load_dataset(
        args.data, args.target, encoding=args.encoding, sheet_name=args.sheet
    )

    # 构建时段分段
    segment_count = int(getattr(args, "segment_count", 1))
    segments = _build_segments(segment_count)

    skip_style = str(getattr(args, "skip_style", "gap")).strip().lower()
    horizon = int(getattr(args, "horizon", 24))
    
    if horizon != 24 and (segment_count != 1 or getattr(args, "dump_csv", False)):
        raise ValueError("分时段/导出模式下仅支持 --horizon 24（一天 24 点）")

    # 选择评估日期
    eval_days = _select_eval_days(
        df.index,
        eval_days=int(getattr(args, "eval_days", 30)),
        date_range=getattr(args, "date_range", None),
    )
    if not eval_days:
        raise ValueError("没有可评估的交易日（请检查数据完整性或 --date-range）")

    # 分时段预测所有评估日
    seg_results_by_segment: List[List[_WindowResult]] = []
    for seg in segments:
        seg_results = _predict_segment_windows(
            model=model,
            df=df,
            target_col=target_col,
            target_key=args.target,
            segment=seg,
            eval_days=eval_days,
            skip_style=skip_style,
            exog_mode=str(getattr(args, "exog_mode", "pred")),
        )
        seg_results_by_segment.append(seg_results)

    # 拼接各天的完整预测
    stitched_days: List[_WindowResult] = []
    for day in eval_days:
        stitched = _stitch_day_from_segments(
            day,
            segments=segments,
            seg_results_by_segment=seg_results_by_segment,
        )
        if stitched is None:
            print(f"跳过评估日 {day.date()}：无法拼接完整 24 点", file=sys.stderr)
            continue
        stitched_days.append(stitched)

    if not stitched_days:
        raise ValueError("没有可评估的完整交易日窗口")

    # 计算总体指标
    y_true_all = np.concatenate([w.y_true for w in stitched_days])
    y_pred_all = np.concatenate([w.y_pred for w in stitched_days])
    metrics = compute_metrics(y_true_all, y_pred_all, args.target)

    # 计算各分段指标（可选）
    if getattr(args, "metrics_by_segment", False):
        for (seg_start, seg_end), seg_results in zip(segments, seg_results_by_segment):
            if not seg_results:
                continue
            y_true_seg = np.concatenate([r.y_true for r in seg_results])
            y_pred_seg = np.concatenate([r.y_pred for r in seg_results])
            seg_metrics = compute_metrics(y_true_seg, y_pred_seg, args.target)
            prefix = f"[{seg_start:02d}-{seg_end:02d}] "
            for k, v in seg_metrics.items():
                metrics[prefix + k] = v

    # 导出结果到CSV（可选）
    if getattr(args, "dump_csv", False):
        PROJECT_ROOT = os.getenv("PROJECT_ROOT", ".")
        out_path = Path(PROJECT_ROOT) / "output"
        os.makedirs(out_path, exist_ok=True)
        out_path = out_path / f"backtest_{args.target}.csv"
        
        rows = []
        for w in stitched_days:
            for ts, yt, yp in zip(w.ts, w.y_true, w.y_pred):
                rows.append({
                    "时刻": pd.Timestamp(ts),
                    "真实值": float(yt),
                    "预测值": float(yp)
                })
        pd.DataFrame(rows).to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"已导出: {out_path}", file=sys.stderr)

    return metrics


# =============================================================================
# 命令行参数解析
# =============================================================================

def parse_args() -> argparse.Namespace:
    """
    解析命令行参数
    
    支持的参数：
    - --mode: 运行模式（backtest/forecast）
    - --data: 数据文件路径
    - --target: 预测目标（日前/实时/价差）
    - --segment-count: 分时段数量
    - --forecast-date: 预测日期（forecast模式）
    - 等等
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["backtest", "forecast"],
        default="backtest",
        help="运行模式：backtest=回测评估；forecast=按指定日期预测",
    )
    
    PROJECT_ROOT = os.getenv("PROJECT_ROOT", ".")
    DATA_SET_NAME = os.getenv("DATA_SET_NAME", "electricity_prices")
    parser.add_argument(
        "--data",
        default=Path(os.path.join(PROJECT_ROOT, DATA_SET_NAME)),
        help="数据文件路径（Excel 或 CSV）",
    )
    parser.add_argument("--encoding", default=None, help="CSV 编码")
    parser.add_argument("--sheet", default=0, help="Excel sheet 名称或索引（默认 0）")
    parser.add_argument(
        "--target",
        default="日前",
        help="预测目标：日前 / 实时 / 价差",
    )
    parser.add_argument("--horizon", type=int, default=24, help="预测范围（小时）")
    parser.add_argument("--eval-days", type=int, default=30, help="评估天数（backtest模式）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（默认 42）")
    parser.add_argument(
        "--deterministic",
        dest="deterministic",
        action="store_true",
        default=True,
        help="启用确定性算法（默认开启）",
    )
    parser.add_argument(
        "--no-deterministic",
        dest="deterministic",
        action="store_false",
        help="关闭确定性算法",
    )
    parser.add_argument(
        "--segment-count",
        type=int,
        default=3,
        help="分时段数量：把一天 24 点等分为 N 段分别预测再拼接（默认 3）"
    )
    parser.add_argument(
        "--exog-mode",
        choices=["pred", "actual"],
        default="pred",
        help="协变量取值策略：pred=都用预测值；actual=历史用实际值",
    )
    parser.add_argument(
        "--date-range",
        dest="date_range",
        default=None,
        help="评估日期范围，如 2026-01-01~2026-01-31"
    )
    parser.add_argument(
        "--skip-style",
        choices=["normal", "gap", "stitch"],
        default=None,
        help="预测策略：normal=直接预测；gap=先预测前一天；stitch=拼接协变量",
    )
    parser.add_argument(
        "--forecast-date",
        default=None,
        help="forecast 模式下必填：指定预测交易日，格式 YYYY/MM/DD",
    )
    parser.add_argument(
        "--dump-csv",
        action="store_true",
        help="导出 CSV 到 output 目录",
    )
    parser.add_argument(
        "--metrics-by-segment",
        action="store_true",
        help="额外输出各分段的指标"
    )
    
    args = parser.parse_args()
    
    # 处理目标类型别名
    target_raw = args.target.strip()
    target_key = TARGET_ALIASES.get(target_raw) or TARGET_ALIASES.get(target_raw.lower())
    if not target_key:
        raise ValueError(f"未知目标: {args.target}")
    args.target = target_key
    
    # 默认skip_style：实时用gap，日前用normal
    if args.skip_style is None:
        args.skip_style = "gap" if args.target == "realtime" else "normal"
    
    # 处理sheet参数
    if isinstance(args.sheet, str) and args.sheet.strip().isdigit():
        args.sheet = int(args.sheet.strip())
    
    # forecast模式必须指定日期
    if args.mode == "forecast" and not args.forecast_date:
        raise ValueError("forecast 模式必须传 --forecast-date（例如 2026/01/25）")
    
    return args


# =============================================================================
# 主程序入口
# =============================================================================

def main():
    """
    主程序入口
    
    两种运行模式：
    1. backtest模式：历史回测评估
       python price_forecast_copy_分时段预测.py --mode backtest --target 日前
       
    2. forecast模式：预测未来一天
       python price_forecast_copy_分时段预测.py --mode forecast --target 日前 --forecast-date 2026/02/07
    """
    args = parse_args()
    
    # 设置可复现性
    set_reproducibility(int(args.seed), bool(args.deterministic))
    print(
        f"reproducibility: seed={args.seed}, deterministic={args.deterministic}",
        file=sys.stderr,
    )
    
    if args.mode == "forecast":
        # 预测模式：预测未来一天
        pred_df = forecast_next_day(args)
        
        if getattr(args, "dump_csv", False):
            # 导出到CSV
            os.makedirs("output", exist_ok=True)
            out_path = os.path.join("output", f"forecast_{args.target}.csv")
            pred_df.to_csv(out_path, index=False, encoding="utf-8-sig")
            print(f"已导出: {out_path}", file=sys.stderr)
        else:
            # 直接输出到控制台
            print(pred_df.to_string(index=False))
        
        return pred_df

    else:
        # 回测模式：评估历史表现
        metrics = forecast(args)
        for k, v in metrics.items():
            print(f"{k}: {v:.4f}")


if __name__ == "__main__":
    main()
    # 使用示例：
    # --target 实时 --mode forecast --dump-csv --forecast-date 2026/02/07
