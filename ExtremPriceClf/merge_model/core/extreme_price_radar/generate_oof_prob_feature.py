# ^_^
import pandas as pd
import numpy as np
import logging
from sklearn.model_selection import KFold
from sklearn.metrics import roc_auc_score  # 路线A核心：概率评估必须用 AUC，不能用误差 MAE
import os

# 导入你现有的模块
from .features import FeatureEngineer
from .data_builder import DataTabularizer
from .classifier import ExtremePriceClassifier

logger = logging.getLogger(__name__)


def generate_oof_prob_feature(
    raw_df: pd.DataFrame,
    output_folder_path: str | None,
    k_folds: int = 5,
    end_time: str = "2026-01-01 00:00:00",
    extreme_threshold: float = -50.0,
) -> pd.DataFrame:
    """
    路线A：使用 ExtremePriceClassifier 生成 OOF 概率特征。 默认按-50打标签
    """
    raw_df = raw_df.copy()
    raw_df['时刻'] = pd.to_datetime(raw_df['时刻'])
    raw_df = raw_df[raw_df['时刻'] <= end_time]
    # 1. 基础特征工程
    logger.info("执行基础特征工程...")
    fe = FeatureEngineer()
    processed_df = fe.process(raw_df)

    # 2. 准备分类任务的 X 和 y (路线A核心：生成 0/1 标签)
    logger.info("准备分类任务数据集...")
    dt = DataTabularizer(target_col='实时电价', extreme_threshold=extreme_threshold)

    # 【修改点 1】：直接复用你写好的 create_training_dataset 方法
    # 它会自动帮你剔除会穿越的实际值特征，并且生成完美的 0/1 标签 y_clf
    X, y_clf = dt.create_training_dataset(processed_df)

    # 初始化一个与全量数据等长的全 0 数组，用于存放 OOF 概率预测值
    oof_predictions = np.zeros(len(X))

    # 3. K-Fold 交叉验证生成 OOF
    logger.info(f"启动 {k_folds} 折交叉验证训练分类模型 (ExtremePriceClassifier)...")

    # 【修改点 2】：时间序列数据绝对不能 shuffle=True！改为 False 防止未来数据泄露
    kf = KFold(n_splits=k_folds, shuffle=False)

    fold = 1
    for train_index, valid_index in kf.split(X):
        logger.info(f"正在处理 Fold {fold}/{k_folds} ...")

        X_train, X_valid = X.iloc[train_index], X.iloc[valid_index]
        y_train, y_valid = y_clf.iloc[train_index], y_clf.iloc[valid_index]

        # 实例化你自己的分类器 (可以传入你想要的 min_precision)
        clf_model = ExtremePriceClassifier()

        # 训练分类模型
        clf_model.train(X_train, y_train, X_valid, y_valid)

        # 对验证集进行预测，提取概率 (这里用 predict_proba 就完全合法了)
        val_preds = clf_model.predict_proba(X_valid)

        # 将预测概率填入 OOF 数组的对应位置
        oof_predictions[valid_index] = val_preds

        # 【修改点 3】：用 AUC 替代 MAE 来评估概率特征的质量
        # 加一个安全判定：如果这一折里恰好没有 -80 的极值（正样本为0），算 AUC 会报错
        if y_valid.sum() > 0:
            fold_auc = roc_auc_score(y_valid, val_preds)
            logger.info(f"Fold {fold} 完成, 验证集 AUC: {fold_auc:.4f}")
        else:
            logger.info(f"Fold {fold} 完成, 该折无极端负电价样本，跳过 AUC 计算")

        fold += 1

    # 4. 将生成的 OOF 概率特征合并回带有 '时刻' 的原始 processed_df
    processed_df = processed_df.reset_index(drop=True)

    # 【修改点 4】：因为生成的是概率，列名改为 p1_prob_OOF，与灰区文档对应
    processed_df['p1_prob_OOF'] = oof_predictions

    # 评估整体 OOF 预测的 AUC
    if y_clf.sum() > 0:
        total_auc = roc_auc_score(y_clf, oof_predictions)
        logger.info(f"OOF 特征生成完毕！全局综合 AUC 分数: {total_auc:.4f}")

    # 5. 按需保存带有 OOF 概率特征的数据集
    if output_folder_path:
        output_path = os.path.join(
            output_folder_path,
            f"{int(extreme_threshold)}训练集_with_oof_prob.xlsx",
        )
        processed_df.to_excel(output_path, index=False)
        logger.info(f"已将带有 OOF 概率特征的全新数据集保存至:{output_folder_path}目录下")
    return processed_df[["时刻", "p1_prob_OOF"]]


