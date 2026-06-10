# ^_^
# model/xgboost_model.py
import xgboost as xgb
import pandas as pd
import numpy as np


class XgboostModel:
    """
    统一的二阶段模型接口封装 - XGBoost内核 (专为小样本灰度区间调优)
    """
    def __init__(self, spw=1.0, random_seed=42):
        """初始化 XGBoost 模型配置。"""
        # 针对 6000 样本量级的强正则化配置
        self.model = xgb.XGBClassifier(
            n_estimators=300,        # 树的数量，配合较低的学习率
            learning_rate=0.03,      # 学习率
            max_depth=3,             # 【关键】极其严格的深度限制 (通常一阶段可能是6-8)
            min_child_weight=5,      # 【关键】叶子节点最小权重，防止对极少数噪点进行切分
            gamma=0.5,               # 【关键】后剪枝惩罚项，只有分裂带来的增益大于此值才分裂
            subsample=0.8,           # 行采样，每次建树随机抽取80%的数据，增加多样性
            colsample_bytree=0.8,    # 列采样，每次建树随机抽取80%的特征，对抗特征冲突
            reg_alpha=2.0,           # L1正则化，促使权重稀疏
            reg_lambda=5.0,          # L2正则化，抑制权重过大
            scale_pos_weight=spw,    # 动态正负样本权重比例
            random_state=random_seed,
            n_jobs=-1,
            verbosity=0              # 0表示静默模式，关闭冗余的运行日志
        )

    def fit(self, X_train, y_train):
        """训练 XGBoost 模型。"""
        # XGBoost 可以直接接收 Pandas DataFrame
        self.model.fit(X_train, y_train)

    def predict_proba(self, X_test):
        """输出正样本概率。"""
        # predict_proba 返回 Nx2 的矩阵，[:, 1] 取出属于类别 1 的概率
        return self.model.predict_proba(X_test)[:, 1]

    def get_feature_importance(self, feature_names):
        """返回特征重要性 DataFrame。"""
        # XGBoost 默认的 feature_importances_ 衡量的是特征在所有树中分裂时带来的平均增益 (Gain)
        importances = self.model.feature_importances_
        feat_imp = pd.DataFrame({
            'Feature': feature_names,
            'Importance': importances
        }).sort_values('Importance', ascending=False)
        return feat_imp
