import pandas as pd
import numpy as np
import joblib
import warnings
import os
from sklearn.metrics import mean_absolute_error 

warnings.filterwarnings('ignore')


# =========================================================
# ★★★ 必须保留 ThreeStageLGBM 类定义 (与日前训练逻辑完全对齐) ★★★
# =========================================================
class ThreeStageLGBM:
    def __init__(self, valley_reg, solar_reg, solar_clf, peak_reg):
        self.valley_reg = valley_reg
        self.solar_reg = solar_reg
        self.solar_clf = solar_clf
        self.peak_reg = peak_reg
        
        # 同步日前业务小时划分 (1-24)
        self.valley_hours = [1, 2, 3, 4, 5, 6, 7, 8]
        self.solar_hours = [9, 10, 11, 12, 13, 14, 15, 16]
        self.peak_hours = [17, 18, 19, 20, 21, 22, 23, 24]

    def predict(self, X):
        preds = np.zeros(len(X))
        if not isinstance(X, pd.DataFrame):
             raise ValueError("推理时必须传入 pandas.DataFrame")
        
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
            # 同步训练端修正逻辑：日前阈值 0.7，修正 -80
            correction_mask = (neg_probs > 0.7) & (solar_preds > 0)
            solar_preds[correction_mask] = solar_preds[correction_mask] - 80
            preds[solar_mask] = solar_preds
        return preds
    def fit(self, X, y): pass

# =========================================================
# 日前推理主类 (Day-Ahead Inference 1-24h 版)
# =========================================================
class PowerInference:
    def __init__(self, model_path):
        if model_path is not None:
            print(f"正在加载日前模型: {model_path} ...")
            if not os.path.exists(model_path):
                print(f"警告：找不到模型文件 {model_path}")
                return 
            try:
                self.model = joblib.load(model_path)
                print("日前模型加载成功！")
            except Exception as e:
                print(f"模型加载失败: {str(e)}")
        else:
            print("初始化推理类（待后续手动注入模型）...")
        
        # 同步日前预测特征列表
        self.features_list = [
            'hour', 'month', 'day_of_week', 'is_weekend', 'hour_sin', 'hour_cos',
            'lag_price_target', 'price_rolling_mean_24h',
            'load', 'wind', 'solar', 'interconnect',
            'bidding_space', 'space_ratio',
            'net_load', 'solar_ratio', 'net_load_sq',
            'wind_ratio', 'renew_penetration', 'ramp_load', 'ramp_solar',
            'prev_day_avg', 'prev_day_max', 'prev_day_min'
        ]

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

    def load_and_process_data(self, file_path, target='日前电价'):
        if file_path.endswith('.xlsx'):
            try:
                df = pd.read_excel(file_path, engine='openpyxl')
            except Exception as e:
                print(f"Excel 文件加载失败: {str(e)}")
                raise
        else:
            try:
                df = pd.read_csv(file_path, encoding='gbk', on_bad_lines='skip')
            except:
                df = pd.read_csv(file_path, encoding='utf-8', on_bad_lines='skip')

        time_col = '时刻'
        price_col = target
        load_col = '直调负荷预测值'
        wind_col = '风电总加预测值'
        solar_col = '光伏总加预测值'
        inter_col = '联络线受电负荷预测值'

        if price_col not in df.columns:
            raise ValueError(f"输入数据缺少目标列: {price_col}")

        df['ds'] = pd.to_datetime(df[time_col], errors='coerce')
        df['y'] = pd.to_numeric(df[price_col], errors='coerce') if price_col else np.nan
        df['load'] = pd.to_numeric(df[load_col], errors='coerce').ffill()
        df['wind'] = pd.to_numeric(df[wind_col], errors='coerce').ffill()
        df['solar'] = pd.to_numeric(df[solar_col], errors='coerce').ffill()
        df['interconnect'] = pd.to_numeric(df[inter_col], errors='coerce').ffill()
        df = df.dropna(subset=['ds']).sort_values('ds').reset_index(drop=True)
        return df
      
    def feature_engineering(self, df):
        """
        日前特征工程：1秒偏移对齐版
        """
        df = df.copy()
        
        #  1秒偏移逻辑
        adjusted_time = df['ds'] - pd.Timedelta(seconds=1)
        
        # 1. 基础时间与周期 (1-24)
        df['hour'] = adjusted_time.dt.hour + 1
        df['month'] = adjusted_time.dt.month
        df['day_of_week'] = adjusted_time.dt.dayofweek
        df['is_weekend'] = df['day_of_week'].isin([5, 6]).astype(int)
        df['hour_sin'] = np.sin(2 * np.pi * (df['hour'] - 1) / 23)
        df['hour_cos'] = np.cos(2 * np.pi * (df['hour'] - 1) / 23)
        
        # 2. 日前滞后逻辑 (D-1 全天可用)
        df['lag_24h'] = df['y'].shift(24)
        df['lag_168h'] = df['y'].shift(168)
        
        # 策略滞后判定基于业务时间轴
        df['lag_price_target'] = np.where(df['day_of_week'] == 0, df['lag_168h'], df['lag_24h'])
        df['price_rolling_mean_24h'] = df['y'].shift(24).rolling(window=24).mean()
        
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
        
        # 4. 昨日全天统计量 (基于业务日期)
        df['date_only'] = adjusted_time.dt.date
        daily_stats = df.groupby('date_only')['y'].agg(
            prev_day_avg='mean',
            prev_day_max='max',
            prev_day_min='min'
        ).shift(1).reset_index()
        
        df = df.merge(daily_stats, on='date_only', how='left')
        df = df.drop(columns=['date_only', 'lag_24h', 'lag_168h'])
        return df.ffill().fillna(0)

    def predict_range(self, file_path, start_time, end_time, target='日前电价', raw_df=None):
        import os
        diag = open(os.path.join(os.path.dirname(__file__), '..', 'lgbm_predict_diag.log'), 'a', encoding='utf-8')
        def d(*a):
            print(*a, file=diag, flush=True)
        d(f"predict_range start {start_time} ~ {end_time}")
        if raw_df is None:
            raw_df = self.load_and_process_data(file_path, target)
        else:
            raw_df = raw_df.copy()
        start_dt, end_dt = pd.to_datetime(start_time), pd.to_datetime(end_time)
        d(f"raw_df len={len(raw_df)}, ds range={raw_df['ds'].min()} ~ {raw_df['ds'].max()}")
        
        # 备份真实值
        truth_df = raw_df[(raw_df['ds'] >= start_dt) & (raw_df['ds'] <= end_dt)][['ds', 'y']].copy()
        truth_df.rename(columns={'y': 'y_true_backup'}, inplace=True)
        d(f"truth_df len={len(truth_df)}")
        
        # 推理模拟
        raw_df.loc[raw_df['ds'] >= start_dt, 'y'] = np.nan
        full_df = self.feature_engineering(raw_df)
        d(f"full_df len={len(full_df)}, columns={list(full_df.columns)}")
        target_df = full_df[(full_df['ds'] >= start_dt) & (full_df['ds'] <= end_dt)].copy()
        d(f"target_df len={len(target_df)}")

        if len(target_df) == 0:
            d("target_df empty, returning None")
            diag.close()
            return print("未找到对应日期数据")
        
        # 执行推理
        d(f"predicting with features {self.features_list}")
        preds = self.model.predict(target_df[self.features_list])
        target_df['pred_y'] = np.where(preds < -80, -80, preds)
        d(f"preds done, shape={preds.shape}")
        
        # 还原真实值
        target_df = target_df.merge(truth_df, on='ds', how='left')
        target_df['y'] = target_df['y_true_backup']
        d(f"merged truth, y notna={target_df['y'].notna().sum()}")
        
        # 误差统计输出
        has_real = target_df['y'].notna().sum() > 0
        d(f"has_real={has_real}")
        if has_real:
            print("\n" + "="*50)
            print(f"{'业务日期':<12} | {'MAE':<8} | {'sMAPE(%)':<10} | {'样本数':<6}")
            print("-" * 50)
            
            # 使用业务日期分组展示 (0点属于前一天)
            target_df['business_date'] = (target_df['ds'] - pd.Timedelta(seconds=1)).dt.date
            for date, group in target_df.groupby('business_date'):
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

        d(f"returning target_df len={len(target_df)}")
        diag.close()
        return target_df

if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()
    target="日前电价"
    start_t = '2026-02-01 01:00:00'
    end_t   = '2026-02-02 00:00:00' 
    PROJECT_ROOT = os.getenv("PROJECT_ROOT", ".")
    DATA_SET_NAME = os.getenv("DATA_SET_NAME")
    LightGBM_MODEL_PATH = os.getenv("LightGBM_MODEL_PATH", "models/LightGBM/best_model_{}.pkl")
    best_model_path = os.path.join(PROJECT_ROOT, LightGBM_MODEL_PATH.format(target))
    model_file = best_model_path
    data_file = os.path.join(PROJECT_ROOT, DATA_SET_NAME)
    infer = PowerInference(model_file)
    
    res = infer.predict_range(data_file, start_t, end_t, target=target)
    res.to_csv("infer_results.csv", index=False)
    # print(infer.predict_range(data_file, start_t, end_t, target=target))