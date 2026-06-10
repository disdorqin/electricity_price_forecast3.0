# ^_^
"""
模块：features.py
职责：特征工程引擎。负责时序特征提取、爬坡率计算及极值水位指标的计算。
注意：本类只做纯粹的特征变换，不涉及数据截断和标签生成。
"""

import logging
import pandas as pd
import numpy as np
import os
import warnings
from chinese_calendar import is_holiday
from borax.calendars import LunarDate
import pandas as pd
import datetime
import numpy as np
from pathlib import Path
from dotenv import load_dotenv

# 配置模块日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_term_start_name(date_obj):
    """获取节气名称"""
    lunar = LunarDate.from_solar_date(date_obj.year, date_obj.month, date_obj.day)
    return lunar.term


def find_initial_term(start_date):
    """回溯查找最近的节气"""
    for i in range(1, 25):
        target_date = start_date - datetime.timedelta(days=i)
        term = get_term_start_name(target_date)
        if term:
            return term
    return "未知节气"


def adjust_date_for_0am(dt):
    """调整0点时间"""
    if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
        return (dt - datetime.timedelta(days=1)).date()
    else:
        return dt.date()


class FeatureEngineer:
    """
    极端负电价雷达 - 特征工程类 (Feature Engineer)
    基于向量化操作进行时间序列的特征扩充。
    """

    def __init__(self, time_col: str = '时刻'):
        """
        初始化特征工程类。

        Args:
            time_col (str): DataFrame 中表示时间的列名。
        """
        self.time_col = time_col
        logger.info("FeatureEngineer 初始化完成。")

    def build_hour_context(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        增加小时特征
        Args:
            df (pd.DataFrame): 原始数据集。

        Returns:
            pd.DataFrame: 增加小时特征后的数据集。
        """
        # logger.info("开始提取时序截面特征 (Time Context)...")
        # 确保时间列为 datetime 类型
        if not pd.api.types.is_datetime64_any_dtype(df[self.time_col]):
            df[self.time_col] = pd.to_datetime(df[self.time_col])

        # 提取基础时序特征
        df['hour'] = df[self.time_col].dt.hour
        # 可选增强：对连续的周期性特征进行三角函数编码（消除 23点 和 0点 的数值断层）
        df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24)
        df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24)

        return df

    def process_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """处理特征工程"""
        self.time_col = "时刻"
        if self.time_col not in df.columns:
            print(f"错误：列名 '{self.time_col}' 不在数据中")
            return None

        df[self.time_col] = pd.to_datetime(df[self.time_col])
        df['TempDate'] = df[self.time_col].apply(adjust_date_for_0am)
        unique_dates = sorted(df['TempDate'].unique())
        current_term = get_term_start_name(unique_dates[0])
        if current_term is None:
            current_term = find_initial_term(unique_dates[0])
        date_features_map = {}

        for d in unique_dates:
            term_on_day = get_term_start_name(d)
            if term_on_day:
                current_term = term_on_day
            check_holiday = is_holiday(d)
            weekday = d.weekday()

            date_features_map[d] = {
                'TempDate': d,
                '星期': weekday,
                '节气名称': current_term,
                '是否法定或周末休息': 1 if check_holiday else 0
            }

        feature_df = pd.DataFrame(list(date_features_map.values()))
        df_final = pd.merge(df, feature_df, on='TempDate', how='left')
        df_final['星期_sin'] = np.sin(2 * np.pi * df_final['星期'] / 7)
        df_final['星期_cos'] = np.cos(2 * np.pi * df_final['星期'] / 7)
        df_final["其他负荷总加实际值"] = df_final["地方电厂总加实际值"] + df_final["核电总加实际值"] + df_final["自备机组总加实际值"] + df_final["试验机组总加实际值"]
        df_final["其他负荷总加预测值"] = df_final["地方电厂总加预测值"] + df_final["核电总加预测值"] + df_final["自备机组总加预测值"] + df_final["试验机组总加预测值"]
        df_final["总用电量实际值"] = df_final["直调负荷实际值"] + df_final["联络线受电负荷实际值"] + df_final["新能源总加实际值"] + df_final["其他负荷总加实际值"]
        df_final["总用电量预测值"] = df_final["直调负荷预测值"] + df_final["联络线受电负荷预测值"] + df_final["新能源总加预测值"] + df_final["其他负荷总加预测值"]
        del df_final['TempDate']
        del df_final["星期"]

        # 新增特征
        df_final["净负荷实际值"] = df_final["直调负荷实际值"] - df_final["新能源总加实际值"]
        df_final["净负荷预测值"] = df_final["直调负荷预测值"] - df_final["新能源总加预测值"]
        df_final["新能源渗透率实际值"] = df_final["新能源总加实际值"] / (df_final["直调负荷实际值"] + 1e-5)
        df_final["新能源渗透率预测值"] = df_final["新能源总加预测值"] / (df_final["直调负荷预测值"] + 1e-5)
        df_final["空间_新能源比实际值"] = df_final["竞价空间实际值"] / (df_final["新能源总加实际值"] + 1.0)
        df_final["空间_新能源比预测值"] = df_final["竞价空间预测值"] / (df_final["新能源总加预测值"] + 1.0)
        return df_final

    def feature_engineer_solar_terms(self, df: pd.DataFrame) -> pd.DataFrame:
        """基于节气构造特征"""
        solar_terms_order = [
            '立春', '雨水', '惊蛰', '春分', '清明', '谷雨',
            '立夏', '小满', '芒种', '夏至', '小暑', '大暑',
            '立秋', '处暑', '白露', '秋分', '寒露', '霜降',
            '立冬', '小雪', '大雪', '冬至', '小寒', '大寒'
        ]
        solar_map = {term: i + 1 for i, term in enumerate(solar_terms_order)}
        if '节气名称' not in df.columns:
            raise ValueError("输入数据缺少 '节气名称' 列")
        df = df.copy()
        df['solar_term_ordinal'] = df['节气名称'].map(solar_map)
        unmapped_rows = df['solar_term_ordinal'].isna().sum()
        if unmapped_rows > 0:
            print(f"警告: 发现 {unmapped_rows} 行无法识别的节气名称")
        df['节气_sin'] = np.sin(2 * np.pi * (df['solar_term_ordinal'] - 1) / 24)
        df['节气_cos'] = np.cos(2 * np.pi * (df['solar_term_ordinal'] - 1) / 24)
        del df['solar_term_ordinal']
        del df["节气名称"]
        return df

    def add_lag_features(self, df: pd.DataFrame, cols, lags) -> pd.DataFrame:
        """
        构造滞后特征

        参数：
        df : DataFrame
        cols : list，需要构造滞后特征的列名
        lags : list 或 range，滞后步长（单位：小时）

        返回：
        df_new : 新增滞后特征后的DataFrame
        """
        df = df.copy()

        # 确保时间排序（非常关键）
        if "时刻" not in df.columns:
            raise ValueError("缺少 '时刻' 列")
        df = df.sort_values("时刻").reset_index(drop=True)

        # 构造滞后特征
        for col in cols:
            if col not in df.columns:
                print(f"警告: 列 {col} 不存在，已跳过")
                continue

            for lag in lags:
                df[f"{col}_lag_{lag}"] = df[col].shift(lag)

        return df

    def add_ramp_features(self, df: pd.DataFrame, cols, suffix="_ramp") -> pd.DataFrame:
        """
        构造波动/爬坡特征 (一阶差分: Value_t - Value_t-1)

        业务含义：
        1. 负荷爬坡 (Load Ramp): load_t - load_t-1
           - 正值表示负荷上升，可能需要启机，推高价格。
           - 负值表示负荷下降。
        2. 新能源波动 (Renewable Ramp): renewable_t - renewable_t-1
           - 负值表示新能源出力骤降，缺口需火电填补，推高价格。
           - 正值表示新能源出力增加，挤压火电空间。

        参数：
        df : DataFrame, 输入数据
        cols : list, 需要构造波动特征的列名列表 (如 ["直调负荷实际值", "新能源总加实际值"])
        suffix : str, 生成新列的后缀名，默认为 "_ramp"
        """
        df = df.copy()
        # 确保时间排序（构造时序差分的前提）
        if "时刻" not in df.columns:
            raise ValueError("缺少 '时刻' 列，无法计算时序波动特征")
        # 按时间升序排列，防止数据乱序导致计算错误
        df = df.sort_values("时刻").reset_index(drop=True)
        for col in cols:
            if col not in df.columns:
                print(f"警告: 列 '{col}' 不存在，已跳过波动特征构造。")
                continue

            # 核心逻辑：当前值 - 上一小时值
            # pandas.diff(1) 等价于 shift(1) 后相减
            ramp_col_name = f"{col}{suffix}"
            df[ramp_col_name] = df[col].diff(1)
            # 相对变化率 (变化量 / 上一时刻值)，避免除以0
            prev_val = df[col].shift(1)
            df[f"{col}_pct_ramp"] = df[ramp_col_name] / (prev_val + 1e-5)
        return df

    def _build_extremum_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        对竞价空间预测值进行时序处理
        计算极值水位特征。
        评估当前指标在其历史分布中的位置（是否处于极度逼仄状态）。

        Args:
            df (pd.DataFrame): 数据集。

        Returns:
            pd.DataFrame: 增加极值水位特征后的数据集。
        """
        # logger.info("开始计算极值水位特征 (Extremum Indicators)...")

        if '竞价空间预测值' in df.columns:
            # 计算过去 7 天内，*同一小时*的竞价空间滑动平均值
            # 这是一个强力的向量化操作：按小时分组，然后利用 transform 进行滚动均值计算
            # shift(1) 确保滚动计算不包含当天的数据（严格遵守预测时序限制）
            df['竞价空间预测_同小时_7d_avg'] = (
                df.groupby('hour')['竞价空间预测值']
                .transform(lambda x: x.shift(1).rolling(window=7, min_periods=1).mean())
            )

            # 计算水位比率 (当前预测值 / 历史 7 天均值)
            # 添加 1e-5 作为平滑项，防止分母为 0
            df['竞价空间_水位_比率'] = df['竞价空间预测值'] / (df['竞价空间预测_同小时_7d_avg'] + 1e-5)
        del df["hour"]
        return df

    def _build_expert_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        引入交易员专家经验的显式特征
        """
        if '竞价空间预测值' in df.columns and 'hour' in df.columns:
            # 经验 1：竞价空间预测值为负的强指示器 (0 或 1)
            df['expert_空间为负'] = (df['竞价空间预测值'] < 0).astype(int)

            # 经验 2：中午时段（假设定义为 11点到 14点）且空间大于 23000
            is_noon = df['hour'].isin([11, 12, 13, 14])
            is_high_space = df['竞价空间预测值'] > 23000
            df['expert_中午高空间'] = (is_noon & is_high_space).astype(int)

        return df

    def process(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        特征工程主接口。对外暴露，一键加工。

        Args:
            df (pd.DataFrame): 原始输入数据。

        Returns:
            pd.DataFrame: 加工后的完整特征数据，保持与原输入相同的行数。
        """
        # logger.info(f"--- 启动特征工程流水线，输入数据形状: {df.shape} ---")

        # 为防止修改原始数据，进行深拷贝
        df_processed = df.copy()

        # 按照逻辑顺序执行私有方法
        df_processed = self.build_hour_context(df_processed)
        # df_processed = self._build_expert_features(df_processed)
        df_processed = self._build_extremum_indicators(df_processed)

        df_processed = self.process_features(df_processed)
        df_processed = self.feature_engineer_solar_terms(df_processed)
        df_processed["时刻"] = pd.to_datetime(df_processed["时刻"])
        lags_list = [24, 48, 24 * 7]
        df_processed = self.add_lag_features(df_processed, ["日前电价"], lags_list)
        df_processed = self.add_lag_features(df_processed, ["直调负荷实际值", "新能源总加实际值", "竞价空间实际值", "实时电价"], [48, 24 * 7])
        df_processed = self.add_lag_features(df_processed, ["竞价空间预测值"], [1, 2, 3, 4])
        df_processed = self.add_ramp_features(df_processed, ["直调负荷预测值", "新能源总加预测值", "竞价空间预测值"])
        # 构造滞后特征时导致前几行缺少数据，导致数据长度不一致，需要截取
        max_lag = max(lags_list)
        df_processed = df_processed.iloc[max_lag:].reset_index(drop=True)

        logger.info(f"--- 特征工程流水线完成，输出数据形状: {df_processed.shape} ---")
        return df_processed

