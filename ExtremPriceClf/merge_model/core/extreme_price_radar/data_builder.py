# ^_^
"""
模块：data_builder.py
职责：数据重构与展平。负责严格的时间窗口对齐，防止未来数据泄露，并生成二分类标签。
"""

import logging
import pandas as pd
import numpy as np
from typing import Tuple, List

# 配置模块日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DataTabularizer:
    """
    极端负电价雷达 - 数据重构类 (Data Tabularizer)
    负责处理 D-1/D-0/D+1 的复杂时间截断与错位对齐，将时序数据转换为 (Samples, Features) 表格。
    """

    def __init__(self, target_col: str = '实时电价', extreme_threshold: float = -50.0):
        """
        初始化数据重构类。

        Args:
            target_col (str): 需要预测的目标列名（默认为实时电价）。
            extreme_threshold (float): 触发极端异常分类的电价阈值（默认 -70）。
        """
        self.target_col = target_col
        self.features_col = None
        self.stage2_features_col = None
        self.extreme_threshold = extreme_threshold
        logger.info(f"DataTabularizer 初始化完成。目标列: {self.target_col}, 极值阈值: {self.extreme_threshold}")

    def drop_actual_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        丢弃实际值特征和日前电价列和实时电价列，防止数据穿越。 确保每行数据都只有在预测时能拿到的特征列
        Args:
            df (pd.DataFrame): 经过特征工程后的数据集。
        Returns:
            pd.DataFrame: 丢弃实际值特征的特征集。
        """
        # logger.info("丢弃实际值特征...")

        # 复制数据以防修改原表
        aligned_df = df.copy()

        # 分离特征名
        all_cols = aligned_df.columns.tolist()
        actual_cols = [col for col in all_cols if col.endswith('实际值')]
        cols_to_drop = actual_cols
        if "日前电价" in all_cols:
            cols_to_drop.append("日前电价")
        if "实时电价" in all_cols:
            cols_to_drop.append("实时电价")
        cols_to_drop += ["地方电厂总加预测值", "核电总加预测值", "自备机组总加预测值", "试验机组总加预测值"]
        aligned_df.drop(columns=cols_to_drop, inplace=True, errors='ignore')
        # 阶段1特征中不包含概率特征，避免信息泄露
        exclude_list = ['p1_prob_OOF']
        aligned_df.drop(columns=[col for col in df.columns if col in exclude_list], inplace=True)
        return aligned_df

    def create_training_dataset(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
        """
        创建适用于 LightGBM 训练的 2D 表格特征 X 和分类标签 y。
        Args:
            df (pd.DataFrame): 经过特征工程处理后的 DataFrame。
        Returns:
            Tuple[pd.DataFrame, pd.Series]:
                - X: 特征矩阵 (Samples, Features)
                - y: 二分类标签 (0 或 1)
        """
        # logger.info(f"--- 启动数据集构建，原始行数: {len(df)} ---")

        # 1. 检查目标列是否存在
        if self.target_col not in df.columns:
            raise ValueError(f"目标列 '{self.target_col}' 不在数据集中！")

        # 2. 生成标签 (Label)
        # 如果真实电价小于极值阈值 (-70)，则为 1，否则为 0
        labels = (df[self.target_col] < self.extreme_threshold).astype(int)

        # 3. 丢弃实际值特征
        aligned_df = self.drop_actual_features(df)
        # 把标签挂载回对齐后的 DataFrame
        aligned_df['label'] = labels
        # 4. 分离 X 和 y
        # 剔除时间戳和原始的目标列（防止未来目标值泄露到特征中）
        cols_to_exclude = ['时刻', self.target_col, 'label']
        # 确保特征列中没有辅助列（时间），目标列和目标列标签
        feature_cols = [c for c in aligned_df.columns if c not in cols_to_exclude]
        self.features_col = feature_cols
        X = aligned_df[feature_cols]
        y = aligned_df['label']

        # logger.info(f"--- 数据集构建完成 ---")
        # logger.info(f"特征矩阵 X 形状: {X.shape}, 特征数量: {len(feature_cols)}")
        # logger.info(f"标签正样本 (<{self.extreme_threshold}) 数量: {y.sum()} / {len(y)} ({y.mean():.2%})")

        return X, y



