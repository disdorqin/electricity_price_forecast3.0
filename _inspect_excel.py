import pandas as pd

p = r'd:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0\ExtremPriceClf\data\融合模型预测电价数据.xlsx'
df = pd.read_excel(p)
print('Shape:', df.shape)
print('Columns:', list(df.columns))
print('Head:')
print(df.head(10).to_string())
print('Tail:')
print(df.tail(10).to_string())
