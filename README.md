# Electricity Forecast Delivery Pipeline v2.5

山东电力现货价格预测交付链路：**7 模型预测 + Ledger 自适应动态权重融合 + Realtime 极端价格分类校正 + 最终交付校验**。

当前版本已经完成 2026-07-03 正式陪跑验收：五阶段全部 `complete`，`postflight=PASS`，`delivery_status=NORMAL`，`exit_code=0`，`fallback_used=false`，最终 `submission_ready.csv` 为 24 行、0 NaN。

---

## 1. 正式链路

```text
输入小时级山东电力现货数据
    ↓
ledger_predict：7 个模型预测目标日 24 小时
    ↓
ledger_weight：从 ledger 中自适应选择最近 30 个完整训练日，学习动态融合权重
    ↓
ledger_fuse：按 task / period / model 权重融合
    ↓
ledger_classifier：仅对 realtime 极端低价做分类校正
    ↓
final_outputs：生成 final/submission_ready.csv
    ↓
postflight：校验 24 行、6 列、无 NaN、manifest 完整
```

五阶段顺序：

```text
ledger_predict → ledger_weight → ledger_fuse → ledger_classifier → final_outputs
```

最终交付文件：

```text
outputs/runs/YYYY-MM-DD/final/submission_ready.csv
```

标准列：

```text
business_day, ds, hour_business, period, dayahead_price, realtime_price
```

---

## 2. 当前交付状态

| 模块 | 状态 | 说明 |
|---|---|---|
| 数据同步 `sync_dataset` | PASS | 支持 db / http / local / auto |
| LightGBM target-day NaN | FIXED | 目标日 `日前电价` 未发布时保留推理行，不再 NoneType |
| SGDFNet target-day NaN | FIXED | `da_anchor` 缺失时使用历史同小时中位数 fallback |
| DA/RT adaptive weight days | FIXED | Dayahead 和 Realtime 都从 D-1 向前找最近 30 个完整训练日 |
| hour_business 严格校验 | FIXED | prediction / actual 必须严格为 `{1..24}` |
| `age_days` 位置计算 | FIXED | adaptive 选中日按列表位置计算，最近完整日为 1 |
| Windows UTF-8 manifest | FIXED | JSON 读写显式 `encoding="utf-8"` |
| 回归测试 | PASS | adaptive 40/40、stability 29/29、NaN regression 16/16、sync 41/41 |
| 2026-07-03 正式陪跑 | PASS | NORMAL / exit 0 / postflight PASS / 24 行 0 NaN |

---

## 3. Adaptive Complete Training Days

`ledger_weight` 对 **Dayahead 和 Realtime 都使用同一套自适应训练日选择逻辑**：

1. 从目标日 `D-1` 开始向前扫描；
2. 跳过不完整日；
3. 收集最近 30 个完整训练日；
4. 选中日按从近到远排序；
5. `selected_days[0] → age_days=1`，最近完整日权重最高；
6. 在 `--weight-max-lookback-days` 范围内凑不够 30 天则失败，并在 manifest/log 中写明 skipped days 和 errors。

完整日定义：

| Task | Prediction 要求 | Actual 要求 |
|---|---|---|
| Dayahead | 3 模型 × `hour_business={1..24}`，`y_pred` 无 NaN | `hour_business={1..24}`，`y_true` 无 NaN |
| Realtime | 4 模型 × `hour_business={1..24}`，`y_pred` 无 NaN | `hour_business={1..24}`，`y_true` 无 NaN |

模型列表：

```text
Dayahead: lightgbm, timesfm, timemixer
Realtime: timesfm, sgdfnet, timemixer, rt916
```

训练表期望规模：

```text
Dayahead: 30 × 3 × 24 = 2160 rows
Realtime: 30 × 4 × 24 = 2880 rows
Actual: 每个 task 30 × 24 = 720 rows
```

`validate_ledger_window()` 仍保留为 audit-only 检查，但不再作为 Dayahead hard gate。真正决定是否能学习权重的是 `select_complete_training_days()`。

---

## 4. 2026-07-03 验收结果

最终验收：

```text
ledger_predict: complete
ledger_weight: complete
ledger_fuse: complete
ledger_classifier: complete
final_outputs: complete
postflight: PASS
delivery_status: NORMAL
exit_code: 0
fallback_used: false
```

最终文件：

```text
outputs/runs/2026-07-03/final/submission_ready.csv
```

结果摘要：

```text
rows: 24
NaN: 0
dayahead_price: 149.66 ~ 456.88
realtime_price: 33.36 ~ 438.90
```

权重学习摘要：

| 指标 | Dayahead | Realtime |
|---|---:|---:|
| selected_count | 30 | 30 |
| training_rows | 2160 | 2880 |
| age_days | 1..30 | 1..30 |
| weights NaN | 0 / 9 | 0 / 12 |

注意：本次验收使用当前 ledger 中可用的最近 30 个完整训练日。由于本地 ledger 中 `2026-02-26 ~ 2026-07-02` 区间 prediction ledger 为空，adaptive 逻辑会跳过这些不完整日，选中 `2026-01-27 ~ 2026-02-25` 的完整历史。该行为符合当前设计，不是代码错误。正式连续生产建议补齐更近日期的 ledger，以提升权重时效性。

---

## 5. 快速开始

### 5.1 安装环境

```bash
conda create -n epf-2 python=3.10 -y
conda activate epf-2
pip install -r requirements.txt
```

Windows + CUDA 已验证。GPU 模型建议保持串行，避免 OOM。

### 5.2 准备数据

默认输入：

```text
data/shandong_pmos_hourly.xlsx
```

必需字段：

```text
时刻 / ds / 时间
日前电价
实时电价
```

自定义路径：

```bash
--data-path path/to/shandong_pmos_hourly.xlsx
```

### 5.3 同步数据

推荐两步式，便于区分数据问题和模型问题：

```bash
python main.py --pipeline sync_dataset --sync-source auto --force-sync --require-fresh-data
python main.py YYYY-MM-DD --data-path data/shandong_pmos_hourly.xlsx
```

也可以一条命令：

```bash
python main.py YYYY-MM-DD --sync-data-before-run --require-fresh-data
```

---

## 6. 正式运行命令

Linux / macOS：

```bash
python main.py 2026-07-03 \
  --data-path data/shandong_pmos_hourly_0702.xlsx \
  --ledger-root outputs/ledger \
  --weight-max-lookback-days 180 \
  --max-cpu-workers 2 \
  --max-gpu-workers 1 \
  --seed 42 \
  --deterministic
```

Windows PowerShell：

```powershell
python main.py 2026-07-03 `
  --data-path data/shandong_pmos_hourly_0702.xlsx `
  --ledger-root outputs/ledger `
  --weight-max-lookback-days 180 `
  --max-cpu-workers 2 `
  --max-gpu-workers 1 `
  --seed 42 `
  --deterministic
```

成功标准：

```text
delivery_status = NORMAL
exit_code = 0
postflight = PASS
final/submission_ready.csv = 24 rows, 0 NaN
fallback_used = false
```

不推荐把下面参数用于正式 NORMAL 交付：

```text
--allow-missing-models
--allow-equal-weight-fallback
--no-range-preflight
```

这些参数只用于诊断或应急。

---

## 7. Ledger 目录

默认 ledger 根目录：

```text
outputs/ledger
```

也可指定：

```bash
--ledger-root <your_ledger_root>
```

核心文件：

| 类型 | 路径 |
|---|---|
| Dayahead prediction | `outputs/ledger/dayahead/prediction/prediction_ledger.parquet` |
| Dayahead actual | `outputs/ledger/dayahead/actual/actual_ledger.parquet` |
| Realtime prediction | `outputs/ledger/realtime/prediction/prediction_ledger.parquet` |
| Realtime actual | `outputs/ledger/realtime/actual/actual_ledger.parquet` |

权重学习只读取 ledger，不直接读取 `outputs/runs`。每日 `ledger_predict` 会把当日预测追加到 prediction ledger；actual ledger 会按可得实际值更新。

---

## 8. 验证命令

基础回归：

```bash
python -m py_compile main.py cli/parser.py pipelines/ledger_weight.py pipelines/prediction_ledger.py pipelines/delivery_quality.py pipelines/ledger_classifier.py
python scripts/check_adaptive_realtime_weight_days.py
python scripts/check_delivery_stability.py
python scripts/check_target_day_nan_regression.py
python scripts/check_sync_dataset.py
```

期望：

```text
check_adaptive_realtime_weight_days.py = 40/40 PASS
check_delivery_stability.py = 29/29 PASS
check_target_day_nan_regression.py = 16/16 PASS
check_sync_dataset.py = 41/41 PASS
```

检查 adaptive training days：

```bash
python - <<'PY'
from pathlib import Path
from pipelines.ledger_weight import select_complete_training_days, DAYAHEAD_MODELS, REALTIME_MODELS
import json
for task, models in [('dayahead', DAYAHEAD_MODELS), ('realtime', REALTIME_MODELS)]:
    result = select_complete_training_days(
        task=task,
        target_date='2026-07-03',
        ledger_root=Path('outputs/ledger'),
        expected_models=models,
        required_days=30,
        max_lookback_days=180,
    )
    print(task)
    print(json.dumps({
        'status': result['status'],
        'selected_count': result['selected_count'],
        'latest_selected_day': result['selected_days'][0] if result['selected_days'] else None,
        'skipped_count': len(result['skipped_days']),
        'errors': result['errors'],
    }, ensure_ascii=False, indent=2))
PY
```

---

## 9. 如果需要补 ledger

如果 adaptive 在 lookback 范围内凑不够 30 个完整训练日，需要 backfill：

```bash
python main.py --pipeline ledger_backfill \
  --start 2026-06-03 \
  --end 2026-07-02 \
  --data-path data/shandong_pmos_hourly_0702.xlsx \
  --max-cpu-workers 2 \
  --max-gpu-workers 1 \
  --seed 42 \
  --deterministic \
  --force
```

若 `D-1` 当天 actual 不完整，adaptive 会自动跳过该日，并继续向前找完整训练日。

---

## 10. 输出文件

| 文件 | 说明 |
|---|---|
| `outputs/runs/YYYY-MM-DD/final/submission_ready.csv` | 最终交付文件 |
| `outputs/runs/YYYY-MM-DD/run_manifest.json` | 五阶段运行元信息 |
| `outputs/runs/YYYY-MM-DD/delivery_report.md` | 交付报告 |
| `outputs/runs/YYYY-MM-DD/dayahead/weight/weights.csv` | Dayahead 融合权重 |
| `outputs/runs/YYYY-MM-DD/realtime/weight/weights.csv` | Realtime 融合权重 |
| `outputs/runs/YYYY-MM-DD/{task}/fuse/fused_predictions.csv` | 融合结果 |
| `outputs/runs/YYYY-MM-DD/realtime/final/realtime_final_predictions_corrected.csv` | 分类器校正后 realtime |

---

## 11. Delivery Status

| delivery_status | exit code | 含义 |
|---|---:|---|
| NORMAL | 0 | 五阶段正常完成，postflight PASS |
| DEGRADED_DELIVERED | 2 | 正常链路失败，但 emergency fallback 生成可交付文件 |
| FAILED_NO_DELIVERY | 1 | 正常链路和 fallback 均失败，无可用交付 |

正式验收优先使用 NORMAL。若使用 DEGRADED，必须说明 fallback 原因和后续修复计划。

---

## 12. Troubleshooting

| 问题 | 判断 | 处理 |
|---|---|---|
| LightGBM `NoneType` | 旧版本未兼容目标日 NaN | 拉取最新 main |
| SGDFNet 24 行 NaN | 旧版本 `da_anchor` 为 NaN | 拉取最新 main |
| `ledger_weight` 凑不够 30 天 | ledger 不足或 lookback 太短 | 补 ledger / backfill / 提高 `--weight-max-lookback-days` |
| `UnicodeDecodeError: gbk` | Windows 默认编码读 JSON | 拉取最新 main，JSON 读写已显式 UTF-8 |
| `submission_ready.csv` 有 NaN | fuse/final 缺某个 task | 查 `delivery_report.md` 与 `run_manifest.json` |
| exit code 2 | fallback 交付 | 查看 `fallback_report.md/json`，修复后 `--force` 重跑 |
| exit code 1 | 无交付 | 查看 `run_manifest.json.errors` |

---

## 13. Git 安全

不要提交：

```text
data/
models/
outputs/runs/
outputs/_*/
```

检查：

```bash
git status --short
git ls-files data models outputs/runs outputs/_*
```

`outputs/runs/YYYY-MM-DD/final/submission_ready.csv`、`run_manifest.json`、`delivery_report.md` 可以作为交付附件单独发送，不建议作为代码提交。

---

## 14. 最近关键修复

| commit | 内容 |
|---|---|
| `bbe9b8c` | 修复 LightGBM target-day NaN / SGDFNet target-day NaN |
| `3cd629e` | Realtime adaptive complete training days |
| `55465be` | 严格 hour_business `{1..24}` + position-based `age_days` |
| `0214aaf` | 修复 classifier manifest Windows UTF-8 问题 |
| `40965eb` | README 交付版 |
| `f379a4c` | Dayahead 也改为 adaptive complete training days |

---

## 15. 一句话结论

模型预测流程、DA/RT 自适应权重学习、融合、分类器、最终输出与 postflight 均已通过 2026-07-03 正式陪跑验收。完整 NORMAL 交付的核心前提是：**ledger 中能在 lookback 范围内为 Dayahead 和 Realtime 各自找到最近 30 个完整训练日。**
