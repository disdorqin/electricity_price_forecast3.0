"""
LightGBM 电价预测 - 训练与寻优模块
==================================

本模块包含 LightGBM 电价预测的核心训练逻辑，实现了：
1. 三段式分时段建模（Valley/Solar/Peak）
2. 动态训练窗口寻优
3. 特征工程（滞后特征、物理特征、统计特征）
4. CUDA/CPU 自动降级

核心类：
- ThreeStageLGBM: 三段式模型封装，整合三个时段的模型
- LGBMPowerPredictor: 主训练类，包含数据加载、特征工程、训练、寻优

时段划分（业务时间 1-24点）：
- Valley (1-8点): 凌晨低谷时段，基础负荷
- Solar (9-16点): 光伏大发时段，可能出现负电价
- Peak (17-24点): 晚高峰时段，风电影响大
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')  # 使用非交互式后端，避免显示问题
import warnings
from sklearn.metrics import mean_absolute_error
import lightgbm as lgb
import joblib
from dotenv import load_dotenv
import os
import time

# 加载环境变量
load_dotenv()

# 忽略警告信息
warnings.filterwarnings('ignore')


class ThreeStageLGBM:
    """
    三段式 LightGBM 模型封装类
    
    基于误差分析将一天分为三个时段分别建模：
    1. Valley (01-08点): 基础负荷时段，电价相对稳定
    2. Solar (09-16点): 光伏深调时段，可能出现负电价，使用回归+分类器修正
    3. Peak (17-24点): 晚高峰时段，受风电影响大
    
    Attributes
    ----------
    valley_reg : LGBMRegressor
        Valley时段的回归模型
    solar_reg : LGBMRegressor
        Solar时段的回归模型
    solar_clf : LGBMClassifier
        Solar时段的二分类模型（预测是否负电价）
    peak_reg : LGBMRegressor
        Peak时段的回归模型
    valley_hours : list
        Valley时段的小时列表 [1,2,3,4,5,6,7,8]
    solar_hours : list
        Solar时段的小时列表 [9,10,11,12,13,14,15,16]
    peak_hours : list
        Peak时段的小时列表 [17,18,19,20,21,22,23,24]
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
        
        # 时段划分（业务时间 1-24点）
        self.valley_hours = [1, 2, 3, 4, 5, 6, 7, 8]      # 凌晨低谷
        self.solar_hours = [9, 10, 11, 12, 13, 14, 15, 16]  # 光伏时段
        self.peak_hours = [17, 18, 19, 20, 21, 22, 23, 24]  # 晚高峰

    def predict(self, X):
        """
        执行三段式预测
        
        根据小时特征将数据分配到对应时段的模型进行预测，
        Solar时段还会使用分类器进行负电价修正。
        
        Parameters
        ----------
        X : DataFrame
            特征数据，必须包含 'hour' 列
        
        Returns
        -------
        ndarray
            预测结果数组
        
        修正逻辑：
        - 如果分类器认为负电价概率 > 0.6 且回归预测 > -20
        - 则将预测值减去 100（向下修正）
        """
        preds = np.zeros(len(X))
        
        # 根据小时创建掩码
        valley_mask = X['hour'].isin(self.valley_hours)
        solar_mask = X['hour'].isin(self.solar_hours)
        peak_mask = X['hour'].isin(self.peak_hours)
        
        # Valley时段预测
        if valley_mask.sum() > 0:
            preds[valley_mask] = self.valley_reg.predict(X[valley_mask])
        
        # Peak时段预测
        if peak_mask.sum() > 0:
            preds[peak_mask] = self.peak_reg.predict(X[peak_mask])

        # Solar时段预测（带负电价修正）
        if solar_mask.sum() > 0:
            X_solar = X[solar_mask]
            # 回归预测
            solar_preds = self.solar_reg.predict(X_solar)
            # 分类器预测负电价概率
            neg_probs = self.solar_clf.predict_proba(X_solar)[:, 1]
            
            # 修正逻辑：如果分类器认为负电价概率高，且回归预测值还偏高，施加下压
            correction_mask = (neg_probs > 0.6) & (solar_preds > -20)
            solar_preds[correction_mask] = solar_preds[correction_mask] - 100
            preds[solar_mask] = solar_preds
            
        #这里没有看懂 if是干什么的
        return preds
    
    def fit(self, X, y):
        """占位符方法，实际训练在 LGBMPowerPredictor 中完成"""
        pass


class LGBMPowerPredictor:
    """
    LightGBM 电价预测主训练类
    
    负责完整的数据处理、特征工程、模型训练和窗口寻优流程。
    
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
        特征列名列表
    """
    
    def __init__(self):
        """初始化预测器"""
        self.model = None
        # 从环境变量读取配置，默认使用 4 线程
        self.lgbm_n_jobs = int(os.getenv("LGBM_N_JOBS", "4"))
        
        # CUDA 配置，默认启用
        self._cuda_enabled = os.getenv("LGBM_DEVICE", "cuda").lower() == "cuda"
        self._cuda_fallback_logged = False
        
        # 特征列表（21个特征）
        self.features_list = [
            'hour', 'month', 'day_of_week', 'is_weekend',           # 时间特征 (4)
            'lag_price_target', 'lag_price_week',                     # 滞后特征 (2)
            'load', 'wind', 'solar', 'interconnect',                  # 物理特征 (4)
            'bidding_space', 'space_ratio',                           # 竞价空间 (2)
            'net_load', 'solar_ratio', 'net_load_sq',                 # 净负荷 (3)
            'wind_ratio', 'renew_penetration', 'ramp_load', 'ramp_solar',  # 新能源 (4)
            'morning_mean', 'noon_min', 'morning_std', 'morning_trend', 'is_info_fresh'  # 统计特征 (5)
        ]

    def _device_type(self):
        """获取计算设备类型（cuda 或 cpu）"""
        return 'cpu'

    def _fit_with_cuda_fallback(self, model, X, y, **fit_kwargs):
        """
        带 CUDA 降级保护的模型训练
        
        如果 CUDA 训练失败，自动降级到 CPU 并继续训练。
        
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
        
        处理负电价和极端值：
        - 真实值/预测值 < 50 时，用 50 替代（避免除零）
        
        Parameters
        ----------
        y_true : array-like
            真实值
        y_pred : array-like
            预测值
        
        Returns
        -------
        float
            sMAPE 值（百分比）
        """
        y_true = np.array(y_true)
        y_pred = np.array(y_pred)
        
        # 处理负电价：小于50的值用50替代
        y_true_fixed = np.where(y_true < 50, 50, y_true)
        y_pred_fixed = np.where(y_pred < 50, 50, y_pred)
        
        numerator = np.abs(y_pred_fixed - y_true_fixed)
        denominator = (np.abs(y_pred_fixed) + np.abs(y_true_fixed)) / 2.0
        
        with np.errstate(divide='ignore', invalid='ignore'):
            terms = numerator / denominator
            terms[denominator == 0] = 0.0
        return np.mean(terms) * 100
    
    def load_and_process_data(self, file_path, target='实时电价'):
        """
        加载并预处理原始数据
        
        支持 CSV 和 Excel 格式，自动处理编码问题。
        
        Parameters
        ----------
        file_path : str
            数据文件路径
        target : str
            目标列名
        
        Returns
        -------
        DataFrame
            预处理后的数据，包含列：
            - ds: 时间戳
            - y: 目标电价
            - load: 负荷
            - wind: 风电
            - solar: 光伏
            - interconnect: 联络线
        """
        # 根据文件类型加载
        if file_path.endswith('.xlsx') or file_path.endswith('.xls'):
            df = pd.read_excel(file_path)
        else:
            try:
                df = pd.read_csv(file_path, encoding='gbk')
            except:
                df = pd.read_csv(file_path, encoding='utf-8')
        
        # 列名映射
        time_col = '时刻'
        price_col = target
        load_col = '直调负荷预测值'
        wind_col = '风电总加预测值'
        solar_col = '光伏总加预测值'
        inter_col = '联络线受电负荷预测值'

        if not all([time_col, price_col, load_col, wind_col, solar_col, inter_col]):
            print(f"关键列缺失！")
            exit()

        # 数据类型转换
        df['ds'] = pd.to_datetime(df[time_col], errors='coerce')
        df['y'] = pd.to_numeric(df[price_col], errors='coerce')
        df['load'] = pd.to_numeric(df[load_col], errors='coerce').ffill()
        df['wind'] = pd.to_numeric(df[wind_col], errors='coerce').ffill()
        df['solar'] = pd.to_numeric(df[solar_col], errors='coerce').ffill()
        df['interconnect'] = pd.to_numeric(df[inter_col], errors='coerce').ffill()
        
        df = df.sort_values('ds').reset_index(drop=True)
        return df
    
    def feature_engineering(self, df):
        """
        特征工程主函数
        
        生成 21 维特征，包括：
        1. 基础时间特征（小时、月份、星期、是否周末）
        2. 滞后特征（48小时前、168小时前的电价）
        3. 物理特征（净负荷、光伏占比、竞价空间等）
        4. D日统计特征（上午均值、中午最小值等）
        
        ★ 核心逻辑：使用"1秒偏移法"定义业务时间
        - 物理 00:00 -> 业务 前一天 24:00
        - 这样确保 00:00 的数据归属于正确的业务日
        
        Parameters
        ----------
        df : DataFrame
            原始数据，包含 ds, y, load, wind, solar, interconnect
        
        Returns
        -------
        DataFrame
            添加了特征列的数据
        """
        df = df.copy()
        
        # ★ 核心：1秒偏移法定义业务时间
        # 物理 00:00 减去 1 秒后，会变成前一天的 23:59:59
        # 这样 hour+1 后，00:00 就变成了前一天的 24:00
        adjusted_time = df['ds'] - pd.Timedelta(seconds=1)
        
        # 1. 基础时间特征 (1-24小时制)
        df['hour'] = adjusted_time.dt.hour + 1   # 0-23 -> 1-24
        df['month'] = adjusted_time.dt.month
        df['day_of_week'] = adjusted_time.dt.dayofweek  # 0=周一, 6=周日
        df['is_weekend'] = df['day_of_week'].isin([5, 6]).astype(int)
        
        # 2. 滞后特征（基于物理行偏移，48h/168h 是安全的）
        lag_step_2day = 48    # 2天 = 48小时
        lag_step_7day = 168   # 7天 = 168小时
        
        df['lag_48h'] = df['y'].shift(lag_step_2day)
        df['lag_168h'] = df['y'].shift(lag_step_7day)
        
        # 智能滞后选择：工作日用上周同期，非工作日用2天前
        # 基于调整后的日期判断工作日（24点属于前一天）
        df['lag_price_target'] = np.where(
            df['day_of_week'] < 5,   # 周一到周五（0-4）
            df['lag_168h'],           # 用上周同期
            df['lag_48h']             # 非工作日用2天前
        )
        df['lag_price_week'] = df['lag_168h']  # 始终用上周同期
        
        # 填充缺失值
        df['lag_price_target'] = df['lag_price_target'].ffill().fillna(0)
        df['lag_price_week'] = df['lag_price_week'].ffill().fillna(0)

        # 3. 物理特征
        safe_load = df['load'].replace(0, 1)  # 避免除零
        df['net_load'] = df['load'] - df['wind'] - df['solar']  # 净负荷
        df['solar_ratio'] = df['solar'] / safe_load  # 光伏占比
        df['net_load_sq'] = (df['net_load'] / 1000) ** 2  # 净负荷平方（缩放）
        df['bidding_space'] = df['net_load'] - df['interconnect']  # 竞价空间
        df['space_ratio'] = df['bidding_space'] / safe_load  # 竞价空间占比
        df['wind_ratio'] = df['wind'] / safe_load  # 风电占比
        df['renew_penetration'] = (df['wind'] + df['solar']) / safe_load  # 新能源渗透率
        df['ramp_load'] = df['load'].diff().fillna(0)  # 负荷变化率
        df['ramp_solar'] = df['solar'].diff().fillna(0)  # 光伏变化率
        
        # 4. D日最新信息特征（基于业务日期聚合）
        df['date_only'] = adjusted_time.dt.date
        
        # 统计业务小时 1-15 点（物理 00:00 - 14:00）的数据
        mask_morning = (df['hour'] >= 1) & (df['hour'] <= 15)
        df_morning = df[mask_morning].copy()
        
        def calc_trend(x):
            """计算序列趋势（最后-最先）"""
            return x.iloc[-1] - x.iloc[0] if len(x) >= 2 else 0

        # 基础统计：均值、标准差
        stats_basic = df_morning.groupby('date_only')['y'].agg(
            morning_mean='mean',
            morning_std='std'
        )
        
        # 中午统计：业务 11-15 点（物理 10:00 - 14:00）的最小值和趋势
        mask_noon = (df_morning['hour'] >= 11) & (df_morning['hour'] <= 15)
        stats_noon = df_morning[mask_noon].groupby('date_only')['y'].agg(
            noon_min='min',
            morning_trend=calc_trend
        )
        
        # 合并统计特征
        daily_feats = pd.concat([stats_basic, stats_noon], axis=1).reset_index()
        
        # Shift 1天：预测 D+1 天时只能用 D 天的统计量
        cols_to_shift = ['morning_mean', 'noon_min', 'morning_std', 'morning_trend']
        daily_feats[cols_to_shift] = daily_feats[cols_to_shift].shift(1)
        
        # 标记数据是否新鲜（当天是否有统计值）
        daily_feats['is_info_fresh'] = daily_feats['morning_mean'].notna().astype(int)
        
        # 填充缺失值
        daily_feats[cols_to_shift] = daily_feats[cols_to_shift].ffill().fillna(0)
        
        # 合并到主数据
        df = df.merge(daily_feats, on='date_only', how='left')
        
        # 清理临时列
        df = df.drop(columns=['date_only', 'lag_48h', 'lag_168h'])
        return df

    def validate_optimize_dataset(self, test_df_raw, test_start_date, test_end_date=None):
        """
        验证寻优数据集的质量
        
        检查标签和特征是否存在缺失值，确保数据质量。
        
        Parameters
        ----------
        test_df_raw : DataFrame
            测试集数据
        test_start_date : str
            测试开始时间
        test_end_date : str, optional
            测试结束时间
        
        Raises
        ------
        ValueError
            如果数据存在质量问题（空集、标签缺失、特征缺失）
        """
        if test_df_raw.empty:
            raise ValueError("寻优数据检查失败：测试集为空")

        # 检查标签缺失
        y_nan_mask = test_df_raw['y'].isna()
        y_nan_count = int(y_nan_mask.sum())
        if y_nan_count > 0:
            bad_times = test_df_raw.loc[y_nan_mask, 'ds'].head(5).dt.strftime('%Y-%m-%d %H:%M:%S').tolist()
            window_desc = f"{test_start_date} -> {test_end_date}" if test_end_date else f">= {test_start_date}"
            raise ValueError(
                f"寻优数据检查失败：测试窗口 {window_desc} 的标签列 y 存在 {y_nan_count} 个 NaN，示例时刻: {bad_times}"
            )

        # 检查特征缺失
        feature_na = test_df_raw[self.features_list].isna().sum()
        bad_feature_na = feature_na[feature_na > 0]
        if not bad_feature_na.empty:
            detail = ", ".join([f"{col}:{int(cnt)}" for col, cnt in bad_feature_na.items()])
            window_desc = f"{test_start_date} -> {test_end_date}" if test_end_date else f">= {test_start_date}"
            raise ValueError(
                f"寻优数据检查失败：测试窗口 {window_desc} 的特征列存在 NaN，{detail}"
            )
        
    def optimize_data_window(self, file_path, test_start_date, test_end_date=None, 
                            step_months=6, target='实时电价', raw_df=None):
        """
        动态训练窗口寻优
        
        尝试不同的训练窗口（2个月、4个月、6个月...），在验证集上评估，
        选择 sMAPE 最低的模型。
        
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
        raw_df : DataFrame, optional
            预加载的数据，如果提供则不再从文件加载
        
        Returns
        -------
        dict
            最佳结果，包含：
            - months_back: 训练窗口月数
            - train_start: 训练开始时间
            - mae: 验证集 MAE
            - smape: 验证集 sMAPE
            - model: 训练好的 ThreeStageLGBM 模型
        """
        # 加载和特征工程
        raw_df = raw_df.copy() if raw_df is not None else self.load_and_process_data(file_path, target)
        full_df = self.feature_engineering(raw_df)
        
        # 划分验证集
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

        # 数据质量验证
        self.validate_optimize_dataset(test_df_raw, test_start_date, test_end_date)

        # 打印表头
        print(f"\n{'Window':<10} | {'MAE':<8} | {'sMAPE':<10} | {'NegRecall':<10} | {'Time':<8}")
        
        results = []
        data_min_date = full_df['ds'].min()
        months_back = 12  # 从12个月开始
        
        # 时段定义
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
            
            # 训练集太小则跳过
            if len(train_df) < 2000:
                if is_limit: 
                    break
                months_back += step_months
                continue

            # 标签截断（处理极端值）
            train_upper = train_df['y'].quantile(0.995)
            train_df['y_clipped'] = train_df['y'].clip(lower=-100, upper=train_upper)

            t0 = time.time()
            
            # ==================== A. Valley Model ====================
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

            # ==================== B. Solar Model ====================
            train_solar = train_df[train_df['hour'].isin(solar_hours)]
            test_solar = test_df_raw[test_df_raw['hour'].isin(solar_hours)]
            
            # 样本权重：负电价样本权重更高
            w_solar = np.ones(len(train_solar))
            y_solar_val = train_solar['y_clipped'].values
            w_solar[y_solar_val < 50] = 2   # 低电价
            w_solar[y_solar_val < 0] = 5    # 负电价
            
            # Solar回归模型
            model_solar_reg = lgb.LGBMRegressor(
                objective='regression',
                n_estimators=3000,
                learning_rate=0.03,
                num_leaves=63,  # 更多叶子节点，拟合更复杂模式
                n_jobs=self.lgbm_n_jobs,
                device_type=self._device_type(),
                verbose=-1,
                random_state=42
            )
            model_solar_reg = self._fit_with_cuda_fallback(
                model_solar_reg,
                train_solar[self.features_list],
                train_solar['y_clipped'],
                sample_weight=w_solar,  # 加权训练
                eval_set=[(test_solar[self.features_list], test_solar['y'])],
                eval_metric='l1',
                callbacks=[lgb.early_stopping(100, verbose=False), lgb.log_evaluation(0)]
            )
            
            # Solar分类模型（预测是否负电价）
            y_solar_class = (y_solar_val < 0).astype(int)
            y_solar_test_class = (test_solar['y'] < 0).astype(int)
            
            model_solar_clf = lgb.LGBMClassifier(
                objective='binary',
                n_estimators=1000,
                learning_rate=0.05,
                class_weight='balanced',  # 平衡正负样本
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

            # ==================== C. Peak Model ====================
            train_peak = train_df[train_df['hour'].isin(peak_hours)]
            test_peak = test_df_raw[test_df_raw['hour'].isin(peak_hours)]
            
            # 样本权重：高风电时段权重更高
            w_peak = np.ones(len(train_peak))
            high_wind_threshold = train_peak['wind'].quantile(0.8)
            w_peak[train_peak['wind'] > high_wind_threshold] = 3 
            
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
                sample_weight=w_peak,
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

            # 在验证集上评估
            X_test_all = test_df_raw[self.features_list]
            pred = combined_model.predict(X_test_all)
            pred = np.where(pred < -80, -80, pred)  # 截断极端负值

            mae = mean_absolute_error(test_df_raw['y'], pred)
            smape = self.calculate_smape(test_df_raw['y'], pred)
            
            # 负电价召回率
            neg_mask = test_df_raw['y'] < 0
            neg_recall = (pred[neg_mask] < 0).sum() / neg_mask.sum() * 100 if neg_mask.sum() > 0 else 0

            print(f"{months_back:<10} | {mae:<8.2f} | {smape:<10.2f} | {neg_recall:<10.2f}% | {t1-t0:<8.2f}")
            
            results.append({
                'months_back': months_back if not is_limit else 'All Data',
                'train_start': train_start_dt,
                'mae': mae,
                'smape': smape,
                'model': combined_model
            })

            if is_limit: 
                break
            months_back += step_months

        # 保存最佳模型
        best_result = self.save_model(results, target)
        return best_result

    def save_model(self, results, target):
        """
        保存最佳模型到文件
        
        Parameters
        ----------
        results : list
            各窗口的训练结果列表
        target : str
            目标列名（用于构建文件名）
        
        Returns
        -------
        dict
            最佳结果
        """
        if not results: 
            return None
        
        df_res = pd.DataFrame(results)
        
        # 找到最大月份数
        max_month = df_res[df_res['months_back'] != 'All Data']['months_back'].max()
        if pd.isna(max_month): 
            max_month = 12
        
        # 选择 sMAPE 最低的模型
        best_idx = df_res['smape'].idxmin()
        best_row = df_res.loc[best_idx]
        
        # 构建保存路径
        PROJECT_ROOT = os.getenv("PROJECT_ROOT", ".")
        LightGBM_MODEL_PATH = os.getenv("LightGBM_MODEL_PATH", "models/LightGBM/best_model_{}.pkl")
        best_model_path = os.path.join(PROJECT_ROOT, LightGBM_MODEL_PATH.format(target))
        
        # 确保目录存在
        model_dir = os.path.dirname(best_model_path)
        os.makedirs(model_dir, exist_ok=True)
        
        # 删除旧模型（避免权限问题）
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
    """主程序入口：用于测试训练功能"""
    from dotenv import load_dotenv
    import os

    load_dotenv()

    PROJECT_ROOT = os.getenv("PROJECT_ROOT", ".")
    DATA_SET_NAME = os.getenv("DATA_SET_NAME")
    data_path = os.path.join(PROJECT_ROOT, DATA_SET_NAME)
    
    predictor = LGBMPowerPredictor()
    
    # 测试窗口寻优
    predictor.optimize_data_window(
        file_path=data_path,
        test_start_date='2026-01-02 01:00:00',
        test_end_date='2026-02-01 14:00:00',
        step_months=2,
        target='实时电价'
    )
