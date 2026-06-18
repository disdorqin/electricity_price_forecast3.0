# Codex 执行提示词：修复 TimeMixer 输出路径 + 跑通全链路

## GOAL

修复 TimeMixer fusion runner 的输出路径 bug，让 `fusion --target both` 端到端跑通，然后测试 `--use-classifier` 模式。

## 约束

- Windows 环境，中文路径
- Conda 环境：`epf-2`，Python：`D:\computer_download\environment\conda\epf-2\python.exe`
- 项目根目录：`D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0`

## 阶段 1：修复 TimeMixer 输出路径（核心阻断）

### 问题

`fusion/runners/run_timemixer_enhanced_export.py` 启动 subprocess 时设置了 `cwd=TimeMixer/`，但传给 subprocess 的 `--output-dir` 是相对路径（如 `fusion_runs\unified_entry\dayahead_run\simulation\timemixer`）。因为 cwd 变了，相对路径解析到了 `TimeMixer/fusion_runs/...` 而不是项目根目录的 `fusion_runs/...`。

实际文件位置：`TimeMixer/fusion_runs/unified_entry/dayahead_run/simulation/timemixer/predictions_day_ahead_last_month.csv`
期望文件位置：`fusion_runs/unified_entry/dayahead_run/simulation/timemixer/predictions_day_ahead_last_month.csv`

### 修复方案

在 `fusion/runners/run_timemixer_enhanced_export.py` 中，构建 subprocess command 之前，将所有路径参数转为绝对路径：

```python
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 在 passthrough 参数处理时，特殊处理路径类参数
path_fields = {"output_dir", "save_checkpoint", "data_path"}
for field in passthrough_fields:
    value = getattr(args, field, None)
    if value is None:
        continue
    if field in path_fields:
        p = Path(value)
        if not p.is_absolute():
            value = str((PROJECT_ROOT / p).resolve())
    command.extend([f"--{field.replace('_', '-')}", str(value)])
```

同时检查 `--candidate-config` 是否也需要绝对化（当前默认路径已经是绝对的，但用户传入的可能是相对路径）。

### 清理

删除误写的目录：`TimeMixer/fusion_runs/`（整个目录）

## 阶段 2：修复其他小问题

### 2.1 executor 串行路径 None 泄漏

文件：`runners/executor.py`

```python
# 第 37-39 行，串行路径改为：
result = _run_task(task)
if result is not None:
    results.append(result)
```

### 2.2 conda-env 空字符串

文件：`services/fusion_service.py`

```python
# 第 47-48 行改为：
if conda_env:
    cmd.extend(["--conda-env", str(conda_env)])
# 不再传空字符串
```

## 阶段 3：重跑 Mode A 烟测

```bash
cd "D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0"
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline fusion --target both --start 2026-06-05 --end 2026-06-07
```

确认产出：
- `fusion_runs/unified_entry/dayahead_run/formal/*/` — DA 各模型预测 + fused_predictions.csv
- `fusion_runs/unified_entry/realtime_run/formal/*/` — RT 各模型预测 + fused_predictions.csv
- `fusion_runs/unified_entry/joint_report/final_truth_vs_fusion.csv`
- `fusion_runs/unified_entry/weights.csv`

如果有报错，修复到跑通为止。产出后打印融合权重和各模型 SMAPE。

## 阶段 4：检查分类器数据覆盖

```bash
D:\computer_download\environment\conda\epf-2\python.exe -c "
import pandas as pd
df = pd.read_excel('ExtremPriceClf/data/260525.xlsx')
print('Shape:', df.shape)
print('Columns:', list(df.columns))
print('Date range:', df['时刻'].min(), '~', df['时刻'].max())
"
```

如果数据只到 5 月，用 5 月的日期范围做 Mode B 测试：
```bash
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline fusion --target realtime --start 2026-05-20 --end 2026-05-25 --use-classifier
```

如果数据到 6 月，用：
```bash
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline fusion --target realtime --start 2026-06-01 --end 2026-06-05 --use-classifier
```

确认产出：
- `realtime/fused_predictions.csv`（原始融合）
- `realtime/fused_predictions_corrected.csv`（分类器修正）
- `classifier/` 目录下的中间文件

## 交付

1. Bug 1 修复代码（diff 或修改后的文件）
2. Mode A 烟测完整输出（成功/失败 + 错误信息）
3. 融合权重和 SMAPE 结果
4. 分类器数据覆盖范围
5. Mode B 烟测结果（如果数据允许）
