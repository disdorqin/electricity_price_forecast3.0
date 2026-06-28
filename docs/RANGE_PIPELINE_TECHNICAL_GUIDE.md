# Range Pipeline Technical Guide

## 1. 时间段模式是什么

时间段模式（Range Mode）对 `[start, end]` 闭区间内的每一天逐日执行完整五阶段 `ledger_full` 链路：

```
ledger_predict → ledger_weight → ledger_fuse → ledger_classifier → final_outputs
```

### 支持的命令

```powershell
# 两个 positional 参数自动触发 range 模式（推荐）
python main.py 2026-02-24 2026-02-28

# 显式指定 pipeline + start/end
python main.py --pipeline ledger_full_range --start 2026-02-24 --end 2026-02-28
```

### 五阶段链路说明

| 阶段 | Pipeline | 功能 |
|------|----------|------|
| 1 | `ledger_predict` | 跑全部 7 个模型（CPU 并行 + GPU 串行），每个模型生成 24 小时预测；追加到 prediction ledger |
| 2 | `ledger_weight` | 读取 D-30 到 D-1 的 prediction ledger + actual ledger，学习每个 (task, period) 的 BGEW 权重 |
| 3 | `ledger_fuse` | 根据当天 weights.csv 对各模型做逐小时加权融合 |
| 4 | `ledger_classifier` | 仅对 realtime 融合结果做极端价格分类校正（-80.00） |
| 5 | `final_outputs` | 合并 dayahead + realtime 修正结果，生成 submission_ready.csv |

---

## 2. 输入前置条件

### data_path 要求

- 默认路径：`data/shandong_pmos_hourly.xlsx`
- 必须包含小时级电价数据及必要协变量
- 数据字段详见 `README.md` 的数据要求章节

### Start 日期前必须有至少 30 天完整 Ledger

权重学习需要 D-30 到 D-1 的预测+实际值配对。如果 `outputs/ledger/` 为空，range 模式无法执行。

**快速准备（推荐）：** 从 `fixtures/seed_ledger/` 复制：

```bash
mkdir -p outputs/ledger
cp -r fixtures/seed_ledger/* outputs/ledger/
```

**完整 backfill：**

```powershell
python main.py --pipeline ledger_backfill --start 2026-01-25 --end 2026-02-23 --data-path data/shandong_pmos_hourly.xlsx --seed 42 --deterministic
```

### 四个 Ledger 文件位置

| 文件 | 路径 |
|------|------|
| Dayahead prediction ledger | `outputs/ledger/dayahead/prediction/prediction_ledger.parquet` |
| Dayahead actual ledger | `outputs/ledger/dayahead/actual/actual_ledger.parquet` |
| Realtime prediction ledger | `outputs/ledger/realtime/prediction/prediction_ledger.parquet` |
| Realtime actual ledger | `outputs/ledger/realtime/actual/actual_ledger.parquet` |

### 每个 Ledger 应该包含什么

| Ledger | 内容 | 每行 |
|--------|------|------|
| `dayahead/prediction` | 3 模型 × 24h × N 天 日前预测 | (task, business_day, hour_business, model_name, y_pred) |
| `dayahead/actual` | 24h × N 天 日前实际值 | (task, business_day, hour_business, y_true) |
| `realtime/prediction` | 4 模型 × 24h × N 天 实时预测 | (task, business_day, hour_business, model_name, y_pred) |
| `realtime/actual` | 24h × N 天 实时实际值 | (task, business_day, hour_business, y_true) |

---

## 3. 输出目录说明

### 每日输出目录

```
outputs/runs/YYYY-MM-DD/
├── run_manifest.json               # 五阶段状态、row counts、配置
├── dayahead/
│   ├── prediction/                 # 各模型原始预测（cache key）
│   ├── weight/                     # BGEW 权重
│   ├── fuse/                       # 融合结果
│   └── final/dayahead_final_predictions.csv
├── realtime/
│   ├── prediction/
│   ├── weight/
│   ├── fuse/
│   └── final/                      # 含 classifier_report.json
└── final/submission_ready.csv      # 最终交付
```

### 区间输出目录

```
outputs/runs/range_START_to_END/
├── range_manifest.json             # 区间级运行元信息
└── range_summary.csv               # CSV 摘要
```

### range_manifest.json 字段解释

| 字段 | 类型 | 说明 |
|------|------|------|
| pipeline | str | `"ledger_full_range"` |
| start_date | str | 区间开始日期 |
| end_date | str | 区间结束日期 |
| total_days | int | 区间总天数 |
| completed_days | int | 成功完成的天数 |
| failed_days | int | 失败的天数 |
| skipped_days | int | 被跳过的天数 |
| degraded_days | int | 降级交付的天数 |
| status | str | `complete` / `complete_with_degraded_days` / `partial` / `failed` / `preflight_failed` / `interrupted` / `all_skipped` |
| delivery_status | str | `NORMAL` / `DEGRADED_DELIVERED` / `FAILED_NO_DELIVERY` |
| daily_results | array | 每日执行结果列表 |
| errors | array | 区间级错误列表 |
| warnings | array | 区间级警告列表 |
| started_at | str (ISO 8601) | 区间开始时间 |
| completed_at | str (ISO 8601) | 区间完成时间 |
| preflight_report | dict | validate_ledger_window 结果（preflight_failed 时） |
| note | str | 针对 preflight_failed 的修复建议 |

**daily_results 每个元素的字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| date | str | 业务日期 |
| status | str | `complete` / `failed` / `error` / `skipped` |
| delivery_status | str | 当日交付状态 |
| postflight_status | str | postflight 结果（PASS/FAIL/NOT RUN） |
| fallback_used | bool | 是否使用了 emergency fallback |
| started_at | str | 当天执行开始时间 |
| completed_at | str | 当天执行完成时间 |
| duration_seconds | float | 当天执行耗时 |
| manifest_path | str | 当日 run_manifest.json 路径 |
| submission_ready_path | str | 当日 submission_ready.csv 路径 |
| stage_statuses | dict | 五阶段各自状态 |
| errors_count | int | 当天错误数 |
| warnings_count | int | 当天警告数 |
| skip_reason | str | 跳过原因（仅 skipped 时有） |

### range_summary.csv 字段解释

| 字段 | 说明 |
|------|------|
| date | 业务日期 |
| status | 当天状态 |
| submission_ready_exists | 是否存在 final 交付文件 |
| submission_ready_rows | final 交付文件行数（应为 24） |
| errors_count | 当天错误数 |
| warnings_count | 当天警告数 |
| manifest_path | 当日 manifest 路径 |
| submission_ready_path | 当日交付文件路径 |

### final/submission_ready.csv 字段解释

| 字段 | 说明 |
|------|------|
| business_day | 业务日期 |
| ds | 时间戳（小时级） |
| hour_business | 业务小时 (1..24) |
| period | 时段 (1_8 / 9_16 / 17_24) |
| dayahead_price | 日前融合预测价格 |
| realtime_price | 实时修正后预测价格 |

- 严格 24 行，hour_business 1..24 不重复
- hour 24 的 ds = D+1 00:00
- 无 `_x`/`_y` 后缀列

---

## 4. 稳定性机制

### Range Preflight

默认开启。在正式执行区间前检查：

1. `data_path` 存在
2. `ledger_root` 存在
3. 四个 ledger parquet 文件存在且可读
4. start 日期 D-30..D-1 窗口内每一天都存在
5. 每天 hour_business 覆盖 1..24
6. actual ledger 每天 24 行
7. prediction ledger 每天每模型 24 行
8. 模型覆盖完整（dayahead 3 模型、realtime 4 模型）

如果 preflight 失败，range 的 manifest 会写入 `status: preflight_failed` + 详细错误 + 修复建议。使用 `--no-range-preflight` 跳过。

### Skip Existing Final

`--skip-existing-final` 会验证已存在的输出是否有效，判断条件：

1. `submission_ready.csv` 存在
2. 列完全匹配（6 列固定顺序）
3. 行数严格 24
4. hour_business 精确 1..24
5. 无重复 hour
6. business_day 匹配 target_date
7. hour 24 的 ds 为 D+1 00:00
8. price 列非空且 numeric
9. 无 `_x`/`_y` 后缀列
10. `run_manifest.json` 存在
11. 五阶段全部 complete
12. manifest errors 为空

任一条件不满足，当天会重跑而非跳过。

### Continue on Error

`--continue-on-error` 让区间在某天失败后继续下一天。最终 status 为 `partial`。

### 状态含义

| 状态 | 含义 |
|------|------|
| `complete` | 所有日期成功或跳过 |
| `complete_with_degraded_days` | 所有日期完成但有降级交付 |
| `partial` | 部分日期成功、部分失败（`--continue-on-error`） |
| `failed` | 某天失败后立即停止（默认） |
| `preflight_failed` | Preflight 校验未通过，未执行任何日期 |
| `interrupted` | 用户 Ctrl+C 中断 |
| `all_skipped` | 所有日期均被跳过（`--skip-existing-final`） |

### 交付状态 (delivery_status)

自 v2.1.1 起，每个 `run_manifest.json` 和 `range_manifest.json` 包含 `delivery_status`：

| 状态 | 含义 |
|------|------|
| `NORMAL` | 正常五阶段完成，postflight 全部通过，未使用 fallback |
| `DEGRADED_DELIVERED` | 正常链路或 postflight 有问题，但 emergency fallback 生成了可用输出 |
| `FAILED_NO_DELIVERY` | 正常链路失败且 fallback 失败，无可用交付文件 |

### Exit Code

| 条件 | Code | 含义 |
|------|------|------|
| `delivery_status == NORMAL` | 0 | 正常成功 |
| `delivery_status == DEGRADED_DELIVERED` | 2 | 有输出但降级（warning 级别） |
| 其他失败 | 1 | 硬错误，无可用输出 |

### Postflight 机制

`ledger_full` 五阶段执行完毕后自动执行 postflight：

1. 调用 `validate_daily_submission()` 严格检查 `submission_ready.csv`
2. PASS → `delivery_status = NORMAL`
3. FAIL → 尝试 `try_emergency_fallback()` 生成应急交付
4. Fallback 成功 → `delivery_status = DEGRADED_DELIVERED`
5. Fallback 失败 → `delivery_status = FAILED_NO_DELIVERY`
6. 检查 next-day ledger readiness
7. 生成 `delivery_report.md` + 终端 DAILY DELIVERY REPORT

### Emergency Fallback

当正常链路无法交付时，系统使用历史同小时中位数生成应急文件：

- 优先最近 7 个 business_day 的同 hour_business median
- 如果 7 天不足，用 30 天；再不足用全历史
- 生成标准 24 行 `submission_ready.csv`
- 写入 `final/fallback_report.md` / `final/fallback_report.json`
- **不写入** prediction ledger，避免污染后续权重学习
- 使用 fallback 后需要 `--force` 重跑正常链路恢复 ledger continuity

### 稳定性 Synthetic 测试

```shell
python scripts/check_delivery_stability.py
```

不依赖 GPU / 模型 / 真实数据，独立验证：
- validate_daily_submission PASS/FAIL
- validate_ledger_window PASS/FAIL
- emergency_fallback 生成降级交付
- exit code 映射

---

## 5. 常见错误排查

### Data path not found

**错误信息：** `Data path not found: data/shandong_pmos_hourly.xlsx`

| 项目 | 内容 |
|------|------|
| 定位 | 检查 `--data-path` 参数指向的文件 |
| 看哪个文件 | `range_manifest.json` → `preflight_errors` |
| 看哪个日志 | 控制台 preflight ERROR 日志 |
| 怎么修 | 将 `shandong_pmos_hourly.xlsx` 放到 `data/`，或传 `--data-path` 指定正确路径 |
| 验证命令 | `python scripts/env_check.py` |

### Ledger root not found

**错误信息：** `Ledger root not found: outputs/ledger`

| 项目 | 内容 |
|------|------|
| 定位 | 检查 `outputs/ledger/` 是否存在 |
| 看哪个文件 | `range_manifest.json` → `preflight_errors` |
| 看哪个日志 | 控制台 preflight ERROR |
| 怎么修 | `cp -r fixtures/seed_ledger/* outputs/ledger/` 或运行 backfill |
| 验证命令 | `ls outputs/ledger/dayahead/prediction/prediction_ledger.parquet` |

### Missing prediction ledger

**错误信息：** `Ledger file not found: outputs/ledger/dayahead/prediction/prediction_ledger.parquet`

| 项目 | 内容 |
|------|------|
| 定位 | 检查四个 ledger 文件各自是否存在 |
| 看哪个文件 | `range_manifest.json` → `preflight_errors` 指出具体文件 |
| 看哪个日志 | 控制台 preflight ERROR |
| 怎么修 | 运行 backfill 或用 seed ledger |
| 验证命令 | `ls -la outputs/ledger/*/prediction/prediction_ledger.parquet` |

### Missing actual ledger

同 prediction ledger，只是目录换为 `actual/`。

### D-30..D-1 ledger coverage insufficient

**错误信息：** `Ledger xxx: missing N day(s) in window YYYY-MM-DD..YYYY-MM-DD`

| 项目 | 内容 |
|------|------|
| 定位 | Preflight 计算 start 日期 D-30 到 D-1，检查窗口内天数 |
| 看哪个文件 | `range_manifest.json` → `preflight_errors` |
| 看哪个日志 | 控制台 preflight ERROR |
| 怎么修 | 运行更多 backfill 天数字补充 ledger，或使用 seed ledger |
| 验证命令 | `python -c "import pandas as pd; df=pd.read_parquet('outputs/ledger/dayahead/prediction/prediction_ledger.parquet'); print(df['target_day'].nunique(), 'days')"` |

### Missing model rows

**错误信息：** `Ledger xxx: model 'xxx' on YYYY-MM-DD has N/24 rows`

| 项目 | 内容 |
|------|------|
| 定位 | Preflight 检查 D-30..D-1 窗口内每天每模型行数 |
| 看哪个文件 | `range_manifest.json` → `preflight_errors` |
| 看哪个日志 | 控制台 preflight ERROR |
| 怎么修 | 对该日期重新运行 `ledger_predict`，或重新 backfill 缺失日期 |
| 验证命令 | 检查 `outputs/runs/YYYY-MM-DD/{task}/prediction/` 是否存在该模型 CSV |

### Missing hour_business

**错误信息：** `Ledger xxx: day YYYY-MM-DD has N/24 hour rows`

| 项目 | 内容 |
|------|------|
| 定位 | Preflight 检查每天是否有 24 个不同 hour_business |
| 看哪个文件 | `range_manifest.json` → `preflight_errors` |
| 看哪个日志 | 控制台 preflight ERROR |
| 怎么修 | 补充缺失小时的数据后重新 backfill |
| 验证命令 | `python -c "import pandas as pd; df=pd.read_parquet('outputs/ledger/dayahead/actual/actual_ledger.parquet'); print(df[df['target_day']=='2026-02-24']['hour_business'].nunique())"` |

### Daily manifest missing

**错误信息：** `run_manifest.json not found`

| 项目 | 内容 |
|------|------|
| 定位 | 检查 `outputs/runs/YYYY-MM-DD/run_manifest.json` |
| 看哪个文件 | `range_manifest.json` → `daily_results` 中对应日期的 `manifest_path` |
| 看哪个日志 | 当日运行日志（控制台输出） |
| 怎么修 | 对该日期单独运行 `python main.py YYYY-MM-DD` 重新生成 |
| 验证命令 | `ls outputs/runs/YYYY-MM-DD/run_manifest.json` |

### Stage failed

**错误信息：** `stage 'xxx' status=failed, expected 'complete'`

| 项目 | 内容 |
|------|------|
| 定位 | 查看当日 `run_manifest.json` → `stages` → 对应 stage |
| 看哪个文件 | `outputs/runs/YYYY-MM-DD/run_manifest.json` |
| 看哪个日志 | 当日控制台日志中对应 stage 的错误 |
| 怎么修 | 针对失败阶段单独运行修复：`python main.py --pipeline <stage> --date YYYY-MM-DD` |
| 验证命令 | `python scripts/verify_final_pipeline.py --date YYYY-MM-DD --runs-root outputs/runs` |

### submission_ready invalid

**错误信息：** 各种 submission_ready 校验失败

| 项目 | 内容 |
|------|------|
| 定位 | 查看 `outputs/runs/YYYY-MM-DD/final/submission_ready.csv` 内容 |
| 看哪个文件 | `outputs/runs/YYYY-MM-DD/run_manifest.json` → `stages.final_outputs` |
| 看哪个日志 | 当日日志中 `submission_ready.csv` 输出 |
| 怎么修 | 重新运行 `python main.py YYYY-MM-DD` 重新生成 final |
| 验证命令 | `python scripts/verify_final_pipeline.py --date YYYY-MM-DD --runs-root outputs/runs` |

### Classifier failed

**错误信息：** `ledger_classifier status=failed`

| 项目 | 内容 |
|------|------|
| 定位 | 查看 `outputs/runs/YYYY-MM-DD/realtime/final/classifier_report.json` |
| 看哪个文件 | `outputs/runs/YYYY-MM-DD/run_manifest.json` → `stages.ledger_classifier` |
| 看哪个日志 | 当日日志中 classifier 阶段的输出 |
| 怎么修 | 默认非严格模式（`--strict-classifier` 未开启）会继续执行，不影响最终输出。如果需要闭环，添加 `--strict-classifier` 让全链路失败。 |
| 验证命令 | `python -c "import json; r=json.load(open('outputs/runs/YYYY-MM-DD/realtime/final/classifier_report.json')); print(r['success'], r['n_corrections'])"` |

### GPU OOM

**错误信息：** CUDA out of memory

| 项目 | 内容 |
|------|------|
| 定位 | GPU queue 串行运行 TimeMixer 和 RT916 |
| 看哪个日志 | 控制台 GPU 模型启动时的 torch 错误 |
| 怎么修 | 减小 `--timemixer-batch-size 8`，确保 `--max-gpu-workers 1` |
| 验证命令 | `nvidia-smi` 查看 GPU 显存使用 |

### pyarrow/parquet read error

**错误信息：** `Cannot read parquet ledger`

| 项目 | 内容 |
|------|------|
| 定位 | Preflight 尝试读取 ledger parquet 文件时失败 |
| 看哪个文件 | `<ledger_path>` 指向的 parquet 文件 |
| 看哪个日志 | preflight ERROR |
| 怎么修 | `pip install pyarrow`，或重新生成 ledger |
| 验证命令 | `python -c "import pandas as pd; pd.read_parquet('outputs/ledger/dayahead/prediction/prediction_ledger.parquet')"` |

---

## 6. 日志与排查路径

### 单日运行日志

| 路径 | 内容 |
|------|------|
| `outputs/runs/YYYY-MM-DD/run_manifest.json` | 五阶段状态、row counts、配置、错误、交付状态 |
| `outputs/runs/YYYY-MM-DD/delivery_report.json` | 交付报告（结构化 JSON） |
| `outputs/runs/YYYY-MM-DD/delivery_report.md` | 交付报告（Markdown 可读） |
| `outputs/runs/YYYY-MM-DD/final/fallback_report.json` | Fallback 报告（仅 DEGRADED_DELIVERED 时） |
| `outputs/runs/YYYY-MM-DD/final/fallback_report.md` | Fallback 报告可读版 |
| `outputs/runs/YYYY-MM-DD/dayahead/` | 日前预测、权重、融合、最终输出 |
| `outputs/runs/YYYY-MM-DD/realtime/` | 实时预测、权重、融合、分类器报告 |
| `outputs/runs/YYYY-MM-DD/final/submission_ready.csv` | 最终交付文件 |

### 区间运行日志

| 路径 | 内容 |
|------|------|
| `outputs/runs/range_START_to_END/range_manifest.json` | 区间元信息、每日状态、错误汇总、交付状态 |
| `outputs/runs/range_START_to_END/range_delivery_report.json` | 区间交付报告 |
| `outputs/runs/range_START_to_END/range_delivery_report.md` | 区间交付报告可读版 |
| `outputs/runs/range_START_to_END/range_summary.csv` | 区间 CSV 摘要 |

### Ledger 数据

| 路径 | 内容 |
|------|------|
| `outputs/ledger/dayahead/prediction/prediction_ledger.parquet` | 日前预测累积账本 |
| `outputs/ledger/dayahead/actual/actual_ledger.parquet` | 日前实际值累积账本 |
| `outputs/ledger/realtime/prediction/prediction_ledger.parquet` | 实时预测累积账本 |
| `outputs/ledger/realtime/actual/actual_ledger.parquet` | 实时实际值累积账本 |

### 标准排查流程

1. 查看 `range_manifest.json` → `status` 和 `errors`
2. 如果有错误日期，查看该日期 `run_manifest.json` → `stages` → 找出失败阶段
3. 查看失败阶段的 error 信息
4. 修复后对当天单独重跑：`python main.py YYYY-MM-DD`
5. 重新运行验证：`python scripts/verify_range_pipeline.py --start START --end END`

---

## 7. 推荐验收流程

```powershell
# 0. 稳定性 synthetic 测试（不依赖 GPU/模型/数据）
python scripts/check_delivery_stability.py

# 1. 运行时间段预测
python main.py 2026-02-24 2026-02-28 --data-path data/shandong_pmos_hourly.xlsx --seed 42 --deterministic

# 2. 验证区间输出
python scripts/verify_range_pipeline.py --start 2026-02-24 --end 2026-02-28 --runs-root outputs/runs
```

如果区间包含降级交付的天，使用 `--allow-degraded`：

```powershell
python scripts/verify_range_pipeline.py --start 2026-02-24 --end 2026-02-28 --allow-degraded
```

如果希望快速跳过已验证日期：

```powershell
python main.py 2026-02-24 2026-02-28 --skip-existing-final
```

如果需要在某天失败后继续：

```powershell
python main.py 2026-02-24 2026-02-28 --continue-on-error
```

如果 preflight 持续不通过但确定数据没问题：

```powershell
python main.py 2026-02-24 2026-02-28 --no-range-preflight
```
