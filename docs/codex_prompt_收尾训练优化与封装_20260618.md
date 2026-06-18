执行提示词：链路收尾、训练加速与项目封装（2026-06-18）

你现在要继续接手项目：

`D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0`

运行环境：

- Conda：`epf-2`
- Python：`D:\computer_download\environment\conda\epf-2\python.exe`
- Windows 中文路径，注意 GBK 编码问题
- GPU：RTX 4060 Laptop 8GB

## GOAL

完成三件事：
1. **验证四阶段链路端到端可用**（DA + RT）
2. **训练时间优化**：在不降低预测精度的前提下减少不必要的训练
3. **项目封装**：对标 `../epf/` 项目结构，补齐配置文件、文档、CLI 增强

## 约束

- SMAPE clip50 是业务逻辑不可改
- D-1 15:00 cutoff 协议（D 日 RT 实际价格禁用，其他 D 日预测值可用）
- 所有模型通过 `pipelines/base.py` 的 `BaseModelPipeline` 接口暴露
- 输出格式统一为 `["时刻", "预测值"]` CSV
- 归档用 `_archive/` 不删除
- 不做大重构，做最小改动确保规范可用
- **每完成一个步骤必须跑验证命令确认结果**

## 阶段 1：验证四阶段链路

### 目标

用 30 天验证窗口跑通完整四阶段，确认所有模型产出。

### 执行命令

```powershell
cd "D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0"

# 阶段 1：模型训练 + 预测
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline model_stage --target both --date 2026-05-15

# 阶段 2：权重学习
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline learner_stage --target both --date 2026-05-15

# 阶段 3：融合
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline fuse_stage --target both --date 2026-05-15

# 阶段 4：分类器（仅 RT，可能 skipped 因数据覆盖不足）
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline classifier_stage --target realtime --date 2026-05-15
```

### 验证标准

- `daily_runs/2026-05-15/dayahead/model_outputs/` 下有 lightgbm、timesfm、timemixer 各一个目录，且含 `val_predictions.csv`（约 720 行 = 30天×24小时）和 `forecast_predictions.csv`（24 行）
- `daily_runs/2026-05-15/realtime/model_outputs/` 下有 timesfm、timemixer、sgdfnet、rt916 各一个目录
- `daily_runs/2026-05-15/dayahead/learner_outputs/weights.csv` 存在且含 3 个 period × 3 个模型的权重
- `daily_runs/2026-05-15/realtime/learner_outputs/weights.csv` 存在且含 3 个 period × 4 个模型的权重
- `daily_runs/2026-05-15/*/final/fused_predictions.csv` 存在且含 24 行非空 y_fused

### 应急策略

- 如果某个模型报错，不要让整个链路断掉——检查 `staged_pipeline.py` 的 try/except 是否正确捕获
- 如果 TimesFM 报 missing weights，正常跳过
- 如果 SGDFNet/TimeMixer 报 Excel 格式错误，检查是否已加 `engine='openpyxl'`
- 如果分类器 skipped 因数据日期不覆盖，用 `--clf-data` 指向主数据文件

## 阶段 2：训练时间优化

### 2.1 LightGBM RT → train-once（P0）

**文件**：`lightGBM/main_fix.py`

**当前问题**：`run_precision_simulation()` 函数在 while 循环内对每一天调用 `_fit_realtime_fixed_window()` 重新训练 4 个模型。训练窗口只滑动 24 行，模型变化极小。

**修改方案**：
仿照 `run_precision_simulation_da()` 的模式：
1. 在 while 循环外，用 `forecast_start` 作为训练截止日，调用一次 `_fit_realtime_fixed_window()`
2. 在 while 循环内，只做预测（用已训练的 `inference` 对象）
3. 训练截止日 = `forecast_start` 的 D-1 15:00（满足 cutoff 协议）

```python
def run_precision_simulation(data_path, forecast_start, forecast_end, target="实时电价",
                              use_predicted_temp=False, training_months=12, val_ratio=0.2):
    predictor = LGBMPowerPredictor()
    inference = PowerInference()
    requested_start_date = pd.to_datetime(forecast_start)
    current_target_date = requested_start_date
    end_target_date = pd.to_datetime(forecast_end)
    all_days_preds = []

    # 训练一次（截止日为 forecast_start 的 D-1 15:00）
    history_end_str = requested_start_date.strftime("%Y-%m-%d 00:00:00")
    history_start_str = (requested_start_date - pd.DateOffset(months=int(training_months))).strftime("%Y-%m-%d 01:00:00")
    raw_df = predictor.load_and_process_data(data_path)
    best_res = _fit_realtime_fixed_window(
        predictor=predictor, data_path=data_path,
        history_start_date=history_start_str, history_end_date=history_end_str,
        raw_df=raw_df, val_ratio=val_ratio,
    )
    inference.model_valley_reg = best_res["model_valley_reg"]
    inference.model_solar_reg = best_res["model_solar_reg"]
    inference.model_solar_clf = best_res["model_solar_clf"]
    inference.model_peak_reg = best_res["model_peak_reg"]

    # 逐日预测（不再重训）
    while current_target_date <= end_target_date:
        # ... 预测逻辑保持不变
```

**验证**：对同一个日期跑修改前后的 RT 预测，对比 SMAPE 差异应 < 1%。

### 2.2 SGDFNet early_stopping + 周期重训（P1）

**文件 1**：`SGDFNet/src/sgdfnet/models.py`

在 `HGBModelConfig` 中将 `early_stopping` 改为 `True`：
```python
@dataclass
class HGBModelConfig:
    # ... existing fields ...
    early_stopping: bool = True          # was False
    n_iter_no_change: int = 10           # new field
    validation_fraction: float = 0.1     # new field
```

在 `build_regressor()` 函数中传入新参数：
```python
def build_regressor(config: HGBModelConfig):
    return HistGradientBoostingRegressor(
        # ... existing params ...
        early_stopping=config.early_stopping,
        n_iter_no_change=config.n_iter_no_change,
        validation_fraction=config.validation_fraction,
    )
```

**文件 2**：`SGDFNet/src/sgdfnet/protocol_b_cutoff.py`

在 `run_protocol_b_cutoff_experiment()` 中，将逐日训练改为每周重训：
```python
# 当前：for decision_day in decision_days:  # 每天都训
# 改为：
retrain_interval = 7  # 每 7 天重训一次
model = None
for i, decision_day in enumerate(decision_days):
    if model is None or i % retrain_interval == 0:
        model = build_regressor(config.model_config)
        model.fit(train_df, feature_cols, ...)
    # 用 model 预测当天
```

**验证**：对比修改前后在同一个月的 SMAPE，差异应 < 2%。

### 2.3 TimesFM 批量推理（P2）

**文件**：`TimesFM/infer.py`

**当前问题**：`predict_price_for_range()` 逐日调用 `predict_price_for_date()`，每次可能重新加载模型。

**修改方案**：将模型加载提取到循环外。查看 `forecast_next_day()` 内部是否有模型加载逻辑，如果有，提取为参数传入。

```python
def predict_price_for_range(script_path, data_path, start_date, end_date, ...):
    # 预加载模型（如果 forecast_next_day 支持传入 model 对象）
    model = load_model_once(...)
    
    all_results = []
    for date in date_range(start_date, end_date):
        result = forecast_next_day(..., model=model)  # 传入预加载的模型
        all_results.append(result)
    return pd.concat(all_results)
```

**注意**：如果 `forecast_next_day` 不支持传入 model 对象，需要先改其接口。查看 `TimesFM/_archive/price_forecast_copy_分时段预测.py` 中的实现。

## 阶段 3：项目封装

### 3.1 创建 configs/ 目录与 YAML 配置

创建 `configs/` 目录，为四阶段各建一个默认配置：

**`configs/model_stage.yaml`**：
```yaml
pipeline: model_stage
target: both
training_months: 12
val_ratio: 0.2
use_predicted_temp: false
segment_count: 3
seed: 42
deterministic: true
stage_models: formal
```

**`configs/learner_stage.yaml`**：
```yaml
pipeline: learner_stage
target: both
weight_lower_bound: -0.5
weight_upper_bound: 1.2
```

**`configs/fuse_stage.yaml`**：
```yaml
pipeline: fuse_stage
target: both
```

**`configs/classifier_stage.yaml`**：
```yaml
pipeline: classifier_stage
target: realtime
clf_data: null  # 默认使用 ExtremPriceClf/data/260525.xlsx
```

### 3.2 增强 main.py CLI

在 `cli/parser.py` 中增加便捷别名：
- `--pipeline predict` → 自动映射为依次执行 model_stage + learner_stage + fuse_stage + classifier_stage
- `--pipeline evaluate` → 计算 SMAPE 等指标

在 `main.py` 中增加全流程快捷模式：
```python
if args.pipeline == "predict":
    # 等价于依次跑四个阶段
    run_model_stage(args)
    run_learner_stage(args)
    run_fuse_stage(args)
    if args.target != "dayahead":
        run_classifier_stage(args)
```

### 3.3 创建 README.md

在项目根目录创建 `README.md`，包含：
- 项目简介（山东电力现货市场电价预测系统）
- 模型列表（5 个模型 + 融合 + 极端电价分类器）
- 快速启动指南（4 条命令）
- 目录结构说明
- 依赖与环境

### 3.4 创建陪跑步骤.md

在项目根目录创建 `陪跑步骤.md`，包含：
- 数据更新流程
- 日常预测操作步骤
- 常见问题排查
- 参考 epf 项目的 `陪跑步骤.md` 格式

### 3.5 创建 .env.example

```
PROJECT_ROOT=.
DATA_PATH=data/shandong_pmos_hourly.xlsx
OUTPUT_ROOT=daily_runs
TRAINING_MONTHS=12
VAL_RATIO=0.2
SPIKE_TRAIN_MONTHS=12
SPIKE_EPOCHS=12
SPIKE_PATIENCE=4
```

### 3.6 检查 requirements.txt 完整性

确认以下依赖都在 `requirements.txt` 中：
- pandas, numpy, scikit-learn, lightgbm
- torch, transformers
- xgboost, catboost
- openpyxl, pyyaml
- scipy
- matplotlib

## 阶段 4：端到端验证

完成以上所有修改后，执行完整验证：

```powershell
# 全流程一键执行
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline predict --target both --date 2026-05-15

# 或者分步执行
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline model_stage --target both --date 2026-05-15
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline learner_stage --target both --date 2026-05-15
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline fuse_stage --target both --date 2026-05-15
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline classifier_stage --target realtime --date 2026-05-15
```

### 验证清单

- [ ] 所有 DA 模型（lightgbm, timesfm, timemixer）产出 val_predictions.csv 和 forecast_predictions.csv
- [ ] 所有 RT 模型（timesfm, timemixer, sgdfnet, rt916）产出 val_predictions.csv 和 forecast_predictions.csv
- [ ] val_predictions.csv 约 720 行（30 天 × 24 小时）
- [ ] forecast_predictions.csv 24 行
- [ ] learner_stage 产出 weights.csv（DA 和 RT 各一份）
- [ ] fuse_stage 产出 fused_predictions.csv（24 行，y_fused 无空值）
- [ ] classifier_stage 不硬崩（可 skipped）
- [ ] 总运行时间 < 之前（对比优化前后耗时）
- [ ] `--pipeline predict` 一键模式可用

## 文件位置速查

| 资源 | 路径 |
|------|------|
| 项目根目录 | `D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0` |
| 参考项目 | `D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\epf` |
| 主数据文件 | `data/shandong_pmos_hourly.xlsx` |
| conda Python | `D:\computer_download\environment\conda\epf-2\python.exe` |
| 四阶段核心 | `pipelines/staged_pipeline.py` |
| 模型注册 | `runners/registry.py` |
| CLI 定义 | `cli/parser.py` |
| 入口 | `main.py` |

## 关键文件修改清单

| 文件 | 修改类型 | 目的 |
|------|---------|------|
| `lightGBM/main_fix.py` | 修改 | RT train-once |
| `SGDFNet/src/sgdfnet/models.py` | 修改 | early_stopping |
| `SGDFNet/src/sgdfnet/protocol_b_cutoff.py` | 修改 | 周期重训 |
| `TimesFM/infer.py` | 修改 | 批量推理 |
| `cli/parser.py` | 修改 | predict/evaluate 别名 |
| `main.py` | 修改 | 一键全流程 |
| `configs/model_stage.yaml` | 新建 | 默认配置 |
| `configs/learner_stage.yaml` | 新建 | 默认配置 |
| `configs/fuse_stage.yaml` | 新建 | 默认配置 |
| `configs/classifier_stage.yaml` | 新建 | 默认配置 |
| `README.md` | 新建 | 项目说明 |
| `陪跑步骤.md` | 新建 | 运维手册 |
| `.env.example` | 新建 | 环境变量模板 |
