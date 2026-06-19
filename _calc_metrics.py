import numpy as np
import pandas as pd

pred_path = r'd:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0\ExtremPriceClf\data\融合模型预测电价数据.xlsx'
df = pd.read_excel(pred_path)
df['时刻'] = pd.to_datetime(df['时刻'])

y_true = df['实时电价'].to_numpy(float)
y_pred = df['预测实时电价'].to_numpy(float)
n = len(y_true)

# 常规指标
mae = np.mean(np.abs(y_pred - y_true))
mse = np.mean((y_pred - y_true) ** 2)
mape = np.mean(np.abs((y_pred - y_true) / y_true)) * 100
r2 = 1 - np.sum((y_true - y_pred) ** 2) / np.sum((y_true - np.mean(y_true)) ** 2)

# SMAPE with clip50 (from docs/metrics_calculation.md)
y_clip = np.maximum(y_true, 50)
y_pred_clip = np.maximum(y_pred, 50)
smape = np.mean(
    np.abs(y_pred_clip - y_clip) / ((np.abs(y_pred_clip) + np.abs(y_clip)) / 2)
) * 100
accuracy = 100 - smape

print('==== 实时电价预测指标 ====')
print(f'样本数: {n}')
print(f'MAE:   {mae:.4f}')
print(f'MSE:   {mse:.4f}')
print(f'MAPE:  {mape:.2f}%')
print(f'R2:    {r2:.4f}')
print(f'SMAPE (clip50): {smape:.2f}%')
print(f'Accuracy (1 - SMAPE): {accuracy:.2f}%')

# 保存结果
result = pd.DataFrame({
    '时刻': df['时刻'],
    '预测实时电价': y_pred,
    '实时电价': y_true,
    '预测clip': y_pred_clip,
    '真实clip': y_clip,
    '绝对误差': np.abs(y_pred - y_true),
    'SMAPE分量': np.abs(y_pred_clip - y_clip) / ((np.abs(y_pred_clip) + np.abs(y_clip)) / 2) * 100
})
out_path = r'd:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0\ExtremPriceClf\data\实时电价预测指标明细.xlsx'
result.to_excel(out_path, index=False)
print(f'明细已保存: {out_path}')
