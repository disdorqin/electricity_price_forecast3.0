# Electricity Forecast Delivery Pipeline v2.1

山东电力现货价格预测系统 — 多模型预测 + 30 天 Ledger 动态权重融合 + 极端价格分类校正。

---

## 第一部分：项目介绍

### 1. 一分钟理解整体链路

```
输入小时级山东电力现货数据
    ↓
7 个模型分别预测未来 24 小时
    ↓
过去 30 天 prediction ledger + actual ledger 学习动态融合权重
    ↓
按 task / period 加权融合
    ↓
Realtime 极端价格分类校正
    ↓
生成 submission_ready.csv
    ↓
postflight 校验；失败则进入 emergency fallback
```

正式链路分为 5 个阶段：

```
ledger_predict → ledger_weight → ledger_fuse → ledger_classifier → final_outputs
```

- **`ledger_predict`**：7 个模型并行/串行预测，写入 prediction ledger
- **`ledger_weight`**：读取 D-30 ~ D-1 的 prediction + actual ledger，学习 BGEW 动态融合权重
- **`ledger_fuse`**：按 (task, period, model) 权重逐小时加权融合
- **`ledger_classifier`**：仅修正 realtime 极端低价（mid-low 阈值）
- **`final_outputs`**：合并 dayahead + realtime，生成 24 行 `submission_ready.csv`

### 2. 当前验证结论

| 项 | 结果 |
|----|------|
| 单日完整链路 | NORMAL |
| 三天 range | NORMAL |
| 7 模型全量接入 | PASS |
| 30 天权重覆盖 | PASS (dayahead 2160/2160, realtime 2880/2880) |
| 故障注入 | 4/4 PASS |
| 虚假成功 (false success) | 0 |
| 最终验证报告 | `docs/FINAL_VALIDATION_SUMMARY.md` |

### 3. 目录组成

| 路径 | 用途 |
|------|------|
| `main.py` | 统一入口 |
| `cli/` | CLI 参数定义 |
| `pipelines/` | 正式 pipeline 编排 |
| `runners/` | 模型 registry + adapter 层 |
| `runtime/` | CPU/GPU 并行调度 |
| `fusion/` | BGEW 权重学习器、融合、分类器 |
| `lightGBM/` | LightGBM bundled 1.0-compatible 实现 |
| `TimesFMBackend/` | TimesFM bundled 1.0-compatible 后端 |
| `TimeMixer/` | TimeMixer 模型 |
| `SGDFNet/` | SGDFNet 模型 |
| `RT916_SpikeFusionNet/` | RT916 (SpikeFusionNet) 模型 |
| `ExtremPriceClf/` | 极端价格分类器 |
| `utils/` | 通用工具 |
| `scripts/` | 验证和检查脚本 |
| `docs/` | 技术文档 |
| `fixtures/seed_ledger/` | 32 天 seed ledger |
| `fixtures/repro_bundle/` | 快速复现包 |

### 4. Outputs 组成

| 目录 | 说明 |
|------|------|
| `outputs/ledger/` | 跨日期预测账本 + 实际值账本 |
| `outputs/runs/YYYY-MM-DD/` | 单日完整运行输出 |
| `outputs/runs/range_START_to_END/` | 时间段汇总 |
| `outputs/smoke/` | 小参数快速测试 |

关键文件：

- `outputs/runs/YYYY-MM-DD/final/submission_ready.csv` — 最终交付文件（24 行，6 列）
- `outputs/runs/YYYY-MM-DD/run_manifest.json` — 完整运行元信息
- `outputs/runs/YYYY-MM-DD/delivery_report.md` — 交付报告
- `outputs/runs/range_START_to_END/range_manifest.json` — 时间段汇总

**注意：** `outputs/` 默认由 `.gitignore` 忽略，不提交 Git。只提交 `fixtures/repro_bundle/` 作为复现种子。

### 5. 模型组成

| 模型 | 任务 | 设备 | 说明 |
|------|------|------|------|
| LightGBM | Dayahead | CPU | bundled 1.0-compatible |
| TimesFM | Dayahead + Realtime | CPU | bundled 1.0-compatible |
| TimeMixer | Dayahead + Realtime | GPU | 2.x 模型，默认 80 epochs |
| SGDFNet | Realtime | CPU | 2.x 模型 |
| RT916 (SpikeFusionNet) | Realtime | GPU | SpikeFusionNet，DA-RT 联动 |

- CPU 队列：默认 `--max-cpu-workers 2`，LightGBM、TimesFM、SGDFNet 并行
- GPU 队列：默认 `--max-gpu-workers 1`，TimeMixer、RT916 串行，防止 OOM
- 所有模型均已 bundled，**不需要外部 EPF v1.0 仓库**

---

## 第二部分：复现指南

### A. 快速复现

目标：使用已提交的 seed ledger / repro bundle，跳过 30 天 backfill，直接跑每日链路。

#### 1. 安装虚拟环境

```bash
conda create -n epf-2 python=3.10 -y
conda activate epf-2
pip install -r requirements.txt
```

**注意：**
- Windows + CUDA 已验证通过
- TimesFM 推荐 `timesfm==2.0.1`
- 如果 PyTorch GPU 版本有问题，先按本机 CUDA 安装 PyTorch，再安装 requirements

#### 2. 放置数据

```
data/shandong_pmos_hourly.xlsx
```

必需字段：`ds` / `时刻` / `时间`（时间戳）、`日前电价`（dayahead 目标值）、`实时电价`（realtime 目标值）。

#### 3. Ledger 数据

`outputs/ledger/` 已包含在仓库中（32 天 seed ledger：2026-01-25 ~ 2026-02-25），不需要手动复制。clone 后直接可用，无需 `ledger_backfill`。

**为什么示例日期是 2026-02-26？** 因为 seed ledger 覆盖到 2026-02-25（D-1），权重学习需要前 30 天数据。2026-02-26 是第一个 D-30..D-1 完全在 seed 范围内的日期。如果你有其他日期的数据，可以换成你想要的日期。

#### 4. 快速陪跑（单日）

```bash
python main.py 2026-02-26
python scripts/verify_final_pipeline.py --date 2026-02-26 --runs-root outputs/runs
```

#### 5. 快速 range 陪跑

```bash
python main.py 2026-02-24 2026-02-26
python scripts/verify_range_pipeline.py --start 2026-02-24 --end 2026-02-26 --runs-root outputs/runs
```

---

### B. 完全复现

目标：从零开始，包括 30 天 backfill、每日正式运行、range 运行、验证。

#### 1. 从零生成 30 天 ledger

```powershell
python main.py --pipeline ledger_backfill ^
    --start 2026-01-25 ^
    --end 2026-02-23 ^
    --data-path data/shandong_pmos_hourly.xlsx ^
    --max-cpu-workers 2 ^
    --max-gpu-workers 1 ^
    --seed 42 ^
    --deterministic ^
    --force
```

预计运行时间：6-12 小时（GPU 串行）。

**Backfill 做什么：** 逐日跑 `ledger_predict`，写 prediction ledger + actual ledger。\
**Backfill 不做什么：** 不跑权重学习、不融合、不生成 final。

参数说明：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--start` / `--end` | — | 回填日期范围 |
| `--data-path` | `data/shandong_pmos_hourly.xlsx` | 输入数据 |
| `--max-cpu-workers` | 2 | CPU 模型并行数 |
| `--max-gpu-workers` | 1 | GPU 模型并行数（串行防 OOM） |
| `--seed` | 42 | 随机种子 |
| `--deterministic` | 未启用 | 确定性运行（可复现） |
| `--force` | 未启用 | 强制重跑，跳过缓存 |

#### 2. 检查 30 天 ledger 是否完整

```bash
python scripts/check_delivery_stability.py
```

期望：

- Dayahead prediction: 30 天 x 3 模型 x 24 小时 = 2160 行
- Realtime prediction: 30 天 x 4 模型 x 24 小时 = 2880 行
- Actual ledger 每个 task: 30 天 x 24 小时 = 720 行

#### 3. 每日正式陪跑

```powershell
python main.py 2026-02-24 ^
    --data-path data/shandong_pmos_hourly.xlsx ^
    --max-cpu-workers 2 ^
    --max-gpu-workers 1 ^
    --seed 42 ^
    --deterministic
```

可选参数：

| 参数 | 说明 | 什么时候调整 |
|------|------|-------------|
| `--max-cpu-workers` | CPU 模型并行数 | CPU 资源不足时调小 |
| `--max-gpu-workers` | GPU 模型并行数 | 默认 1，避免 OOM |
| `--timemixer-epochs` | TimeMixer 训练轮数 | 快速联调可调小，正式结果不建议 |
| `--timemixer-patience` | early stopping patience | 快速联调可调小 |
| `--force` | 清理当日 cache 强制重跑 | 模型文件异常或数据更新后 |
| `--strict-classifier` | 分类器失败则全链路失败 | 严格验收时 |
| `--runs-root` | 运行输出根目录 | 隔离实验时 |
| `--ledger-root` | ledger 根目录 | 隔离实验时 |

**不建议：** 使用 `--allow-missing-models` 作为正式运行参数，或减少模型数量作为正式验证。

#### 4. 时间段正式陪跑

```powershell
python main.py 2026-02-24 2026-02-26 ^
    --data-path data/shandong_pmos_hourly.xlsx ^
    --max-cpu-workers 2 ^
    --max-gpu-workers 1 ^
    --seed 42 ^
    --deterministic
```

或显式指定 pipeline：

```powershell
python main.py --pipeline ledger_full_range ^
    --start 2026-02-24 ^
    --end 2026-02-26 ^
    --data-path data/shandong_pmos_hourly.xlsx ^
    --max-cpu-workers 2 ^
    --max-gpu-workers 1 ^
    --seed 42 ^
    --deterministic
```

range 模式额外参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--continue-on-error` | False | 某天失败后继续后续日期 |
| `--skip-existing-final` | False | 跳过已有且验证有效的最终输出 |
| `--no-range-preflight` | False | 跳过前置检查 |

#### 5. 验证命令

```bash
# 单日验证
python scripts/verify_final_pipeline.py --date 2026-02-24 --runs-root outputs/runs

# 时间段验证
python scripts/verify_range_pipeline.py --start 2026-02-24 --end 2026-02-26 --runs-root outputs/runs

# 综合稳定性测试（不依赖 GPU / 模型 / 真实数据）
python scripts/check_delivery_stability.py
```

#### 6. 实验隔离

联调时可使用独立 runs/ledger 目录：

```powershell
$RUN_ROOT = "outputs/_validation_tmp/runs"
$LEDGER_ROOT = "outputs/_validation_tmp/ledger"

python main.py 2026-02-24 ^
    --data-path data/shandong_pmos_hourly.xlsx ^
    --runs-root $RUN_ROOT ^
    --ledger-root $LEDGER_ROOT ^
    --timemixer-epochs 1 ^
    --timemixer-patience 1 ^
    --max-cpu-workers 2 ^
    --max-gpu-workers 1 ^
    --seed 42
```

**注意：** 快速参数只用于工程链路验证，不作为正式精度结果。实验结束后归档 manifest/report，清理临时目录。

---

## 第三部分：技术文档

### 1. Ledger 机制

Ledger 是跨日期共享的累积账本：

- **Prediction ledger**：`outputs/ledger/{task}/prediction/prediction_ledger.parquet`，累积每日每模型 24 小时预测值
- **Actual ledger**：`outputs/ledger/{task}/actual/actual_ledger.parquet`，累积每日实际值
- 关键列：`task` / `model_name` / `forecast_date` / `target_day` / `business_day` / `hour_business`
- hour 24 = D+1 00:00
- 同 key 自动 dedup，保留最新

### 2. 30 天 Hard Gate

`ledger_weight` 在权重学习前执行严格校验：

- Dayahead prediction 期望：30 × 3 × 24 = **2160 行**
- Realtime prediction 期望：30 × 4 × 24 = **2880 行**
- Actual ledger 每个 task 期望：30 × 24 = **720 行**

缺天、缺模型、缺小时会阻断权重学习，防止不完整账本生成假权重。

### 3. BGEW 动态权重

`fusion/learners/daily_ledger_gef.py`：

- 按 `(task, period)` 分别学习权重
- Period：`1_8` / `9_16` / `17_24`
- 顺序更新：从 D-1 (age=1) 到 D-30 (age=30) 逐日更新
- day_gate 线性衰减（0.3~0.85），最近一周可 boost 到 0.85
- 损失函数：`0.7 × smape_floor50 + 0.3 × mae_percent`
- 证据收缩防止过拟合
- 权重归一化：每个 `(task, period)` 内权重和为 1.0
- 输出：`weights.csv` / `dynamic_weight_trace.csv` / `candidate_metrics.csv`

### 4. 融合机制

- 逐小时读取各模型预测
- 使用对应 `(task, period, model)` 权重加权
- 缺模型时严格失败，不做静默补 0
- 输出：`fused_predictions.csv` (24 行) + `fused_debug.csv`

### 5. Realtime 极端价格分类器

`fusion/classifier_bridge.py`：

- **只作用于 realtime**
- 输入：fused realtime 预测
- 输出：`realtime_final_predictions_corrected.csv`（将符合条件的小时修正为 -80.00）
- 元数据：`classifier_report.json`
- 失败时：非 strict 模式保留未校正结果（warning）；`--strict-classifier` 使整链路失败

### 6. Postflight 校验

`pipelines/delivery_quality.py` 校验项：

- submission_ready.csv 存在
- 24 行
- 列名严格匹配：`business_day, ds, hour_business, period, dayahead_price, realtime_price`
- `business_day` 与目标日期一致
- `hour_business` = 1..24
- `ds` 与 hour 24 口径一致
- 价格列为 numeric，无缺失
- 无 `_x` / `_y` 后缀列
- manifest 各 stage 完整
- errors 列表为空

### 7. Delivery Status / Exit Code

| delivery_status | 含义 | exit code |
|----------------|------|-----------|
| NORMAL | 正常五阶段完成，无 fallback | 0 |
| DEGRADED_DELIVERED | 正常链路失败，fallback 成功交付 | 2 |
| FAILED_NO_DELIVERY | 正常链路 + fallback 均失败 | 1 |

### 8. 兜底策略（Emergency Fallback）

`pipelines/emergency_fallback.py`：

**触发条件：** 任意 stage 失败、postflight 校验失败、`submission_ready.csv` 不合法。

**方法：** 历史同小时中位数（tiered fallback）：

1. 最近 7 天同小时中位数
2. 最近 30 天同小时中位数
3. 全量历史同小时中位数
4. 全局中位数

**输出：**
- 标准 `final/submission_ready.csv`（24 行，6 列，格式一致）
- `fallback_report.json` + `fallback_report.md`

**重要：**
- Fallback **不写** prediction ledger，不污染未来权重学习
- 不伪装 NORMAL：`delivery_status = DEGRADED_DELIVERED`，exit code = 2
- 使用 fallback 后应修复问题并 `--force` 重跑正常链路

### 9. Troubleshooting

| 问题 | 可能原因 | 解决 |
|------|---------|------|
| `ledger_weight` failed | 30 天 ledger 不完整 | 复制 repro ledger 或运行 backfill |
| range preflight failed | 起始日前缺 ledger | 补 ledger 后重跑 |
| exit code 2 | fallback 交付 | 查看 fallback_report，修复后 force 重跑 |
| exit code 1 | 无可用交付 | 查看 run_manifest errors |
| CUDA OOM | GPU 模型占用高 | `--max-gpu-workers 1`，减小 batch size |
| TimesFM 依赖错误 | 环境版本不匹配 | 使用验证过的 `timesfm==2.0.1` |
| 结果不是 NORMAL | fallback 或 stage warning | 查看 delivery_report |

### 10. 输出文件说明

| 文件 | 说明 |
|------|------|
| `run_manifest.json` | 完整运行元信息（5 个 stage 状态、错误、配置） |
| `delivery_report.md/json` | 交付报告 |
| `submission_ready.csv` | 最终交付文件（24 行，6 列） |
| `range_manifest.json` | range 运行汇总 |
| `range_summary.csv` | range 每日状态 CSV |
| `fallback_report.md/json` | 兜底策略报告 |
| `weights.csv` | BGEW 融合权重 |
| `dynamic_weight_trace.csv` | 权重学习轨迹 |
| `coverage_report.csv` | ledger 覆盖审计 |

### 11. 最终验证结果

全部验证细节见 [`docs/FINAL_VALIDATION_SUMMARY.md`](docs/FINAL_VALIDATION_SUMMARY.md)。

总结：

- **单日 `2026-02-26`**：NORMAL，5/5 stage complete，7/7 模型 OK，postflight PASS
- **三日 range `2026-02-24~26`**：NORMAL，3/3 天 completed，0 degraded/failed/skipped
- **故障注入 4 例**：全部 PASS（stage failure → fallback ✓，缺数据 → FAILED ✓，空 ledger → hard gate ✓，坏 final → fallback ✓）
- **False success：0**

### 12. 常见问题与解决方法

#### Q1: 为什么权重学习需要 30 天 ledger？

**现象：** `ledger_weight` 报 "ledger window incomplete" 或 training coverage 不满足。

**原因：**
- `ledger_weight` 用 D-30..D-1 的模型预测和实际值拟合权重。
- Dayahead 需要 30 × 3 × 24 = **2160 行** prediction rows。
- Realtime 需要 30 × 4 × 24 = **2880 行** prediction rows。
- actual 每个 task 需要 30 × 24 = **720 行**。
- 权重学习**不**读 `outputs/runs`，而是读 `outputs/ledger`。

**检查命令：**
```bash
python scripts/check_delivery_stability.py
```

**解决方法：** 复制 `fixtures/repro_bundle/ledger/` 到 `outputs/ledger/`，或运行完整 `ledger_backfill`。

**是否需要重跑：** 是。补全 ledger 后重新运行整条链路。

---

#### Q2: 机器到底从哪里找 30 天数据？

默认读取 `outputs/ledger`，具体文件：

| 数据 | 路径 |
|------|------|
| Dayahead prediction | `outputs/ledger/dayahead/prediction/prediction_ledger.parquet` |
| Dayahead actual | `outputs/ledger/dayahead/actual/actual_ledger.parquet` |
| Realtime prediction | `outputs/ledger/realtime/prediction/prediction_ledger.parquet` |
| Realtime actual | `outputs/ledger/realtime/actual/actual_ledger.parquet` |

**注意事项：** `outputs/ledger/` 已包含在仓库中（32 天 seed），clone 后直接可用。如果意外删除了 `outputs/ledger/`，可以从 `fixtures/repro_bundle/ledger/` 手动复制恢复，或运行 `ledger_backfill` 重新生成。

**自定义路径：** 传 `--ledger-root YOUR_PATH` 则读取指定目录。

---

#### Q3: 为什么不提交完整 outputs/runs？

`outputs/runs` 是每日中间产物和审计产物，原因如下：
- 权重学习不直接从 runs 读取数据。
- 全量 runs 体积大（400+ 文件，~5.6 MB），容易污染仓库。
- 仓库只保留 `fixtures/repro_bundle/sample_runs/` 作为验证通过的样例。
- 正式输出在本地 `outputs/runs/` 生成，`.gitignore` 已忽略此目录。

**注意：** `outputs/runs/` 不提交不影响任何功能。`outputs/ledger/` 已包含在仓库中（32 天 seed），clone 后直接可用。

---

#### Q4: `ledger_weight failed` 怎么办？

**现象：**
- 报 "ledger window incomplete"。
- training coverage failed。
- expected_rows 和 actual_rows 不一致。

**检查命令：**
```bash
python scripts/check_delivery_stability.py
```

或直接调用 Python 检查：
```python
from pipelines.delivery_quality import validate_ledger_window
print(validate_ledger_window("2026-02-26", "outputs/ledger"))
```

**解决方法：**
1. 删除坏的 `outputs/ledger`，让程序自动从 fixtures bootstrap。
2. 或手动复制 `fixtures/repro_bundle/ledger/` 到 `outputs/ledger/`。
3. 或运行完整 backfill：`python main.py --pipeline ledger_backfill --start YYYY-MM-DD --end YYYY-MM-DD`。

**是否需要重跑：** 是。

---

#### Q5: 第一次运行就报找不到 data 文件怎么办？

**原因：** ledger 只负责权重训练窗口，当天的模型预测仍需要原始 Excel 数据。

**检查命令：**
```bash
# Linux / macOS
ls data/shandong_pmos_hourly.xlsx

# Windows PowerShell
Test-Path data/shandong_pmos_hourly.xlsx
```

**解决方法：**
- 将数据文件放到 `data/shandong_pmos_hourly.xlsx`。
- 或用 `--data-path /path/to/your/data.xlsx` 指定真实路径。
- 如果数据不能公开，**不要**提交到 Git。

**是否需要重跑：** 是。

---

#### Q6: 为什么有 seed ledger 还要 data？

**原因：**
- seed ledger 只用于历史权重学习（D-30..D-1）。
- 今天 `ledger_predict` 仍要读取输入数据来训练/推理当天模型。
- emergency fallback 也需要历史价格数据计算中位数。

**结论：** data 和 ledger 是互补的，缺一不可。

---

#### Q7: Exit code 2 是不是成功？

**不是 NORMAL。**

- `exit code 2 = DEGRADED_DELIVERED`。
- 有交付文件（`submission_ready.csv`），但来自 emergency fallback。
- 需要查看 `fallback_report.md` / `fallback_report.json` 了解原因。
- 修复问题后使用 `--force` 重跑。

**是否可以提交：** 应急场景下可以，但需注明为 DEGRADED 状态，且后续应修复重跑。

---

#### Q8: `FAILED_NO_DELIVERY` 怎么办？

**含义：** 正常链路失败，fallback 也失败，无任何交付文件。

**常见原因：**
- 输入数据缺失（data file 找不到或格式错误）。
- 历史价格数据不足（fallback 所需的最少历史数据也不够）。
- 输出目录不可写。
- 关键依赖缺失（如 PyTorch / TimesFM 未正确安装）。

**检查命令：** 查看 `run_manifest.json` 的 `errors` 列表和 `delivery_report.md`。

**解决方法：** 根据 manifest 中的具体错误修复后 `--force` 重跑。

---

#### Q9: CUDA OOM 怎么办？

**解决方法：**
- 保持 `--max-gpu-workers 1`（GPU 模型串行执行）。
- 减小 `--timemixer-batch-size 8`（降低单次推理显存占用）。
- 快速联调时降低 `--timemixer-epochs 1 --timemixer-patience 1`。
- 关闭其他占用 GPU 的程序（nvidia-smi 查看）。

**注意：** 快速参数（减少 epochs / patience）只用于工程链路验证，不代表正式精度。

---

#### Q10: TimesFM 依赖报错怎么办？

**经验证版本：**
- `timesfm==2.0.1`
- 使用 `TimesFM_2p5_200M_torch` API（纯 PyTorch，Windows 兼容）。
- 避免使用 JAX API（Windows 不兼容）。

**解决方法：**
- 重新安装验证版本：`pip install timesfm==2.0.1`。
- 不要修改 `TimesFMBackend/` 代码逻辑。
- 保持 LightGBM 和 TimesFM 的 1.0-compatible bundled 行为不变。

---

#### Q11: 为什么 final/submission_ready.csv 不是 24 行？

**检查命令：**
```bash
python scripts/verify_final_pipeline.py --date YYYY-MM-DD --runs-root outputs/runs
```

同时查看：
- `run_manifest.json` — 确认各 stage 是否 complete。
- `delivery_report.md` — 查看校验结果详情。

**可能原因：**
- 某模型预测少小时。
- fuse 缺小时（某个 period 权重缺失）。
- classifier 输出不完整。
- final merge 出错。

**解决方法：** `--force` 重跑。检查对应 task 的 prediction / fuse / final 文件。如果是 fallback 触发，查看 fallback_report。

---

#### Q12: Range preflight failed 怎么办？

**原因：** Start 日期前 D-30..D-1 的 ledger 数据不完整。

**解决方法：**
- 先补 ledger：复制 `fixtures/repro_bundle/ledger/` 或运行 backfill。
- 不建议使用 `--no-range-preflight` 绕过检查——正式验收必须通过 preflight。

**是否需要重跑：** 是。

---

#### Q13: 可以减少模型数量吗？

- **正式复现不建议。** 7/7 模型完整才算 NORMAL 交付。
- `--allow-missing-models` 只能用于诊断调试，不用于正式交付。
- 如果某模型不可用，链路仍可跑完，但交付状态会标记为 DEGRADED。

---

#### Q14: 可以减少 TimeMixer 训练时间吗？

- **快速联调可以。** 使用：
  ```bash
  --timemixer-epochs 1 --timemixer-patience 1
  ```
- **正式精度不要减少。** 默认 80 epochs + early stopping。
- 快速联调时必须使用隔离目录（`--runs-root` / `--ledger-root`），避免污染正式结果。

---

#### Q15: 如何确认没有污染正式 outputs？

**检查命令：**
```bash
git ls-files outputs data models
```
预期输出为空（这些目录被 `.gitignore` 忽略）。

**本地清理：**
```bash
# Linux / macOS
rm -rf outputs/_validation_tmp outputs/_exp_fast outputs/_fault_injection_tmp

# Windows PowerShell
Remove-Item outputs/_validation_tmp -Recurse -Force -ErrorAction SilentlyContinue
```

---

#### Q16: 复现包和正式输出有什么区别？

| | `fixtures/repro_bundle/ledger` | `fixtures/repro_bundle/sample_runs` | `outputs/runs/` |
|--|-------------------------------|-------------------------------------|-----------------|
| 用途 | 权重学习种子数据 | 验证通过的交付样例 | 每日正式输出 |
| 提交 Git | 是 | 是 | 否（gitignored） |
| 是否被链路修改 | 否（静态） | 否（静态） | 是（每次运行追加） |
| 是否可替代 backfill | 是 | 不适用 | 否 |

---

#### Q17: 如果想完全从零复现怎么办？

1. 不使用 repro_bundle。
2. 运行完整 `ledger_backfill`（30 天，预计 6-12 小时）。
3. 然后每日运行 `ledger_full`。
4. 参考 README 第二部分 B 节完整命令。

---

#### Q18: 为什么不直接读取 fixtures/repro_bundle/ledger？

**原因：**
- 正式运行会继续 append 新预测到 ledger。
- `fixtures/` 应保持静态，不应被运行时修改。
- 所以仓库中已有 `outputs/ledger/`（预置 seed），运行时直接修改 `outputs/ledger` 而不会动 `fixtures/`。
- 如果意外删除了 `outputs/ledger/`，从 `fixtures/repro_bundle/ledger/` 手动复制即可恢复。

---

#### Q19: 今天跑完以后，ledger 会怎么变化？

- `ledger_predict` 会把今天各模型预测追加到 `outputs/ledger/{task}/prediction/`。
- actual ledger 也会按可得实际值更新（同 key 自动 dedup，保留最新值）。
- 后续日期的权重学习会自动使用更新后的 ledger。

---

#### Q20: 甲方只要最终交付文件应该看哪里？

- 单日：`outputs/runs/YYYY-MM-DD/final/submission_ready.csv`
- Range：查看 `range_summary.csv` 和每天的 final 文件。
- 审计附件：`run_manifest.json` / `delivery_report.md`。

---

## License

MIT
