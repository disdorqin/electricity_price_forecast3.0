# electricity_forecast_model2.1

山东电力现货价格预测系统 — 多模型预测 + 30 天 Ledger 动态权重融合 + 极端价格分类校正。

**最终交付：** `outputs/runs/YYYY-MM-DD/final/submission_ready.csv`，包含未来 24 小时的日前价格（dayahead）和实时价格（realtime）预测。

**核心方法：** 7 个模型各自预测 → 过去 30 天的真实预测/实际值学习 BGEW 动态融合权重 → 加权融合 → realtime 极端价格分类校正。

**正式入口：** `main.py`，默认 pipeline 为 `ledger_full`（单日完整链路）。不需要外部 EPF v1.0 仓库，所有模型实现均已 bundled。

**不提交 Git：** `data/`、`outputs/`、`models/`。

---

## 目录结构

### 代码目录

| 目录 | 说明 |
|------|------|
| `cli/` | 命令行参数定义（`main.py` → `cli/parser.py`） |
| `pipelines/` | 正式 pipeline 编排（`ledger_predict`、`ledger_weight`、`ledger_full` 等） |
| `runners/` | 模型 registry + adapter 层 |
| `runtime/` | CPU/GPU 并行调度 |
| `fusion/` | 融合核心：BGEW 权重学习器、权重应用、分类器桥接 |
| `lightGBM/` | LightGBM bundled 实现 |
| `TimesFMBackend/` | TimesFM bundled 后端（**不是** TensorFlow） |
| `TimeMixer/` | TimeMixer 模型实现 |
| `SGDFNet/` | SGDFNet 模型实现 |
| `RT916_SpikeFusionNet/` | RT916 (SpikeFusionNet) 模型实现 |
| `ExtremPriceClf/` | 极端价格分类器 |
| `utils/` | 通用工具（business_day、reproducibility、io） |
| `scripts/` | 环境检查、输出验证、可复现性检查等 |
| `docs/` | 项目文档 |
| `fixtures/seed_ledger/` | 示例 seed ledger（不是正式 outputs） |

### 运行产物目录

正式交付只看以下三个目录：

| 目录 | 说明 |
|------|------|
| `outputs/ledger/` | 跨日期预测账本 + 实际值账本，用于 30 天权重学习 |
| `outputs/runs/YYYY-MM-DD/` | 某业务日的完整运行输出，含 `final/submission_ready.csv` |
| `outputs/runs/range_*_to_*/` | 时间段运行的汇总 manifest + summary CSV |
| `outputs/smoke/` | 小参数快速测试输出，不污染正式 ledger |

**其他 `outputs/` 下的目录**（如 `outputs/audit_30day_*`、`outputs/repro_check`、`outputs/unified_runs`、`outputs/RT916_SpikeMarketLab` 以及直接命名日期如 `outputs/2026-02-01`）均为本地历史实验/调试产物，不属于正式交付链路，可删除或忽略。

详细输出规则见 [`docs/OUTPUT_CONVENTION.md`](docs/OUTPUT_CONVENTION.md)。

---

## 模型介绍

| 模型 | 设备 | 目标 | 说明 |
|------|------|------|------|
| LightGBM | CPU | Dayahead | bundled `lightGBM/` |
| TimesFM | CPU | Dayahead + Realtime | bundled `TimesFMBackend/`（不是 TensorFlow） |
| TimeMixer | GPU | Dayahead + Realtime | v2.0 内部 early stopping / calibration |
| SGDFNet | CPU | Realtime | v2.0 内部 val_days 校准 |
| RT916 (SpikeFusionNet) | GPU | Realtime | v2.0 内部 DA-RT 联动 |

- **CPU 队列**：默认 `--max-cpu-workers 2`，LightGBM、TimesFM、SGDFNet 并行。
- **GPU 队列**：默认 `--max-gpu-workers 1`，TimeMixer、RT916 串行，防止 OOM。
- **Realtime cutoff**：默认 D-1 14:00，由 `--realtime-cutoff-hour 14` 统一控制，影响所有 realtime 模型。
- **外部 EPF v1.0**：`--epf-v1-root` 仅用于旧版兼容，正常交付不需要。LightGBM 和 TimesFM 均使用本仓库 bundled 实现。

---

## 正式预测链路

每日正式预测由 `ledger_full` 串联以下 5 个阶段：

```
ledger_predict  →  ledger_weight  →  ledger_fuse  →  ledger_classifier  →  final_outputs
(7 模型预测)       (BGEW 权重学习)     (加权融合)        (极端价格校正)         (交付文件)
```

### 各阶段说明

**1. `ledger_predict` — 模型预测**

- 跑全部 7 个模型，每个模型生成当天 24 小时预测。
- CPU 模型并行，GPU 模型串行。
- 输出写入 `outputs/runs/YYYY-MM-DD/{task}/prediction/`。
- 同时将预测值追加到 `outputs/ledger/{task}/prediction/prediction_ledger.parquet`，供未来日期学习权重使用。
- 已有预测文件时自动缓存跳过（cache HIT）；`--force` 强制重跑。

**2. `ledger_weight` — BGEW 权重学习**

- 读取过去 30 天（D-30 到 D-1）的 prediction ledger + actual ledger。
- 按 task（dayahead/realtime）和 period（1-8、9-16、17-24）分别学习每个模型的动态融合权重。
- 输出 `weights.csv`、`dynamic_weight_trace.csv`、`coverage_report.csv` 到 `outputs/runs/YYYY-MM-DD/{task}/weight/`。

**3. `ledger_fuse` — 加权融合**

- 根据当天 `weights.csv`，对当天各模型预测做逐小时加权融合。
- 输出 `fused_predictions.csv`（24 行）到 `outputs/runs/YYYY-MM-DD/{task}/fuse/`。

**4. `ledger_classifier` — 极端价格分类（仅 Realtime）**

- 对 realtime 融合预测做极端低价（mid-low）分类校正。
- 将符合条件的小时修正为 -80.00。
- 输出 `realtime_final_predictions_corrected.csv` + `classifier_report.json`。
- 失败时自动 fallback 到副分类器（`--strict-classifier` 可让全链路失败）。

**5. `final_outputs` — 最终交付**

- 合并 dayahead + realtime 修正后结果，生成 `final/submission_ready.csv`。
- 24 行，字段：`business_day, ds, hour_business, period, dayahead_price, realtime_price`。

---

## 输出文件结构

以下为单日运行完成后的完整目录结构：

```
outputs/runs/YYYY-MM-DD/
├── run_manifest.json               # 完整运行元信息（5 个 stage 状态、row counts、config）
│
├── dayahead/
│   ├── prediction/                  # 各模型初始预测（cache key）
│   │   ├── lightgbm_predictions.csv
│   │   ├── timemixer_predictions.csv
│   │   ├── timesfm_predictions.csv
│   │   └── all_model_predictions_long.csv  (72 行)
│   ├── weight/                      # 30 天账本学习出的融合权重
│   │   ├── weights.csv
│   │   ├── dynamic_weight_trace.csv
│   │   ├── candidate_metrics.csv
│   │   └── coverage_report.csv
│   ├── fuse/                        # 加权融合结果
│   │   ├── fused_predictions.csv    (24 行)
│   │   └── fused_debug.csv
│   └── final/
│       └── dayahead_final_predictions.csv
│
├── realtime/
│   ├── prediction/                  # 各模型初始预测（cache key）
│   │   ├── timesfm_predictions.csv
│   │   ├── sgdfnet_predictions.csv
│   │   ├── timemixer_predictions.csv
│   │   ├── rt916_predictions.csv
│   │   └── all_model_predictions_long.csv  (96 行)
│   ├── weight/                      # 30 天账本学习出的融合权重
│   │   ├── weights.csv
│   │   ├── dynamic_weight_trace.csv
│   │   ├── candidate_metrics.csv
│   │   └── coverage_report.csv
│   ├── fuse/                        # 加权融合结果
│   │   ├── fused_predictions.csv    (24 行)
│   │   └── fused_debug.csv
│   └── final/
│       ├── realtime_final_predictions.csv           (分类器前)
│       ├── realtime_final_predictions_corrected.csv  (分类器后, 24 行)
│       └── classifier_report.json
│
└── final/
    ├── dayahead_final_predictions.csv
    ├── realtime_final_predictions.csv
    ├── realtime_final_predictions_corrected.csv
    └── submission_ready.csv          ← 最终客户交付文件 (24 行)
```

### 目录命名约定

| 目录 | 内容 | 说明 |
|------|------|------|
| `prediction/` | 各模型原始预测（per-model CSV + long table） | 缓存 key，`--force` 时清除 |
| `weight/` | BGEW 权重学习结果 | 目录名是 `weight`，文件名是 `weights.csv` |
| `fuse/` | 加权融合结果 | 目录名是 `fuse`，不是 `fused/` |
| `final/` | 最终可交付文件 | 含 `submission_ready.csv` |

---

## 复现指南 / 快速运行

### 环境安装

```bash
conda create -n epf-2 python=3.11 -y
conda activate epf-2
pip install -r requirements.txt
```

### 放置数据

默认数据路径：`data/shandong_pmos_hourly.xlsx`

必需字段：

| 字段 | 说明 |
|------|------|
| `ds` 或 `时刻` | 时间戳（小时级） |
| `日前电价` | Dayahead 电价目标值 |
| `实时电价` | Realtime 电价目标值 |
| 负荷/气象特征 | 可选协变量（建议） |

数据要求：
- 最小建议 2 年历史覆盖（如 2024-01 ~ 2026-02）
- 小时级粒度，每天 24 点（hour 24 = D+1 00:00）
- realtime cutoff D-1 14:00 前数据需可用

### 每日正式预测

最简单形式（默认 `ledger_full` pipeline）：

```bash
python main.py 2026-02-24
```

推荐形式（显式指定参数）：

```powershell
conda run -n epf-2 python main.py 2026-02-24 ^
    --data-path data/shandong_pmos_hourly.xlsx ^
    --max-cpu-workers 2 ^
    --max-gpu-workers 1 ^
    --seed 42 ^
    --deterministic
```

输出：`outputs/runs/2026-02-24/final/submission_ready.csv`（24 行，6 列）

### 时间段预测

两个 positional 参数自动激活 range 模式。每天完整执行五阶段链路：

```
ledger_predict → ledger_weight → ledger_fuse → ledger_classifier → final_outputs
```

```powershell
conda run -n epf-2 python main.py 2026-02-24 2026-02-25 ^
    --data-path data/shandong_pmos_hourly.xlsx ^
    --max-cpu-workers 2 ^
    --max-gpu-workers 1 ^
    --seed 42 ^
    --deterministic
```

或显式指定：

```powershell
conda run -n epf-2 python main.py --pipeline ledger_full_range ^
    --start 2026-02-24 --end 2026-02-25 ^
    --data-path data/shandong_pmos_hourly.xlsx ^
    --max-cpu-workers 2 ^
    --max-gpu-workers 1 ^
    --seed 42 ^
    --deterministic
```

### 前置条件

Start 日期前必须有至少 30 天完整 ledger。如果没有，请先 [初始化 ledger](#什么时候需要-backfill--历史回填)。

### 输出位置

- 每天独立输出：`outputs/runs/YYYY-MM-DD/final/submission_ready.csv`
- 区间汇总：`outputs/runs/range_2026-02-24_to_2026-02-25/range_manifest.json`
- 区间 CSV：`outputs/runs/range_2026-02-24_to_2026-02-25/range_summary.csv`

### 稳定性机制

| 参数 | 默认 | 行为 |
|------|------|------|
| `--continue-on-error` | False | 某天失败后继续后续日期，最终状态为 `partial` |
| `--skip-existing-final` | False | 跳过已有且经过 12 项严格校验的输出的日期 |
| `--no-range-preflight` | — | 跳过前置检查（data/ledger 存在性、D-30 覆盖） |

Range preflight 默认严格检查 D-30..D-1 窗口内每天每模型 24 行的完整覆盖。如果 preflight 失败，即使在 manifest 落盘后也不会执行任何一天。详细错误见 `range_manifest.json`。

### 交付状态

每个单日 `run_manifest.json` 和区间 `range_manifest.json` 现在包含 `delivery_status` 字段：

| 状态 | 含义 |
|------|------|
| `NORMAL` | 正常五阶段完成，postflight 全部通过，未使用 fallback |
| `DEGRADED_DELIVERED` | 正常链路或 postflight 有问题，但系统通过 emergency fallback 产生了可用的 `submission_ready.csv`。终端会醒目提醒后续修复 |
| `FAILED_NO_DELIVERY` | 正常链路失败且 fallback 也失败，没有可用的交付文件 |

### Exit Code

`main.py` 对 `ledger_full` 和 `ledger_full_range` 按以下规则返回：

| 条件 | Exit Code |
|------|-----------|
| `delivery_status == NORMAL` | 0 |
| `delivery_status == DEGRADED_DELIVERED` | 2 |
| 其他失败 | 1 |

Exit code 2 表示"有输出但已降级"——CI/CD 可以视为 warning 而非 error，但不应视为正常成功。

### Emergency Fallback

当 normal pipeline 无法生成有效的 `submission_ready.csv` 时，系统自动尝试 emergency fallback：

- **方法**：使用过去 7/30/全量历史数据的同小时中位数
- **输出**：标准 24 行 `submission_ready.csv`，格式一致
- **标记**：`delivery_status = DEGRADED_DELIVERED`，不会伪装为 NORMAL
- **报告**：生成 `fallback_report.md` / `fallback_report.json`
- **注意事项**：
  - Fallback 不使用模型预测，不代表正常模型成功
  - Fallback **不写入** prediction ledger，避免污染未来权重学习
  - 使用 fallback 后应尽快修复问题并 `--force` 重跑正常链路，恢复 ledger 连续性

### Delivery Report

每次正式运行会生成交付报告：

- 每日报告：`outputs/runs/YYYY-MM-DD/delivery_report.md`
- 每日 JSON：`outputs/runs/YYYY-MM-DD/delivery_report.json`
- 区间报告：`outputs/runs/range_START_to_END/range_delivery_report.md`
- 区间 JSON：`outputs/runs/range_START_to_END/range_delivery_report.json`

### 校验命令

```shell
# 稳定性 synthetic 测试（不依赖 GPU/模型/数据）
python scripts/check_delivery_stability.py

# 单日验证
python scripts/verify_final_pipeline.py --date YYYY-MM-DD --runs-root outputs/runs

# 时间段验证（默认严格，--allow-degraded 接受降级交付）
python scripts/verify_range_pipeline.py --start START --end END --allow-degraded
```

### 常见状态

| 状态 | 含义 |
|------|------|
| `complete` | 所有日期成功或跳过 |
| `partial` | 部分日期失败（`--continue-on-error`） |
| `preflight_failed` | 前置校验未通过 |
| `interrupted` | 用户中断（Ctrl+C） |
| `failed` | 某天失败后停止 |

### Enable Check — 验证输出

```bash
python scripts/verify_final_pipeline.py --date 2026-02-24 --runs-root outputs/runs
```

### 时间段验证

```bash
python scripts/verify_range_pipeline.py --start 2026-02-24 --end 2026-02-25 --runs-root outputs/runs
```

使用 `--allow-degraded` 接受降级交付：
```bash
python scripts/verify_range_pipeline.py --start 2026-02-24 --end 2026-02-25 --allow-degraded
```

### 稳定性 Synthetic 测试

不依赖 GPU / 模型 / 真实数据，验证核心校验逻辑：

```shell
python scripts/check_delivery_stability.py
```

输出示例：
```
CHECK_DELIVERY_STABILITY
PASS: daily submission valid
PASS: daily submission catches missing hour
PASS: ledger window valid
PASS: ledger window catches missing model-day
PASS: ledger window catches missing hour
PASS: emergency fallback creates degraded delivery
PASS: exit code: NORMAL -> 0
PASS: exit code: DEGRADED_DELIVERED -> 2
PASS: exit code: FAILED_NO_DELIVERY -> 1
RESULT: 9/9 passed, 0 failed
```

### 常见状态

| 状态 | 含义 |
|------|------|
| `complete` | 所有日期成功或跳过 |
| `partial` | 部分日期失败（`--continue-on-error`） |
| `preflight_failed` | 前置校验未通过 |
| `interrupted` | 用户中断（Ctrl+C） |
| `failed` | 某天失败后停止 |

### Smoke 快速测试

Smoke 是小参数快速测试，**不是正式预测**，不输出最终交付文件。它只验证模型预测链路能跑通。输出在 `outputs/smoke/`，不污染正式 `outputs/ledger/`。

```powershell
conda run -n epf-2 python main.py --pipeline ledger_smoke --date 2026-02-24 ^
    --data-path data/shandong_pmos_hourly.xlsx ^
    --smoke-training-months 3 ^
    --smoke-timemixer-epochs 3 ^
    --smoke-timemixer-patience 1 ^
    --seed 42 ^
    --deterministic ^
    --force
```

Smoke 默认行为：
- 使用 `outputs/smoke/ledger` + `outputs/smoke/runs`，**不触碰**正式 `outputs/ledger/`
- 训练月数少（3 个月）、TimeMixer epoch 少（3 轮）
- 只运行预测链路（`ledger_predict`），不跑权重/融合/分类器
- 必须所有模型都成功才算 PASS

### 验证输出

单日验证：

```bash
python scripts/verify_final_pipeline.py --date 2026-02-24 --runs-root outputs/runs
```

时间段验证：

```bash
python scripts/verify_range_pipeline.py --start 2026-02-24 --end 2026-02-25 --runs-root outputs/runs
```

Smoke 验证：

```bash
python scripts/verify_smoke.py
```

环境检查：

```bash
python scripts/env_check.py
```

---

## 什么时候需要 Backfill / 历史回填

### Backfill 的意义

Backfill 不是某一天的正式预测，也不是最终交付。它的作用是**为第一个正式预测日准备过去 30 天的预测账本和实际值账本**。

正式链路中的 `ledger_weight` 需要读取 D-30 到 D-1 的 prediction ledger + actual ledger 来学习融合权重。如果 `outputs/ledger/` 为空，首次运行 `ledger_full` 会因缺少历史数据而失败。

**backfill 只做什么：**
- 循环调用 `ledger_predict`，逐日回填历史预测
- 把预测值写入 `outputs/ledger/{task}/prediction/prediction_ledger.parquet`
- 把实际值写入 `outputs/ledger/{task}/actual/actual_ledger.parquet`

**backfill 不做什么：**
- 不跑 `ledger_weight`（权重学习）
- 不跑 `ledger_fuse`（融合）
- 不跑 `ledger_classifier`（分类器）
- 不生成 `final/submission_ready.csv`

### 何时需要 Backfill

| 场景 | 需要 Backfill？ |
|------|----------------|
| 首次在新机器运行，`outputs/ledger/` 为空 | 是 |
| 已有 30 天以上 ledger（如从 seed 复制） | 否 |
| 想补充更早的历史数据 | 可选 |

### 快速填充（推荐）

预置的 32 天 seed ledger 在 `fixtures/seed_ledger/`（2026-01-25 ~ 2026-02-25）：

```bash
mkdir -p outputs/ledger
cp -r fixtures/seed_ledger/* outputs/ledger/
```

此方式省去 6-12 小时的 backfill 运行时间。

### 完整 Backfill 命令

```powershell
conda run -n epf-2 python main.py --pipeline ledger_backfill ^
    --start 2026-01-25 ^
    --end 2026-02-23 ^
    --data-path data/shandong_pmos_hourly.xlsx ^
    --max-cpu-workers 2 ^
    --max-gpu-workers 1 ^
    --seed 42 ^
    --deterministic
```

预计运行时间：6-12 小时（GPU 串行排队）。

---

## 技术文档

### 30 天 Ledger 机制

Ledger 是跨日期共享的累积账本，不属于某一天 `outputs/runs/YYYY-MM-DD/`：

- **Prediction ledger**：`outputs/ledger/{task}/prediction/prediction_ledger.parquet`，累积每天每个模型的 24 小时预测。
- **Actual ledger**：`outputs/ledger/{task}/actual/actual_ledger.parquet`，累积每天的实际值。
- 同一天同模型同小时自动 dedup，保留最新。
- `ledger_weight` 读取 D-30 到 D-1 的 ledger，学习融合权重。
- 32 天 × 3 模型 × 24h = 2304 行（dayahead prediction），32 天 × 4 模型 × 24h = 3072 行（realtime prediction）。

### BGEW 动态权重学习器

`fusion/learners/daily_ledger_gef.py`：

- **初始化**：所有模型等权。
- **顺序更新**：从 D-1（age_days=1）到 D-30（age_days=30）逐日更新权重。
- **day_gate**：线性衰减（0.3~0.85），最近一周可 boost 到 0.85。
- **损失函数**：`0.7 × smape_floor50 + 0.3 × mae_percent`
- **分时段**：dayahead/realtime 均按 1-8、9-16、17-24 三时段独立学习权重。
- **证据收缩**：防止过拟合于稀疏数据。
- **权重归一化**：每个 `(task, period)` 内部权重和为 1.0。

### 并行调度

```
CPU queue (max_workers=2, 默认):
  LightGBM v1.0  — dayahead
  TimesFM v1.0   — dayahead + realtime
  SGDFNet        — realtime

GPU queue (max_workers=1, 串行):
  TimeMixer      — dayahead + realtime
  RT916          — realtime
```

- `--max-cpu-workers 2`：CPU 模型并行数，默认 2。
- `--max-gpu-workers 1`：GPU 模型串行数，默认 1（防止 CUDA OOM）。

### Realtime Cutoff

- 所有 realtime 模型统一使用 D-1 14:00 截止。
- 由 `--realtime-cutoff-hour 14` 控制。
- 截止时间影响：TimeMixer、RT916、SGDFNet 的数据窗口。

### 极端价格分类器

`fusion/classifier_bridge.py`：

- **只作用于 realtime**。
- 输入：`fused_predictions.csv`（24 小时融合值）。
- 输出：`realtime_final_predictions_corrected.csv`（24 行），将符合条件的极端低价修正为 -80.00。
- 元数据：`classifier_report.json`（method、success、n_corrections）。
- 失败时自动 fallback 到副分类器。

### 缓存机制

- **`prediction/`** 目录受缓存保护：如果 per-model CSV 已存在，跳过模型推理（cache HIT）。
- **`weight/`、`fuse/`、`final/`** 每次运行重新生成（无缓存）。
- `--force`：强制清除 prediction cache 并重跑所有模型。
- `--force` 同时会清空当日 `outputs/runs/YYYY-MM-DD/` 目录后重建（不影响 `outputs/ledger/`）。

### 分阶段运行

调试或部分重跑时可单独运行子阶段：

```powershell
# 仅预测
python main.py --pipeline ledger_predict --date 2026-02-24

# 仅权重学习（需先跑过 predict）
python main.py --pipeline ledger_weight --date 2026-02-24

# 仅融合（需先跑过 weight）
python main.py --pipeline ledger_fuse --date 2026-02-24

# 仅分类器（需先跑过 fuse）
python main.py --pipeline ledger_classifier --date 2026-02-24
```

---

## 命令行参数说明

### 基础参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `YYYY-MM-DD` (positional) | — | 单日预测日期 |
| `YYYY-MM-DD YYYY-MM-DD` (positional) | — | 时间段预测（自动激活 range 模式） |
| `--pipeline` | `ledger_full` | 选择 pipeline（见下方可选值） |
| `--date` | — | 单日日期 |
| `--start` | — | 时间段开始日期 |
| `--end` | — | 时间段结束日期 |
| `--data-path` | `data/shandong_pmos_hourly.xlsx` | 输入数据路径 |
| `--seed` | `42` | 随机种子 |
| `--deterministic` | 未启用 | 开启确定性运行（可复现） |
| `--force` | 未启用 | 强制重跑，跳过缓存；同时清空当日 `outputs/runs/YYYY-MM-DD/` |
| `--runs-root` | `outputs/runs` | 每日运行输出根目录 |
| `--ledger-root` | `outputs/ledger` | Ledger 根目录 |

**`--pipeline` 可选值：**

| 值 | 用途 |
|----|------|
| `ledger_full` | **默认**。完整单日链路（predict → weight → fuse → classifier → final） |
| `ledger_full_range` | 时间段预测（逐日循环 `ledger_full`） |
| `ledger_predict` | 仅模型预测 |
| `ledger_weight` | 仅权重学习 |
| `ledger_fuse` | 仅融合 |
| `ledger_classifier` | 仅分类器 |
| `ledger_backfill` | 历史回填（初始化 ledger，见 Backfill 章节） |
| `ledger_smoke` | 小参数快速测试（不污染正式 ledger） |
| `evaluate` | 评估（调试用） |
| `sync_dataset` | 同步数据集（调试用） |

### 资源参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--max-cpu-workers` | `2` | CPU 模型并行数 |
| `--max-gpu-workers` | `1` | GPU 模型并行数（串行防 OOM） |

### 模型参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--realtime-cutoff-hour` | `14` | Realtime 预测数据截止小时（D-1） |
| `--timemixer-epochs` | `80` | TimeMixer 训练轮数 |
| `--timemixer-patience` | `15` | TimeMixer early stopping patience |
| `--timemixer-batch-size` | `16` | TimeMixer batch size |
| `--timemixer-seeds` | `42` | TimeMixer legacy seeds |
| `--target` | `both` | ⚠️ **当前 `ledger_predict` 未使用此参数**。保留/开发中。 |
| `--models` | `all` | ⚠️ **当前 `ledger_predict` 未使用此参数**。保留/开发中。 |

### 融合 / 容错参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--allow-missing-models` | 未启用 | 允许部分模型失败，继续后续阶段 |
| `--allow-equal-weight-fallback` | 未启用 | 缺少权重时允许等权 fallback |
| `--strict-classifier` | 未启用 | 分类器失败时让 `ledger_full` 整体失败 |
| `--recent-week-boost` | 启用 | 最近一周 day_gate boost（默认开启） |
| `--no-recent-week-boost` | — | 关闭最近一周 day_gate boost |
| `--recent-week-max-gate` | `0.85` | 最近一周最大 day_gate |

### 时间段参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--continue-on-error` | 未启用 | 某天失败后继续后续日期 |
| `--skip-existing-final` | 未启用 | 跳过已有且验证有效的最终输出 |
| `--no-range-preflight` | 未启用 | 跳过 range 前置检查 |

### Smoke 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--smoke-training-months` | `3` | Smoke 训练月数 |
| `--smoke-timemixer-epochs` | `3` | Smoke TimeMixer 轮数 |
| `--smoke-timemixer-patience` | `1` | Smoke TimeMixer patience |

### Legacy 兼容参数（正常交付不需要）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--epf-v1-root` | `None` | 外部 EPF v1.0 根目录。仅用于旧版兼容。正常交付使用 bundled `lightGBM/` + `TimesFMBackend/` |
| `--epf-v1-mode` | `exact` | 旧版 adapter 模式。正常用户不需要修改 |
| `--allow-v2-fallback` | 未启用 | 旧版 fallback。正常用户不需要开启 |

---

## 常见问题

**Q: Do I need the developer's old EPF folder?**
A: No. The delivery version is self-contained. `lightGBM/` and `TimesFMBackend/` are bundled in this repository. `--epf-v1-root` is only for legacy compatibility.

**Q: CUDA out of memory？**
A: GPU 队列默认串行（`max_gpu_workers=1`）。如果仍有 OOM，减小 batch size（`--timemixer-batch-size 8`）。

**Q: 如何只重跑 fuse，不重跑模型？**
A: 直接运行 `ledger_fuse`，它会读取已有的 prediction 和 weight 文件。

**Q: `data/` 和 `models/` 需要手动下载吗？**
A: `data/` 需要自行放置输入文件。`models/` 首次运行时自动下载/生成权重（如 TimeMixer `.pt` checkpoint、TimesFM huggingface_hub 缓存），不需要手动操作。

**Q: 目录从 `TF/` 重命名为 `TimesFMBackend/` 了吗？**
A: 是的。`TF/` 已重命名为 `TimesFMBackend/`，避免与 TensorFlow 缩写混淆。`TimesFMBackend/` 包含完整 timesfm_2p5 PyTorch+Flax 代码，**不是** TensorFlow。

**Q: 没有 30 天 ledger 怎么办？**
A: 从 `fixtures/seed_ledger/` 复制 seed ledger（`cp -r fixtures/seed_ledger/* outputs/ledger/`），或运行 `ledger_backfill` 回填。详见 [Backfill 章节](#什么时候需要-backfill--历史回填)。

**Q: 在哪里看完整输出结构？**
A: 见 [`docs/OUTPUT_CONVENTION.md`](docs/OUTPUT_CONVENTION.md)。

---

## 许可

MIT License
