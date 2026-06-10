# ^_^
# model/catboost_model.py
from catboost import CatBoostClassifier
import pandas as pd


class CatboostModel:
    """
    统一的二阶段模型接口封装 - CatBoost内核
    """
    def __init__(self, spw=1.0, random_seed=42):
        """初始化 CatBoost 模型配置。"""
        # 在这里配置针对“灰度小样本”的专属超参数
        self.model = CatBoostClassifier(
            iterations=500,
            learning_rate=0.03,
            depth=4,                # 树浅一点防过拟合
            l2_leaf_reg=10,         # 强L2正则
            scale_pos_weight=spw,   # 动态样本权重
            eval_metric='F1',
            random_seed=random_seed,
            verbose=0               # 保持控制台清爽
        )

    def fit(self, X_train, y_train):
        """训练 CatBoost 模型。"""
        self.model.fit(X_train, y_train)

    def predict_proba(self, X_test):
        """输出正样本概率。"""
        # 统一返回正样本（类别1）的概率数组
        return self.model.predict_proba(X_test)[:, 1]

    def get_feature_importance(self, feature_names):
        """返回特征重要性 DataFrame。"""
        importances = self.model.get_feature_importance()
        feat_imp = pd.DataFrame({
            'Feature': feature_names,
            'Importance': importances
        }).sort_values('Importance', ascending=False)
        return feat_imp
