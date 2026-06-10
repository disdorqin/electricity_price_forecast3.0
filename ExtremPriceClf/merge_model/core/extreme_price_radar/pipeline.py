"""
模块：pipeline.py
职责：雷达流水线总控。对外暴露顶层 API，编排特征工程、数据重构、模型训练与高精度推理。
"""

import logging
import numpy as np
import pandas as pd
from typing import Tuple

# 假设其他模块存放在同一包下，这里进行导入
# 实际项目中如果遇到路径问题，请确保 __init__.py 配置正确
try:
    from .features import FeatureEngineer
    from .data_builder import DataTabularizer
    from .classifier import ExtremePriceClassifier
except ImportError:
    # 兼容单文件测试运行或不同运行路径
    pass

# 配置模块日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class RadarPipeline:
    """
    极端负电价雷达 - 流水线总控类 (Pipeline)
    负责将特征提取、数据时序对齐、模型训练与推理无缝串接。
    """

    def __init__(self,
                 target_col: str = '实时电价',
                 extreme_threshold: float = -70.0,
                 min_precision: float = 0.70):
        """
        初始化雷达流水线，实例化三大核心组件。

        Args:
            target_col (str): 目标预测列名。
            extreme_threshold (float): 触发极值警报的阈值。
            min_precision (float): 动态寻优时要求的最低精确率。
        """
        logger.info(">>> 正在初始化极端负电价雷达系统 (Extreme Price Radar) <<<")
        self.target_col = target_col
        self.extreme_threshold = extreme_threshold

        # 实例化子模块
        self.fe = FeatureEngineer(time_col='时刻')
        self.dt = DataTabularizer(target_col=self.target_col, extreme_threshold=self.extreme_threshold)
        self.clf1 = ExtremePriceClassifier(min_precision=min_precision)
        # 二阶段在整合模型中由独立模块负责，这里不再绑定二阶段分类器

        # logger.info(">>> 雷达系统组装完毕 <<<")

    def run_training_pipeline(self, raw_df: pd.DataFrame, val_ratio: float = 0.2) -> None:
        """
        执行完整的端到端训练流水线。

        流程: 特征工程 -> 数据对齐与展平 -> 时序划分训练/验证集 -> 模型训练与阈值寻优。

        Args:
            raw_df (pd.DataFrame): 包含历史各维度的原始数据集。
            val_ratio (float): 验证集划分比例 (默认最后 20% 的时间作为验证集，防止未来泄露)。
        """
        logger.info("========== [1/4] 启动特征工程 ==========")
        df_features = self.fe.process(raw_df)

        logger.info("========== [2/4] 启动数据对齐与展平 ==========")
        X, y = self.dt.create_training_dataset(df_features)

        logger.info("========== [3/4] 划分训练集与验证集 ==========")
        # 时序数据严禁随机打乱 (train_test_split)，必须按时间顺序切分
        split_idx = int(len(X) * (1 - val_ratio))
        X_train, y_train = X.iloc[:split_idx], y.iloc[:split_idx]
        X_val, y_val = X.iloc[split_idx:], y.iloc[split_idx:]
        logger.info(f"训练集大小: {len(X_train)} | 验证集大小: {len(X_val)}")

        logger.info("========== [4/4] 启动模型训练与高精度阈值寻优 ==========")
        self.clf1.train(X_train, y_train, X_val, y_val)

        logger.info(">>> 训练流水线全部执行完毕。 <<<")

    def run_inference(self, daily_features_df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        """
        线上推理接口。接收 D-1 至 D+1 的混合特征流，输出明天 24 小时的预警结果。

        注意：传入的数据至少需要包含过去24 * 7小时的历史记录，以满足滞后特征的需求。
             最后 24 行必须是明天 (D+1) 的时间戳。

        Args:
            daily_features_df (pd.DataFrame): 包含连续7天特征的数据帧。

        Returns:
            Tuple[np.ndarray, np.ndarray]:
                长度为 24 的一维数组 [0, 1, 0, ...]，代表明天各小时是否预警。
                长度为 24 的一维概率数组 [0.1, 0.9, 0.2, ...]，代表明天各小时预测为正样本的概率。
        """
        logger.info("========== 开始执行日内滚动推理 ==========")

        # 1. 动态生成特征
        df_features = self.fe.process(daily_features_df)

        # 2. 复用 DataTabularizer 的严格错位对齐逻辑
        # 我们不调用 create_training_dataset，因为明天还没有真实标签，且会 dropna
        aligned_df = self.dt.drop_actual_features(df_features)

        # 3. 精准截取明天的 24 个小时 (即 DataFrame 的最后 24 行)
        if len(aligned_df) < 24:
            raise ValueError("输入的数据流长度不足，无法截取明天的 24 个时刻！")

        X_tomorrow = aligned_df.iloc[-24:].copy()

        # 4. 筛选进入分类器的特征列 (剔除辅助列和目标列)
        cols_to_exclude = ['时刻', self.target_col, 'label']
        feature_cols = [c for c in X_tomorrow.columns if c not in cols_to_exclude]
        X_infer = X_tomorrow[feature_cols]

        # 检查是否有因历史数据长度不足导致的 NaN
        if X_infer.isnull().values.any():
            # X_infer = X_infer.fillna(method='ffill').fillna(0)  # 简单兜底
            raise ValueError("推理特征中存在 NaN！请确保传入了充足的 D-1 及更早的历史数据来填补 Shift 空缺。")

        # 5. 做预测
        # preds = self.clf1.predict(X_infer)
        preds = self.clf1.predict_under_expert_rules3(X_infer)     # 基于规则3的预测方式
        preds_prob = self.clf1.predict_proba(X_infer)
        if len(preds) != 24:
            logger.warning(f"预测输出的长度不是 24 ({len(preds)})，请检查输入时间轴！")

        return preds, preds_prob

