# 2.1 Ledger Smoke 测试运行指南

> Commit: `08761c8` | 仓库: https://github.com/disdorqin/electricity_forecast_model2.1

## 前置检查

在运行 smoke 之前，逐项确认：

```text
[ ] conda 环境 epf-2 存在:   conda activate epf-2
[ ] Python >= 3.10:           python --version
[ ] PyTorch + CUDA 可用:      python -c "import torch; print(torch.cuda.is_available())"
[ ] TimesFM 权重:             models/timesFM/config.json + model.safetensors 存在（共 ~886 MB）
[ ] 数据文件:                 data/shandong_pmos_hourly.xlsx 存在
[ ] EPF v1.0 仓库:            D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\epf 存在
[ ] EPF v1.0 LightGBM:        epf/lightGBM/lightGBM_oneday.py 存在
[ ] EPF v1.0 TimesFM:         epf/TF/infer.py 存在
```

## Step 1: 确认数据日期范围

```powershell
conda activate epf-2
cd "D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.1"

python -c "
import pandas as pd
df = pd.read_excel('data/shandong_pmos_hourly.xlsx')
df['_ts'] = pd.to_datetime(df['时刻'], errors='coerce')
print('Date range:', df['_ts'].min().date(), '->', df['_ts'].max().date())
# 找最后的完整业务日（至少 24 小时 actual）
latest = df['_ts'].max().date()
print('建议测试日期:', latest)
"
```

记下输出的日期 `D`，替换下面命令中的 `2026-02-24`。

## Step 2: 跑一天 smoke

```powershell
conda activate epf-2
cd "D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.1"

python main.py --pipeline ledger_smoke --date <D> `
  --data-path data/shandong_pmos_hourly.xlsx `
  --epf-v1-root "D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\epf" `
  --smoke-training-months 3 `
  --smoke-timemixer-epochs 3 `
  --smoke-timemixer-patience 1 `
  --max-cpu-workers 2 `
  --max-gpu-workers 1 `
  --force
```

**参数说明**：

| 参数 | 值 | 说明 |
|------|-----|------|
| `--pipeline ledger_smoke` | — | smoke 模式，只跑预测不跑权重/融合/分类器 |
| `--date <D>` | YYYY-MM-DD | 替换为数据中的最近完整业务日 |
| `--epf-v1-root` | EPF 1.0 路径 | LightGBM/TimesFM 依赖 |
| `--smoke-training-months` | 3 | 只训练最近 3 个月（加速） |
| `--smoke-timemixer-epochs` | 3 | TimeMixer 只训 3 个 epoch |
| `--smoke-timemixer-patience` | 1 | Early stop patience=1 |
| `--max-cpu-workers` | 2 | CPU 模型并行 2 个 |
| `--max-gpu-workers` | 1 | GPU 模型串行（防 OOM） |
| `--force` | — | 跳过缓存重跑 |

**输出目录**: `outputs/smoke/runs/{D}/` （独立，不污染 `outputs/runs/` 和 `outputs/ledger/`）

## Step 3: 验收检查

Smoke 跑完后逐项检查：

```text
[ ] outputs/smoke/runs/{D}/run_manifest.json 存在且 status = complete 或 complete_with_warnings
[ ] outputs/smoke/runs/{D}/smoke_report.json 存在且 smoke_status = PASS
[ ] outputs/smoke/runs/{D}/dayahead/prediction/all_model_predictions_long.csv = 72 行
[ ] outputs/smoke/runs/{D}/realtime/prediction/all_model_predictions_long.csv = 96 行
[ ] 每个模型单独 CSV: {model_name}_predictions.csv 各 24 行
[ ] lightgbm/dayahead = 24 rows, hour_business=1..24
[ ] timesfm/dayahead = 24 rows
[ ] timemixer/dayahead = 24 rows
[ ] timesfm/realtime = 24 rows
[ ] sgdfnet/realtime = 24 rows
[ ] timemixer/realtime = 24 rows
[ ] rt916/realtime = 24 rows
[ ] 所有行 business_day == D
[ ] hour 24 的 ds == D+1 00:00:00
[ ] 所有模型 y_pred 不全为 NaN
[ ] outputs/smoke/ledger/dayahead/prediction/prediction_ledger.parquet 存在
[ ] outputs/smoke/ledger/realtime/prediction/prediction_ledger.parquet 存在
[ ] TimesFM 在 CPU queue 运行（查看日志中 "[CPU] timesfm"）
[ ] TimeMixer 和 RT916 不同时跑 GPU（max_gpu_workers=1 保证串行）
```

## 验收通过后，进入正式 30 天 backfill

Smoke 全部通过后，再跑正式版：

```powershell
# 正式单日预测
python main.py --pipeline ledger_predict --date <D> `
  --data-path data/shandong_pmos_hourly.xlsx `
  --epf-v1-root "D:\..." `
  --force

# 30 天 backfill（过夜跑）
python main.py --pipeline ledger_backfill --start <D-30> --end <D-1> `
  --data-path data/shandong_pmos_hourly.xlsx `
  --epf-v1-root "D:\..."

# 权重学习
python main.py --pipeline ledger_weight --date <D>

# 融合
python main.py --pipeline ledger_fuse --date <D>

# 分类器
python main.py --pipeline ledger_classifier --date <D> --data-path data/shandong_pmos_hourly.xlsx

# 一键完整
python main.py --pipeline ledger_full --date <D> `
  --data-path data/shandong_pmos_hourly.xlsx `
  --epf-v1-root "D:\..."
```

## 常见问题

| 问题 | 解决 |
|------|------|
| `EPF v1 root not found` | 确认 `--epf-v1-root` 路径存在，或传 `--allow-v2-fallback` |
| `TimesFM model weights not available` | 下载权重到 `models/timesFM/` |
| `CUDA out of memory` | 确保 `--max-gpu-workers 1`，减小 `--timemixer-batch-size` 到 8 |
| TimeMixer 报错 `no data` | 确认数据覆盖到了目标日期前 12 个月 |
| SGDFNet `no predictions` | 训练数据不足 2160 行，增大 `--training-months` |
| RT916 BFloat16 错误 | 已自动设置 `OPTIM_AMP=0`，推理强制 FP32 |
