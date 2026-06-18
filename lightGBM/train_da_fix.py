"""
LightGBM 日前电价预测 - 训练与寻优模块
======================================

本模块是日前电价（Day-Ahead）预测的训练核心，与实时电价预测的区别：
1. 日前电价在 D日 00:00 前确定，预测 D日的 24 小时电价
2. 特征工程使用 D-1 日全天数据作为滞后特征
3. Solar时段的负电价修正逻辑不同（日前市场负电价较少）

核心类：
- ThreeStageLGBM: 三段式模型封装（与实时预测相同结构）
- LGBMPowerPredictor: 主训练类，包含日前特有的特征工程

时段划分（业务时间 1-24点）：
- Valley (1-8点): 凌晨低谷时段
- Solar (9-16点): 光伏时段，日前场景负电价概率较低
- Peak (17-24点): 晚高峰时段
"""

import pandas as pd
import numpy as np
import warnings
from sklearn.metrics import mean_absolute_error
import lightgbm as lgb
import joblib
import os
import time

# 忽略警告信息
warnings.filterwarnings('ignore')


class ThreeStageLGBM:
    """
    三段式 LightGBM 模型封装类（日前版本）
    
    基于日前电价特性优化的三段式模型。
    与实时预测的主要区别在 Solar 时段的修正逻辑：
    - 日前场景：阈值 0.7，修正幅度 -80（负电价较少，更保守）
    - 实时场景：阈值 0.6，修正幅度 -100（负电价较多，更激进）
    
    Attributes
    ----------
    valley_reg : LGBMRegressor
        Valley时段回归模型
    solar_reg : LGBMRegressor
        Solar时段回归模型
    solar_clf : LGBMClassifier
        Solar时段负电价分类器
    peak_reg : LGBMRegressor
        Peak时段回归模型
    valley_hours : list
        Valley时段小时列表 [1,2,3,4,5,6,7,8]
    solar_hours : list
        Solar时段小时列表 [9,10,11,12,13,14,15,16]
    peak_hours : list
        Peak时段小时列表 [17,18,19,20,21,22,23,24]
    """
    
    def __init__(self, valley_reg, solar_reg, solar_clf, peak_reg):
        """
        初始化三段式模型
        
        Parameters
        ----------
        valley_reg : LGBMRegressor
            Valley时段回归模型
        solar_reg : LGBMRegressor
            Solar时段回归模型
        solar_clf : LGBMClassifier
            Solar时段负电价分类器
        peak_reg : LGBMRegressor
            Peak时段回归模型
        """
        self.valley_reg = valley_reg
        self.solar_reg = solar_reg
        self.solar_clf = solar_clf
        self.peak_reg = peak_reg
        
        # 对应 1-24点 业务小时
        self.valley_hours = [1, 2, 3, 4, 5, 6, 7, 8]
        self.solar_hours = [9, 10, 11, 12, 13, 14, 15, 16]
        self.peak_hours = [17, 18, 19, 20, 21, 22, 23, 24]

    def predict(self, X):
        """
        执行三段式预测（日前版本）
        
        日前场景的修正逻辑：
        - 负电价概率 > 0.7 且预测值 > 0 时，减去 80
        - 比实时场景更保守，因为日前市场负电价较少
        
        Parameters
        ----------
        X : DataFrame
            特征数据，必须包含 'hour' 列
        
        Returns
        -------
        ndarray
            预测结果数组
        """
        preds = np.zeros(len(X))
        
        # 创建时段掩码
        valley_mask = X['hour'].isin(self.valley_hours)
        solar_mask = X['hour'].isin(self.solar_hours)
        peak_mask = X['hour'].isin(self.peak_hours)
        
        # Valley时段预测
        if valley_mask.sum() > 0:
            preds[valley_mask] = self.valley_reg.predict(X[valley_mask])
        
        # Peak时段预测
        if peak_mask.sum() > 0:
            preds[peak_mask] = self.peak_reg.predict(X[peak_mask])

        # Solar时段预测（日前修正逻辑）
        if solar_mask.sum() > 0:
            X_solar = X[solar_mask]
            # 回归预测
            solar_preds = self.solar_reg.predict(X_solar)
            # 分类器预测负电价概率
            neg_probs = self.solar_clf.predict_proba(X_solar)[:, 1]
            
            # 日前电价修正逻辑：阈值更高（0.7），修正幅度更小（-80）
            correction_mask = (neg_probs > 0.7) & (solar_preds > 0)
            solar_preds[correction_mask] = solar_preds[correction_mask] - 80
            preds[solar_mask] = solar_preds
            
        return preds
    
    def fit(self, X, y):
        """占位符方法（实际训练在 LGBMPowerPredictor 中完成）"""
        pass


class LGBMPowerPredictor:
    """
    LightGBM 日前电价预测主训练类
    
    与实时预测的主要区别：
    1. 特征工程使用 D-1 全天数据作为滞后特征
    2. 特征列表略有不同（price_rolling_mean_24h 替代部分实时特征）
    3. 寻优窗口设置不同（日前在 D日 00:00 前完成）
    
    Attributes
    ----------
    model : ThreeStageLGBM
        训练好的三段式模型
    lgbm_n_jobs : int
        LightGBM 并行线程数
    _cuda_enabled : bool
        是否启用 CUDA 加速
    _cuda_fallback_logged : bool
        是否已记录 CUDA 降级日志
    features_list : list
        特征列名列表（日前版本）
    """
    
    def __init__(self):
        """初始化日前预测器"""
        self.model = None
        # 从环境变量读取配置
        self.lgbm_n_jobs = int(os.getenv("LGBM_N_JOBS", "4"))
        # CUDA 配置
        self._cuda_enabled = os.getenv("LGBM_DEVICE", "cuda").lower() == "cuda"
        self._cuda_fallback_logged = False
        
        # 日前预测特征列表（与实时预测略有不同）
        self.features_list = [
            'hour', 'month', 'day_of_week', 'is_weekend', 'hour_sin', 'hour_cos',
            'lag_price_target', 'price_rolling_mean_24h',  # 日前使用24小时滚动均值
            'load', 'wind', 'solar', 'interconnect',
            'bidding_space', 'space_ratio',
            'net_load', 'solar_ratio', 'net_load_sq',
            'wind_ratio', 'renew_penetration', 'ramp_load', 'ramp_solar',
            'prev_day_avg', 'prev_day_max', 'prev_day_min'  # 昨日统计特征
        ]

    def _device_type(self):
        """获取计算设备类型（cuda 或 cpu）"""
        return 'cpu'

    def _fit_with_cuda_fallback(self, model, X, y, **fit_kwargs):
        """
        带 CUDA 降级保护的模型训练
        
        如果 CUDA 训练失败，自动降级到 CPU。
        
        Parameters
        ----------
        model : LGBMModel
            LightGBM 模型实例
        X : DataFrame
            训练特征
        y : Series
            训练标签
        fit_kwargs : dict
            额外的 fit 参数
        
        Returns
        -------
        LGBMModel
            训练好的模型
        """
        try:
            model.fit(X, y, **fit_kwargs)
            return model
        except Exception as e:
            model_params = model.get_params()
            if model_params.get('device_type') != 'cuda':
                raise

            # CUDA 失败，降级到 CPU
            self._cuda_enabled = False
            fallback_params = dict(model_params)
            fallback_params['device_type'] = 'cpu'
            fallback_model = model.__class__(**fallback_params)

            if not self._cuda_fallback_logged:
                print(f"[LightGBM] CUDA 不可用，后续训练将统一使用 CPU。原因: {e}")
                self._cuda_fallback_logged = True
            fallback_model.fit(X, y, **fit_kwargs)
            return fallback_model

    def calculate_smape(self, y_true, y_pred):
        """
        计算对称平均绝对百分比误差（sMAPE）
        
        处理负电价：小于50的值用50替代
        
        Parameters
        ----------
        y_true : array-like
            真实值
        y_pred : array-like
            预测值
        
        Returns
        -------
        float
            sMAPE值（百分比）
        """
        y_true = np.array(y_true)
        y_pred = np.array(y_pred)
        y_true_fixed = np.where(y_true < 50, 50, y_true)
        y_pred_fixed = np.where(y_pred < 50, 50, y_pred)
        numerator = np.abs(y_pred_fixed - y_true_fixed)
        denominator = (np.abs(y_pred_fixed) + np.abs(y_true_fixed)) / 2.0
        with np.errstate(divide='ignore', invalid='ignore'):
            terms = numerator / denominator
            terms[denominator == 0] = 0.0
        return np.mean(terms) * 100
    
    def load_and_process_data(self, file_path):
        """
        加载并预处理原始数据
        
        支持 CSV 和 Excel 格式。
        
        Parameters
        ----------
        file_path : str
            数据文件路径
        
        Returns
        -------
        DataFrame
            预处理后的数据
        """
        # 根据文件类型加载
        if file_path.endswith('.xlsx') or file_path.endswith('.xls'):
            df = pd.read_excel(file_path)
        else:
            try:
                df = pd.read_csv(file_path, encoding='gbk')
            except:
                df = pd.read_csv(file_path, encoding='utf-8')

        # 列名标准化
        df.columns = [c.strip() for c in df.columns]

        # 列名映射（日前电价场景）
        time_col = '时刻'
        price_col = '日前电价'
        load_col = '直调负荷预测值'
        wind_col = '风电总加预测值'
        solar_col = '光伏总加预测值'
        inter_col = '联络线受电负荷预测值'

        # 数据类型转换
        df['ds'] = pd.to_datetime(df[time_col], errors='coerce')
        df['y'] = pd.to_numeric(df[price_col], errors='coerce')
        df['load'] = pd.to_numeric(df[load_col], errors='coerce').ffill()
        df['wind'] = pd.to_numeric(df[wind_col], errors='coerce').ffill() if wind_col else 0
        df['solar'] = pd.to_numeric(df[solar_col], errors='coerce').ffill() if solar_col else 0
        df['interconnect'] = pd.to_numeric(df[inter_col], errors='coerce').ffill() if inter_col else 0
        
        # 排序并清理
        df = df.dropna(subset=['ds', 'y']).sort_values('ds').reset_index(drop=True)
        return df
       
    def feature_engineering(self, df):
        """
        日前电价特征工程
        
        与实时预测的主要区别：
        1. 日前场景：D-1 全天数据已产出，可用作滞后特征
        2. 滞后策略：周一用上周同期（168小时前），其他用24小时前
        3. 包含昨日全天统计特征（均值、最大、最小）
        
        ★ 核心逻辑：使用"1秒偏移法"定义业务时间
        - 物理 00:00 -> 业务 前一天 24:00
        
        Parameters
        ----------
        df : DataFrame
            原始数据
        
        Returns
        -------
        DataFrame
            添加了特征的数据
        """
        df = df.copy()
        
        # ★ 1秒偏移逻辑：00:00 归属前一天 24:00
        adjusted_time = df['ds'] - pd.Timedelta(seconds=1)
        
        # 1. 基础时间特征 (1-24h 业务习惯)
        df['hour'] = adjusted_time.dt.hour + 1
        df['month'] = adjusted_time.dt.month
        df['day_of_week'] = adjusted_time.dt.dayofweek
        df['is_weekend'] = df['day_of_week'].isin([5, 6]).astype(int)
        
        # 周期特征使用业务小时映射
        df['hour_sin'] = np.sin(2 * np.pi * (df['hour'] - 1) / 23)
        df['hour_cos'] = np.cos(2 * np.pi * (df['hour'] - 1) / 23)
        
        # 2. 滞后特征（日前场景：D-1全天已产出）
        # 注意：此处shift按物理行(1h/行)操作
        df['lag_24h'] = df['y'].shift(24)    # 24小时前
        df['lag_168h'] = df['y'].shift(168)  # 168小时前（上周同期）
        
        # 策略滞后：判定日期基于业务时间轴 adjusted_time
        # 周一（day_of_week==0）用上周同期，其他用24小时前
        df['lag_price_target'] = np.where(df['day_of_week'] == 0, df['lag_168h'], df['lag_24h'])
        df['price_rolling_mean_24h'] = df['y'].shift(24).rolling(window=24).mean()
        
        # 填充缺失值
        df['lag_price_target'] = df['lag_price_target'].ffill().fillna(0)
        df['price_rolling_mean_24h'] = df['price_rolling_mean_24h'].ffill().fillna(0)

        # 3. 物理特征
        safe_load = df['load'].replace(0, 1)
        df['net_load'] = df['load'] - df['wind'] - df['solar']
        df['solar_ratio'] = df['solar'] / safe_load
        df['net_load_sq'] = (df['net_load'] / 1000) ** 2
        df['bidding_space'] = df['net_load'] - df['interconnect']
        df['space_ratio'] = df['bidding_space'] / safe_load
        df['wind_ratio'] = df['wind'] / safe_load
        df['renew_penetration'] = (df['wind'] + df['solar']) / safe_load
        df['ramp_load'] = df['load'].diff().fillna(0)
        df['ramp_solar'] = df['solar'].diff().fillna(0)
        
        # 4. 昨日全天统计量（基于业务日期 date_only）
        df['date_only'] = adjusted_time.dt.date
        # shift(1) 确保预测 D 业务日时，统计的是 D-1 业务日的全天价格
        daily_stats = df.groupby('date_only')['y'].agg(
            prev_day_avg='mean',
            prev_day_max='max',
            prev_day_min='min'
        ).shift(1).reset_index()
        
        df = df.merge(daily_stats, on='date_only', how='left')
        df = df.drop(columns=['date_only', 'lag_24h', 'lag_168h'])
        return df.ffill().fillna(0)

    def optimize_data_window(self, file_path, test_start_date, test_end_date=None, 
                            step_months=6, target='日前电价'):
        """
        动态训练窗口寻优（日前版本）
        
        与实时预测的区别：
        - 日前寻优在 D日 00:00 前完成
        - 验证区间通常更长（31天）
        
        Parameters
        ----------
        file_path : str
            数据文件路径
        test_start_date : str
            验证开始时间
        test_end_date : str, optional
            验证结束时间
        step_months : int
            每次增加的月数，默认 6
        target : str
            目标列名
        
        Returns
        -------
        dict
            最佳结果，包含 months_back, mae, smape, model
        """
        # 加载和特征工程
        raw_df = self.load_and_process_data(file_path)
        full_df = self.feature_engineering(raw_df)
        
        # 转换测试集边界
        test_start_dt = pd.to_datetime(test_start_date)
        if test_end_date:
            test_end_dt = pd.to_datetime(test_end_date)
            test_mask = (full_df['ds'] >= test_start_dt) & (full_df['ds'] <= test_end_dt)
        else:
            test_mask = full_df['ds'] >= test_start_dt

        test_df_raw = full_df[test_mask].copy()
        if len(test_df_raw) == 0:
            print(f"错误：测试集为空！")
            return

        # 打印表头
        print(f"\n{'Window':<10} | {'MAE':<8} | {'sMAPE':<10} | {'NegRecall':<10} | {'Time':<8}")
        
        results = []
        data_min_date = full_df['ds'].min()
        months_back = 12  # 从12个月开始
        
        # 三段式时段定义
        valley_hours = [1, 2, 3, 4, 5, 6, 7, 8]
        solar_hours = [9, 10, 11, 12, 13, 14, 15, 16]
        peak_hours = [17, 18, 19, 20, 21, 22, 23, 24]
        
        # 动态窗口寻优循环
        while True:
            # 计算训练开始时间
            train_start_dt = test_start_dt - pd.DateOffset(months=months_back)
            is_limit = False
            if train_start_dt < data_min_date:
                train_start_dt = data_min_date
                is_limit = True

            # 划分训练集
            train_mask = (full_df['ds'] >= train_start_dt) & (full_df['ds'] < test_start_dt)
            train_df = full_df[train_mask].copy()
            
            # 数据量检查
            if len(train_df) < 2000:
                if is_limit:
                    break
                months_back += step_months
                continue

            # 标签截断（处理极端值）
            train_upper = train_df['y'].quantile(0.995)
            train_df['y_clipped'] = train_df['y'].clip(lower=-100, upper=train_upper)
            
            t0 = time.time()
            
            # ========== A. Valley 时段训练 ==========
            train_valley = train_df[train_df['hour'].isin(valley_hours)]
            test_valley = test_df_raw[test_df_raw['hour'].isin(valley_hours)]
            model_valley_reg = lgb.LGBMRegressor(
                objective='regression',
                n_estimators=2000,
                learning_rate=0.05,
                num_leaves=31,
                n_jobs=self.lgbm_n_jobs,
                device_type=self._device_type(),
                verbose=-1,
                random_state=42
            )
            model_valley_reg = self._fit_with_cuda_fallback(
                model_valley_reg,
                train_valley[self.features_list],
                train_valley['y_clipped'],
                eval_set=[(test_valley[self.features_list], test_valley['y'])],
                eval_metric='l1',
                callbacks=[lgb.early_stopping(100, verbose=False), lgb.log_evaluation(0)]
            )

            # ========== B. Solar 时段训练 ==========
            train_solar = train_df[train_df['hour'].isin(solar_hours)]
            test_solar = test_df_raw[test_df_raw['hour'].isin(solar_hours)]
            
            # Solar回归模型
            model_solar_reg = lgb.LGBMRegressor(
                objective='regression',
                n_estimators=3000,
                learning_rate=0.03,
                num_leaves=63,
                n_jobs=self.lgbm_n_jobs,
                device_type=self._device_type(),
                verbose=-1,
                random_state=42
            )
            model_solar_reg = self._fit_with_cuda_fallback(
                model_solar_reg,
                train_solar[self.features_list],
                train_solar['y_clipped'],
                eval_set=[(test_solar[self.features_list], test_solar['y'])],
                eval_metric='l1',
                callbacks=[lgb.early_stopping(100, verbose=False), lgb.log_evaluation(0)]
            )
            
            # Solar分类模型（负电价检测）
            y_solar_class = (train_solar['y_clipped'] < 0).astype(int)
            y_solar_test_class = (test_solar['y'] < 0).astype(int)
            model_solar_clf = lgb.LGBMClassifier(
                objective='binary',
                n_estimators=1000,
                learning_rate=0.05,
                class_weight='balanced',
                n_jobs=self.lgbm_n_jobs,
                device_type=self._device_type(),
                verbose=-1,
                random_state=42
            )
            model_solar_clf = self._fit_with_cuda_fallback(
                model_solar_clf,
                train_solar[self.features_list],
                y_solar_class,
                eval_set=[(test_solar[self.features_list], y_solar_test_class)],
                eval_metric='auc',
                callbacks=[lgb.early_stopping(100, verbose=False), lgb.log_evaluation(0)]
            )

            # ========== C. Peak 时段训练 ==========
            train_peak = train_df[train_df['hour'].isin(peak_hours)]
            test_peak = test_df_raw[test_df_raw['hour'].isin(peak_hours)]
            model_peak_reg = lgb.LGBMRegressor(
                objective='regression',
                n_estimators=3000,
                learning_rate=0.03,
                num_leaves=40,
                n_jobs=self.lgbm_n_jobs,
                device_type=self._device_type(),
                verbose=-1,
                random_state=42
            )
            model_peak_reg = self._fit_with_cuda_fallback(
                model_peak_reg,
                train_peak[self.features_list],
                train_peak['y_clipped'],
                eval_set=[(test_peak[self.features_list], test_peak['y'])],
                eval_metric='l1',
                callbacks=[lgb.early_stopping(100, verbose=False), lgb.log_evaluation(0)]
            )

            # 组合三段式模型
            combined_model = ThreeStageLGBM(
                model_valley_reg,
                model_solar_reg,
                model_solar_clf,
                model_peak_reg
            )
            
            t1 = time.time()
            
            # 评估模型
            pred = combined_model.predict(test_df_raw[self.features_list])
            pred = np.where(pred < -80, -80, pred)  # 截断极端值
            mae = mean_absolute_error(test_df_raw['y'], pred)
            smape = self.calculate_smape(test_df_raw['y'], pred)
            
            # 负电价召回率
            neg_mask = test_df_raw['y'] < 0
            neg_recall = (pred[neg_mask] < 0).sum() / neg_mask.sum() * 100 if neg_mask.sum() > 0 else 0

            # 打印结果
            print(f"{months_back:<10} | {mae:<8.2f} | {smape:<10.2f} | {neg_recall:<10.2f}% | {t1-t0:<8.2f}")
            
            results.append({
                'months_back': months_back if not is_limit else 'All Data',
                'mae': mae,
                'smape': smape,
                'model': combined_model
            })
            
            if is_limit:
                break
            months_back += step_months

        # 保存最佳模型
        best_result = self.save_model(results, '日前电价')
        return best_result

    def save_model(self, results, target):
        """
        保存最佳模型到文件
        
        Parameters
        ----------
        results : list
            各窗口的评估结果列表
        target : str
            目标列名（用于文件名）
        
        Returns
        -------
        dict
            最佳结果
        """
        if not results:
            return None
        
        df_res = pd.DataFrame(results)
        
        # 获取最大月份数
        max_month = df_res[df_res['months_back'] != 'All Data']['months_back'].max()
        if pd.isna(max_month):
            max_month = 12
        
        # 选择 sMAPE 最低的模型
        best_idx = df_res['smape'].idxmin()
        best_row = df_res.loc[best_idx]
        
        # 构建模型路径
        PROJECT_ROOT = os.getenv("PROJECT_ROOT", ".")
        LightGBM_MODEL_PATH = os.getenv("LightGBM_MODEL_PATH", "models/LightGBM/best_model_{}.pkl")
        best_model_path = os.path.join(PROJECT_ROOT, LightGBM_MODEL_PATH.format(target))
        
        # 确保目录存在
        model_dir = os.path.dirname(best_model_path)
        os.makedirs(model_dir, exist_ok=True)
        
        # 删除旧模型
        if os.path.exists(best_model_path):
            try:
                os.remove(best_model_path)
                print(f"[模型] 删除旧模型: {best_model_path}")
            except Exception as e:
                print(f"[模型] 警告：无法删除旧模型: {e}")
        
        # 保存新模型
        try:
            joblib.dump(best_row['model'], best_model_path)
            print(f"[模型] 最佳模型已保存: {best_model_path}")
        except Exception as e:
            print(f"[模型] 错误：无法保存模型 {best_model_path}: {e}")
            raise

        return best_row


if __name__ == "__main__":
    """主程序入口：用于测试日前训练功能"""
    from dotenv import load_dotenv
    import os

    # 加载环境变量
    load_dotenv()

    PROJECT_ROOT = os.getenv("PROJECT_ROOT", ".")
    DATA_SET_NAME = os.getenv("DATA_SET_NAME")

    # 构建数据路径
    data_path = os.path.join(PROJECT_ROOT, DATA_SET_NAME)
    
    # 初始化预测器
    predictor = LGBMPowerPredictor()
    
    # 执行窗口寻优
    predictor.optimize_data_window(
        file_path=data_path,
        test_start_date='2026-01-01 01:00:00',
        test_end_date='2026-02-01 00:00:00',
        step_months=2,
        target='日前电价'
    )
