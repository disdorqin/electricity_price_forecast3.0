# ^_^
"""
模块：classifier.py
职责：封装 LightGBM 模型，动态处理极度样本不平衡，并基于验证集实现高精度概率阈值寻优。
调整参数：
        self.best_threshold_: float = 0.55  # 默认阈值，训练后会被动态覆盖
        dynamic_spw = base_ratio * 0.3      正样本权重
        scale_pos_weight=dynamic_spw,       # 设定正例样本的权重，使模型更关注极值
"""

import logging
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import precision_recall_curve, precision_score, recall_score, f1_score
from typing import Tuple, Optional

# 配置模块日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ExtremePriceClassifier:
    """
    极端负电价雷达 - 分类器核心类
    封装 LightGBM，处理严重的数据不平衡，并在指定 Precision 约束下最大化 Recall。
    """

    def __init__(self, random_state: int = 42, min_precision: float = 0.70):
        """
        初始化分类器。

        Args:
            random_state (int): 随机种子，保证结果可复现。
            min_precision (float): 阈值寻优时可接受的最低精确率 (Precision) 边界。
        """
        self.random_state = random_state
        self.min_precision = min_precision

        # 模型与状态
        self.model: Optional[lgb.LGBMClassifier] = None
        self.best_threshold_: float = 0.55  # 默认阈值，训练后会被动态覆盖
        self.is_fitted_: bool = False

        # logger.info(f"ExtremePriceClassifier 初始化完成。目标最小精确率: {self.min_precision}")

    def _find_best_threshold(self, y_val: np.ndarray, y_pred_proba: np.ndarray) -> None:
        """
        [内部私有方法] 使用 P-R 曲线寻找最佳概率拦截阈值 T。
        逻辑：在满足 Precision >= min_precision 的前提下，寻找能让 Recall 最大的阈值。

        Args:
            y_val (np.ndarray): 验证集的真实标签。
            y_pred_proba (np.ndarray): 验证集的预测正样本概率。
        """
        logger.info("开始执行动态阈值寻优 (Dynamic Threshold Tuning)...")

        # 计算 P-R 曲线的所有点
        precisions, recalls, thresholds = precision_recall_curve(y_val, y_pred_proba)

        # 注意：precision_recall_curve 返回的 thresholds 数组比 precisions 和 recalls 少一个元素
        # 最后一个元素对应的是 threshold=1.0 的极端情况，我们切片对齐
        p_slice = precisions[:-1]
        r_slice = recalls[:-1]

        # 寻找满足最低 Precision 要求的索引
        valid_indices = np.where(p_slice >= self.min_precision)[0]

        if len(valid_indices) == 0:
            logger.warning(
                f"警告：没有任何一个阈值能让 Precision 达到 {self.min_precision}！"
                "退而求其次，选择 Precision 最高的那个阈值。"
            )
            best_idx = np.argmax(p_slice)
        else:
            # 在所有满足 Precision 条件的候选项中，寻找 Recall 最大的那一个
            best_valid_idx = np.argmax(r_slice[valid_indices])
            best_idx = valid_indices[best_valid_idx]

        self.best_threshold_ = thresholds[best_idx]

        # 打印寻优结果
        best_p = p_slice[best_idx]
        best_r = r_slice[best_idx]
        logger.info(
            f"阈值寻优完毕！最佳拦截概率阈值 T = {self.best_threshold_:.4f} "
            f"(当前验证集表现 -> Precision: {best_p:.4f}, Recall: {best_r:.4f})"
        )

    def train(self, X_train: pd.DataFrame, y_train: pd.Series,
              X_val: pd.DataFrame, y_val: pd.Series) -> None:
        """
        训练 LightGBM 模型并执行阈值寻优。

        Args:
            X_train (pd.DataFrame): 训练集特征。
            y_train (pd.Series): 训练集标签。
            X_val (pd.DataFrame): 验证集特征。
            y_val (pd.Series): 验证集标签。
        """
        logger.info("启动模型训练...")

        # 1. 动态计算样本不平衡比例
        pos_count = (y_train == 1).sum()
        neg_count = (y_train == 0).sum()

        if pos_count == 0:
            raise ValueError("训练集中没有发现正样本（<-70的异常值），模型无法训练！")

        base_ratio = neg_count / pos_count
        # 将基础比例放大 10 倍，给予正样本极高的惩罚权重，强迫模型关注极值
        dynamic_spw = base_ratio * 0.3

        logger.info(f"正负样本分布: 负类 {neg_count} | 正类 {pos_count}。")
        logger.info(f"动态设定的 scale_pos_weight = {dynamic_spw:.2f}")

        # 2. 初始化并训练 LightGBM
        # 设置适当的正规化参数防止过度拟合
        self.model = lgb.LGBMClassifier(
            n_estimators=200,
            learning_rate=0.05,
            scale_pos_weight=dynamic_spw,       # 设定正例样本的权重，使模型更关注极值
            max_depth=5,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=self.random_state,
            n_jobs=-1,
            importance_type='gain',
            verbosity=-1
        )

        # 兼容不同版本 LightGBM 的 early_stopping 方式
        callbacks = [lgb.early_stopping(stopping_rounds=30, verbose=False)]

        self.model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            callbacks=callbacks
        )

        logger.info(f"模型训练完成。最佳迭代次数: {self.model.best_iteration_}")
        self.is_fitted_ = True

        # 3. 在验证集上预测概率并寻优最佳阈值
        y_val_proba = self.model.predict_proba(X_val)[:, 1]
        # self._find_best_threshold(y_val.values, y_val_proba)

    def predict_proba(self, X_test: pd.DataFrame) -> np.ndarray:
        """
        输出样本为极端负价（类别1）的原始概率。

        Args:
            X_test (pd.DataFrame): 测试集特征。

        Returns:
            np.ndarray: 概率数组。
        """
        if not self.is_fitted_ or self.model is None:
            raise RuntimeError("分类器尚未训练，请先调用 train() 方法。")
        return self.model.predict_proba(X_test)[:, 1]

    def predict(self, X_test: pd.DataFrame) -> np.ndarray:
        """
        根据寻优得到的阈值 T 进行截断，输出最终的二进制预测结果。

        Args:
            X_test (pd.DataFrame): 测试集特征。

        Returns:
            np.ndarray: 二分类结果 [0, 1, 0, ...]
        """
        proba = self.predict_proba(X_test)
        # 根据动态阈值进行概率截断
        binary_preds = (proba >= self.best_threshold_).astype(int)

        trigger_count = binary_preds.sum()
        logger.info(f"推理完成：共产生 {trigger_count} 个极端负电价警报信号。")

        return binary_preds

    def predict_under_expert_rules3(self, X_test: pd.DataFrame) -> np.ndarray:
        """
        加入规则3的预测方式
        如果上一时刻判定为异常 (1)，则降低当前时刻的判断门槛（比如降低 20% 的阈值要求）
        """
        proba = self.predict_proba(X_test)

        # 1. 基础概率截断
        binary_preds = (proba >= self.best_threshold_).astype(int)

        # 2. 专家经验规则 3 的落地：时序状态惯性 (Momentum)
        # 如果上一时刻判定为异常 (1)，则降低当前时刻的判断门槛（比如降低 20% 的阈值要求）
        momentum_threshold = self.best_threshold_ * 0.80

        for i in range(1, len(binary_preds)):
            # 如果前一个小时触发了报警，且当前小时的原始概率达到了“打折后”的阈值
            if binary_preds[i - 1] == 1 and proba[i] >= momentum_threshold:
                binary_preds[i] = 1

        trigger_count = binary_preds.sum()
        logger.info(f"推理完成：共产生 {trigger_count} 个极端负电价警报信号 (含专家规则修正)。")

        return binary_preds



