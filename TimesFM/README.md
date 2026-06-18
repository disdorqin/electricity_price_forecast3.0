# Times-FM

- 默认分3个时间段分别预测后拼接预测结果--segment-count 3

### 实时电价

- 回测（评估）

python price_forecast_copy_分时段预测.py --target 实时 --dump-csv --date-range 2026/01/06-2026/01/25

- 预测未来一天（不评估）

python price_forecast_copy_分时段预测.py --target 实时 --mode forecast  --dump-csv --forecast-date 2026/02/07

### 日前电价

- 回测（评估）

python price_forecast_copy_分时段预测.py --target 日前 --dump-csv --date-range 2026/01/06-2026/01/25

- 预测未来一天（不评估）

python price_forecast_copy_分时段预测.py --target 日前 --mode forecast  --dump-csv --forecast-date 2026/02/07