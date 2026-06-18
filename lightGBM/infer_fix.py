import pandas as pd
import numpy as np
import joblib
import warnings
import os
from sklearn.metrics import mean_absolute_error 

warnings.filterwarnings('ignore')

# =========================================================
# ★★★ 必须保留 ThreeStageLGBM 类定义 (Joblib 加载需要) ★★★
# =========================================================
class ThreeStageLGBM:
    def __init__(self, valley_reg, solar_reg, solar_clf, peak_reg):
        self.valley_reg = valley_reg
        self.solar_reg = solar_reg
        self.solar_clf = solar_clf
        self.peak_reg = peak_reg
        
        # 修正：小时定义改为 1-24 习惯
        self.valley_hours = [1, 2, 3, 4, 5, 6, 7, 8]
        self.solar_hours = [9, 10, 11, 12, 13, 14, 15, 16]
        self.peak_hours = [17, 18, 19, 20, 21, 22, 23, 24]

    def predict(self, X):
        preds = np.zeros(len(X))
        if not isinstance(X, pd.DataFrame):
             raise ValueError("推理时必须传入 pandas.DataFrame")
        if 'hour' not in X.columns:
             raise ValueError("输入数据 X 缺少 'hour' 列")

        valley_mask = X['hour'].isin(self.valley_hours)
        solar_mask = X['hour'].isin(self.solar_hours)
        peak_mask = X['hour'].isin(self.peak_hours)
        
        if valley_mask.sum() > 0:
            preds[valley_mask] = self.valley_reg.predict(X[valley_mask])
        if peak_mask.sum() > 0:
            preds[peak_mask] = self.peak_reg.predict(X[peak_mask])
        if solar_mask.sum() > 0:
            X_solar = X[solar_mask]
            solar_preds = self.solar_reg.predict(X_solar)
            neg_probs = self.solar_clf.predict_proba(X_solar)[:, 1]
            correction_mask = (neg_probs > 0.6) & (solar_preds > -20)
            solar_preds[correction_mask] = solar_preds[correction_mask] - 100
            preds[solar_mask] = solar_preds
        return preds
    def fit(self, X, y): pass

# =========================================================
# 推理主类 ( 1-24点 逻辑版)
# =========================================================
class PowerInference:
    def __init__(self, model_path):
        if model_path is not None:
            print(f"正在加载模型: {model_path} ...")
            if not os.path.exists(model_path):
                print(f"警告：找不到模型文件 {model_path}")
                return 
            try:
                self.model = joblib.load(model_path)
                print("模型加载成功！")
            except Exception as e:
                print(f"模型加载失败: {str(e)}")
        else:
            print("初始化推理类（待后续手动注入模型）...")
        
        self.features_list = [
            'hour', 'month', 'day_of_week', 'is_weekend',
            'lag_price_target', 'lag_price_week',
            'load', 'wind', 'solar', 'interconnect',
            'bidding_space', 'space_ratio',
            'net_load', 'solar_ratio', 'net_load_sq',
            'wind_ratio', 'renew_penetration', 'ramp_load', 'ramp_solar',
            'morning_mean', 'noon_min', 'morning_std', 'morning_trend', 'is_info_fresh'
        ]

    def get_last_actual_business_day(self, raw_df):
        actual_df = raw_df.dropna(subset=['y'])
        if actual_df.empty:
            raise ValueError("原始数据中不存在可用的实时电价历史值")
        last_actual_dt = actual_df['ds'].max()
        return (last_actual_dt - pd.Timedelta(seconds=1)).normalize()

    def calculate_smape(self, y_true, y_pred):
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

    def load_and_process_data(self, file_path, target='实时电价'):
        if file_path.endswith('.xlsx'):
            try:
                df = pd.read_excel(file_path, engine='openpyxl')
            except Exception as e:
                print(f"Excel 文件加载失败: {str(e)}")
                raise
        else:
            try:
                df = pd.read_csv(file_path, encoding='gbk')
            except:
                df = pd.read_csv(file_path, encoding='utf-8')
        time_col = '时刻'
        price_col = target
        load_col = '直调负荷预测值'
        wind_col = '风电总加预测值'
        solar_col = '光伏总加预测值'
        inter_col = '联络线受电负荷预测值'

        df['ds'] = pd.to_datetime(df[time_col], errors='coerce')
        df['y'] = pd.to_numeric(df[price_col], errors='coerce') if price_col else np.nan
        df['load'] = pd.to_numeric(df[load_col], errors='coerce').ffill()
        df['wind'] = pd.to_numeric(df[wind_col], errors='coerce').ffill()
        df['solar'] = pd.to_numeric(df[solar_col], errors='coerce').ffill()
        df['interconnect'] = pd.to_numeric(df[inter_col], errors='coerce').ffill()
        return df.sort_values('ds').reset_index(drop=True)

    def feature_engineering(self, df):
        """
        特征工程  (1-24点 逻辑修正版)
        """
        df = df.copy()
        
        # ★ 修正重点：使用“减1秒”逻辑提取时间特征，确保 00:00 归为前一天的 24 点
        feature_time = df['ds'] - pd.Timedelta(seconds=1)
        
        # 1. 基础时间特征 (改为 1-24)
        df['hour'] = feature_time.dt.hour + 1
        df['month'] = feature_time.dt.month
        df['day_of_week'] = feature_time.dt.dayofweek
        df['is_weekend'] = df['day_of_week'].isin([5, 6]).astype(int)
        
        # 2. 滞后特征 (基于原始顺序，移除 bfill)
        lag_step_2day = 48   
        lag_step_7day = 168  
        
        df['lag_48h'] = df['y'].shift(lag_step_2day)
        df['lag_168h'] = df['y'].shift(lag_step_7day)
        
        # 基于调整后的日期判断工作日（24点属于前一天）
        mask_target_is_workday = df['day_of_week'].isin([0, 1, 2, 3, 4])
        df['lag_price_target'] = np.where(mask_target_is_workday, df['lag_168h'], df['lag_48h'])
        df['lag_price_week'] = df['lag_168h']
        
        df['lag_price_target'] = df['lag_price_target'].ffill().fillna(0)
        df['lag_price_week'] = df['lag_price_week'].ffill().fillna(0)

        # 3. 物理特征 (load/wind等直接对应时刻，无需偏移)
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
        
        # 4. D日最新信息特征 (同步调整后的日期)
        df['date_only'] = feature_time.dt.date
        mask_morning = (df['hour'] >= 1) & (df['hour'] <= 15) # 这里的 hour 是 1-24
        df_morning = df[mask_morning].copy()
        
        def calc_trend(x):
            if len(x) < 2: return 0
            return x.iloc[-1] - x.iloc[0]

        stats_basic = df_morning.groupby('date_only')['y'].agg(
            morning_mean='mean',
            morning_std='std'
        )
        mask_noon = (df_morning['hour'] >= 11) & (df_morning['hour'] <= 15)
        stats_noon = df_morning[mask_noon].groupby('date_only')['y'].agg(
            noon_min='min',
            morning_trend=calc_trend
        )
        daily_feats = pd.concat([stats_basic, stats_noon], axis=1).reset_index()

        cols_to_shift = ['morning_mean', 'noon_min', 'morning_std', 'morning_trend']
        daily_feats[cols_to_shift] = daily_feats[cols_to_shift].shift(1)
        daily_feats['is_info_fresh'] = daily_feats['morning_mean'].notna().astype(int)
        daily_feats[cols_to_shift] = daily_feats[cols_to_shift].ffill().fillna(0)
        daily_feats['is_info_fresh'] = daily_feats['is_info_fresh'].fillna(0)
        
        df = df.merge(daily_feats, on='date_only', how='left')
        
        # 清洗
        df['morning_mean'] = df['morning_mean'].fillna(0)
        df['morning_std'] = df['morning_std'].fillna(0)
        df['morning_trend'] = df['morning_trend'].fillna(0)
        df['noon_min'] = df['noon_min'].fillna(0)
        df['is_info_fresh'] = df['is_info_fresh'].fillna(0)
        
        df = df.drop(columns=['date_only', 'lag_48h', 'lag_168h'])
        return df

    def predict_range(
        self,
        file_path,
        start_time,
        end_time,
        target='实时电价',
        raw_df=None,
        use_predicted_temp=False,
        update_history_with_predictions=False
    ):
        source_df = raw_df.copy() if raw_df is not None else self.load_and_process_data(file_path, target)
        start_dt, end_dt = pd.to_datetime(start_time), pd.to_datetime(end_time)

        if use_predicted_temp:
            info_cutoff_dt = start_dt - pd.Timedelta(seconds=1)
        else:
            # 默认沿用原逻辑：预测 D+1 时可使用 D 日 14:00 前临时值
            info_cutoff_dt = start_dt - pd.Timedelta(hours=11)
        
        # 备份真实值
        truth_df = source_df[(source_df['ds'] >= start_dt) & (source_df['ds'] <= end_dt)][['ds', 'y']].copy()
        truth_df.rename(columns={'y': 'y_true_backup'}, inplace=True)
        
        # 屏蔽未来数据进行特征计算
        feature_df = source_df.copy()
        feature_df.loc[feature_df['ds'] > info_cutoff_dt, 'y'] = np.nan
        full_df = self.feature_engineering(feature_df)
        target_df = full_df[(full_df['ds'] >= start_dt) & (full_df['ds'] <= end_dt)].copy()

        if len(target_df) == 0: return print("未找到数据")
        
        # 预测
        preds = self.model.predict(target_df[self.features_list])
        target_df['pred_y'] = np.where(preds < -80, -80, preds)
        
        # 还原真实值
        target_df = target_df.merge(truth_df, on='ds', how='left')
        target_df['y'] = target_df['y_true_backup']
        
        # 统计每日误差 (基于修正后的 hour)
        has_real = target_df['y'].notna().sum() > 0
        if has_real:
            print("\n" + "="*50)
            print(f"{'业务日期':<12} | {'MAE':<8} | {'sMAPE(%)':<10} | {'样本数':<6}")
            print("-" * 50)
            
            # 使用修正后的日期进行展示（0点属于前一天）
            target_df['display_date'] = (target_df['ds'] - pd.Timedelta(seconds=1)).dt.date
            for date, group in target_df.groupby('display_date'):
                valid_group = group.dropna(subset=['y'])
                if len(valid_group) > 0:
                    d_mae = mean_absolute_error(valid_group['y'], valid_group['pred_y'])
                    d_smape = self.calculate_smape(valid_group['y'], valid_group['pred_y'])
                    print(f"{str(date):<12} | {d_mae:<8.2f} | {d_smape:<10.2f} | {len(valid_group):<6}")
            
            all_valid = target_df.dropna(subset=['y'])
            total_mae = mean_absolute_error(all_valid['y'], all_valid['pred_y'])
            total_smape = self.calculate_smape(all_valid['y'], all_valid['pred_y'])
            print("-" * 50)
            print(f"{'总体平均':<12} | {total_mae:<8.2f} | {total_smape:<10.2f} | {len(all_valid):<6}")
            print("="*50 + "\n")

        if update_history_with_predictions:
            updated_raw_df = source_df.copy()
            updated_raw_df.loc[
                updated_raw_df['ds'].isin(target_df['ds']),
                'y'
            ] = target_df['pred_y'].to_numpy()
            return target_df, updated_raw_df

        return target_df
    

if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()
    target="实时电价"
    start_t = '2026-02-02 01:00:00'
    end_t   = '2026-02-03 00:00:00' 
    PROJECT_ROOT = os.getenv("PROJECT_ROOT", ".")
    DATA_SET_NAME = os.getenv("DATA_SET_NAME")
    LightGBM_MODEL_PATH = os.getenv("LightGBM_MODEL_PATH", "models/LightGBM/best_model_{}.pkl")
    best_model_path = os.path.join(PROJECT_ROOT, LightGBM_MODEL_PATH.format(target))
    model_file = best_model_path
    data_file = os.path.join(PROJECT_ROOT, DATA_SET_NAME)
    infer = PowerInference(model_file)
    
    # res = infer.predict_range(data_file, start_t, end_t, target=target)
    # res.to_csv("infer_results.csv", index=False)

    print(infer.predict_range(data_file, start_t, end_t, target=target))
