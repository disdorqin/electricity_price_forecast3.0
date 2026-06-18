# Codex 执行提示词：修复 TimesFM 融合阻断 + 跑通双模式全链路

## GOAL

让 fusion 链路在 TimesFM 不可用时自动跳过（不崩溃），然后跑通 Mode A 和 Mode B 的完整端到端验证。

## 约束

- Windows 环境，中文路径
- Conda 环境：`epf-2`，Python：`D:\computer_download\environment\conda\epf-2\python.exe`
- 项目根目录：`D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0`

## 阶段 1：修复 TimesFM 融合容错（4 处改动）

### 1.1 `fusion/runners/run_timesfm_export.py` — 缓存未命中写空 CSV

找到 `raise RuntimeError("TimesFM cached export not found...")` 那段代码，替换为：

```python
import sys, logging
logger = logging.getLogger(__name__)

# 替换 raise RuntimeError
if not cached_found:
    logger.warning("TimesFM: no cached predictions for requested window, writing empty export")
    import pandas as pd
    empty_df = pd.DataFrame(columns=["时刻", "预测值", "真实值"])
    empty_df.to_csv(output_path, index=False)
    sys.exit(0)
```

具体实现请根据现有代码结构调整，关键是：缓存未命中时写一个空 CSV 然后正常退出（exit code 0），而不是 raise。

### 1.2 `fusion/pipeline_common.py` — 添加 `--disable-timesfm` + 单模型容错

a) 在 `build_model_specs()` 中添加 timesfm 跳过逻辑（参照已有的 `--disable-lightgbm`）：
```python
if getattr(args, 'disable_timesfm', False):
    pool = [m for m in pool if m != "timesfm"]
```

b) 在 `_run_specs_with_scheduler()` 中，为每个 spec 的执行加 try/except：
```python
for spec in gpu_specs:
    try:
        run_one(spec)
    except Exception as e:
        logger.warning(f"Model {spec.model_name} failed in {phase}: {e}, skipping")
        continue
```

注意：CPU 路径（ProcessPoolExecutor）也需要类似处理。

### 1.3 `cli/parser.py` — 添加 `--disable-timesfm` 参数

```python
parser.add_argument("--disable-timesfm", action="store_true", default=False,
                    help="Skip TimesFM model in fusion pipeline")
```

### 1.4 `fusion/run_fixed_window_fusion.py` — adapter 加载容错

在 `_load_normalized_predictions()` 中为每个 adapter.load() 加 try/except：
```python
for artifact in artifacts:
    try:
        adapter_cls = get_adapter(artifact.adapter)
        adapter = adapter_cls(str(artifact.source), **artifact.adapter_kwargs)
        df = adapter.load().copy()
        df["model_name"] = artifact.model_name
        frames.append(df)
    except (FileNotFoundError, pd.errors.EmptyDataError, KeyError) as e:
        logger.warning(f"Skipping {artifact.model_name}: {e}")
        continue
if not frames:
    raise RuntimeError("No model predictions available after loading")
```

## 阶段 2：跑通 Mode A（纯融合）

```bash
cd "D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0"
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline fusion --target both --start 2026-05-05 --end 2026-05-07
```

这个日期范围内 TimesFM 有缓存（2026-05-01 ~ 2026-05-10），即使容错修复不完美也能用缓存跑过。

确认产出并打印：
- `weights.csv` 内容
- DA/RT 各模型 SMAPE
- `joint_report/final_truth_vs_fusion.csv` 的前几行

如果有报错，修复到跑通为止。

## 阶段 3：跑通 Mode B（融合 + 分类器）

先检查分类器数据范围：
```bash
D:\computer_download\environment\conda\epf-2\python.exe -c "
import pandas as pd
df = pd.read_excel('ExtremPriceClf/data/260525.xlsx')
print('Date range:', df['时刻'].min(), '~', df['时刻'].max())
"
```

然后用合适的日期范围测试：
```bash
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline fusion --target realtime --start 2026-05-20 --end 2026-05-25 --use-classifier
```

确认产出：
- `realtime/fused_predictions.csv`（原始融合）
- `realtime/fused_predictions_corrected.csv`（分类器修正）
- `classifier/` 目录下的中间文件
- 打印：分类器修正了多少小时

如果分类器报错（比如依赖缺失），先安装缺失包再重试。

## 阶段 4：Mode B 综合测试

```bash
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline fusion --target both --start 2026-05-20 --end 2026-05-25 --use-classifier
```

## 阶段 5：dayahead + classifier 警告测试

```bash
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline fusion --target dayahead --start 2026-05-20 --end 2026-05-25 --use-classifier
```

确认打印 "ExtremPriceClf only applies to realtime" 警告，DA 融合正常产出。

## 交付

1. 4 处修复的代码（文件或 diff）
2. Mode A 烟测：完整日志 + weights.csv 内容 + SMAPE 汇总表
3. Mode B 烟测：分类器修正小时数 + corrected CSV 前几行
4. 综合测试和警告测试的日志
5. 任何未解决问题的说明
