# ^_^
# model/lightgbm_model.py
import lightgbm as lgb
import pandas as pd


class LightgbmModel:
    """
    统一的二阶段模型接口封装 - LightGBM内核
    """
    def __init__(self, spw=1.0, random_seed=42):
        """初始化 LightGBM 模型配置。"""
        self.model = lgb.LGBMClassifier(
            n_estimators=300,
            learning_rate=0.03,
            max_depth=4,
            num_leaves=15,
            reg_alpha=2.0,          # 强L1正则防过拟合
            reg_lambda=5.0,         # 强L2正则
            scale_pos_weight=spw,
            random_state=random_seed,
            n_jobs=-1,
            verbose=-1
        )

    def fit(self, X_train, y_train):
        """训练 LightGBM 模型。"""
        self.model.fit(X_train, y_train)

    def predict_proba(self, X_test):
        """输出正样本概率。"""
        return self.model.predict_proba(X_test)[:, 1]

    def get_feature_importance(self, feature_names):
        """返回特征重要性 DataFrame。"""
        importances = self.model.feature_importances_
        feat_imp = pd.DataFrame({
            'Feature': feature_names,
            'Importance': importances
        }).sort_values('Importance', ascending=False)
        return feat_imp
