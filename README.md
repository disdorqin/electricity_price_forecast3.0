# Electricity Forecast Delivery Pipeline v2.5

山东电力现货价格预测交付链路：**7 模型预测 + Ledger 动态权重融合 + Realtime 极端价格分类校正 + 最终交付文件校验**。

当前版本面向正式陪跑场景，重点处理了两个生产问题：

1. 预测明天时，目标日价格尚未发布，LightGBM / SGDFNet 仍能正常输出 24 小时预测。
2. Realtime 权重学习不再强制要求 D-1 当天 realtime actual 完整，而是自动向前寻找最近 30 个完整训练日。

---

## 1. 一分钟理解正式链路

```text
输入小时级山东电力现货数据
    ↓
ledger_predict：7 个模型分别预测目标日 24 小时
    ↓
ledger_weight：从 prediction ledger + actual ledger 学习动态融合权重
    ↓
ledger_fuse：按 task / period / model 加权融合
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
| LightGBM target-day NaN | FIXED | 目标日 `日前电价` 未发布时不再删掉推理行 |
| SGDFNet target-day NaN | FIXED | `da_anchor` 缺失时使用历史同小时中位数 fallback |
| Realtime adaptive weight days | FIXED | D-1 realtime actual 不完整时自动向前补完整训练日 |
| Windows UTF-8 manifest | FIXED | `delivery_quality` / `ledger_classifier` JSON 读写显式 UTF-8 |
| 回归测试 | PASS | adaptive 37/37、stability 29/29、NaN regression 16/16、sync 41/41 |
| 单日 / range 回测 | PASS | repro bundle 场景验证通过 |

### 2026-07-03 陪跑说明

已验证：已有 2026-07-03 预测结果 + 当前 ledger 可完成 **Realtime** 的 `weight → fuse → classifier` 链路；Realtime 输出 24 行、0 NaN。

当前本地 ledger 若仅包含 `2026-01-25 ~ 2026-02-25` 与 `2026-07-03`，中间 `2026-02-26 ~ 2026-07-02` 预测 ledger 为空，则 **Dayahead** 的 `D-30..D-1` 连续窗口不满足，`ledger_weight` 会拒绝学习 Dayahead 权重。这是 ledger 覆盖不足，不是模型代码 bug。

正式交付要得到 `delivery_status=NORMAL`，必须满足下方 Ledger 要求。

---

## 3. Ledger 要求

Ledger 根目录默认：

```text
outputs/ledger
```

也可以用参数指定：

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

### 3.1 Dayahead 权重：严格连续 30 天

对于目标日 `D`，Dayahead 权重学习要求：

```text
D-30 .. D-1 连续 30 天完整
```

完整定义：

```text
30 天 × 3 模型 × 24 小时 = 2160 prediction rows
30 天 × 24 小时 = 720 actual rows
```

三个 Dayahead 模型：

```text
lightgbm, timesfm, timemixer
```

Dayahead 不跳过缺失日。缺天、缺模型、缺小时、NaN 均会 hard fail，防止用假历史学习权重。

### 3.2 Realtime 权重：最近 30 个完整训练日

Realtime 正式陪跑时，D-1 当天真实实时价格往往只到 14:00，无法完整 24 小时。因此 Realtime 不再强制使用连续 `D-30..D-1`。

Realtime 会：

1. 从 `D-1` 开始往前扫描；
2. 跳过不完整日；
3. 收集最近 30 个完整训练日；
4. 按选中日期顺序计算 `age_days`，最近完整日为 `age_days=1`；
5. 在 `--weight-max-lookback-days` 范围内凑不够 30 天则失败并输出 skipped details。

四个 Realtime 模型：

```text
timesfm, sgdfnet, timemixer, rt916
```

完整日定义：

```text
prediction: 4 模型 × hour_business {1..24}，y_pred 无 NaN
actual: hour_business {1..24}，y_true 无 NaN
```

默认最大回看：

```bash
--weight-max-lookback-days 90
```

若历史 ledger 时间跨度较远，可以提高，例如：

```bash
--weight-max-lookback-days 180
```

---

## 4. 快速开始

### 4.1 安装环境

```bash
conda create -n epf-2 python=3.10 -y
conda activate epf-2
pip install -r requirements.txt
```

Windows + CUDA 已验证。TimesFM 推荐使用项目验证过的版本。

### 4.2 准备数据

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

如果数据路径不同：

```bash
--data-path path/to/shandong_pmos_hourly.xlsx
```

### 4.3 同步数据

独立同步：

```bash
python main.py --pipeline sync_dataset --sync-source auto --force-sync
```

生产建议两步式：

```bash
python main.py --pipeline sync_dataset --sync-source auto --force-sync --require-fresh-data
python main.py YYYY-MM-DD --data-path data/shandong_pmos_hourly.xlsx
```

也可以一条命令：

```bash
python main.py YYYY-MM-DD --sync-data-before-run --require-fresh-data
```

---

## 5. 正式运行命令

### 5.1 单日正式陪跑

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
exit code = 0
final/submission_ready.csv = 24 rows, 0 NaN
```

### 5.2 Range 运行

```bash
python main.py 2026-02-24 2026-02-26 \
  --data-path data/shandong_pmos_hourly.xlsx \
  --max-cpu-workers 2 \
  --max-gpu-workers 1 \
  --seed 42 \
  --deterministic
```

### 5.3 不推荐的正式参数

下面参数只用于诊断或应急，不作为 NORMAL 交付依据：

```text
--allow-missing-models
--allow-equal-weight-fallback
--no-range-preflight
```

如果使用这些参数跑通，只能说明工程链路可继续，不等于正式 NORMAL。

---

## 6. 预测明天但今天 realtime actual 不完整怎么办？

这是正式陪跑的正常情况。

例如：

```text
target_date = 2026-07-03
运行时间 = 2026-07-02 14:00 左右
2026-07-02 realtime actual 只有 0:00~14:00，不完整
```

处理逻辑：

| 任务 | 处理 |
|---|---|
| Dayahead | 仍要求 D-30..D-1 连续 30 天完整 |
| Realtime | 跳过 D-1 partial actual，向前找最近 30 个完整训练日 |

这不是放宽质量，而是不使用不完整 actual 学权重，同时保持最近完整样本优先。

---

## 7. 检查命令

### 7.1 基础测试

```bash
python -m py_compile main.py cli/parser.py pipelines/ledger_weight.py pipelines/prediction_ledger.py pipelines/delivery_quality.py pipelines/ledger_classifier.py
python scripts/check_adaptive_realtime_weight_days.py
python scripts/check_delivery_stability.py
python scripts/check_target_day_nan_regression.py
python scripts/check_sync_dataset.py
```

期望：

```text
check_adaptive_realtime_weight_days.py = 37/37 PASS
check_delivery_stability.py = 29/29 PASS
check_target_day_nan_regression.py = 16/16 PASS
check_sync_dataset.py = 41/41 PASS
```

### 7.2 检查 ledger window

```bash
python - <<'PY'
from pipelines.delivery_quality import validate_ledger_window
import json
print(json.dumps(validate_ledger_window('2026-07-03', 'outputs/ledger'), ensure_ascii=False, indent=2, default=str))
PY
```

说明：

- 如果 Dayahead 缺 `D-30..D-1`，正式 full chain 不能 NORMAL。
- 如果只有 Realtime D-1 actual 缺失，adaptive 逻辑会自动向前找完整日。

### 7.3 检查 Realtime adaptive training days

```bash
python - <<'PY'
from pathlib import Path
from pipelines.ledger_weight import select_complete_training_days, REALTIME_MODELS
import json
sel = select_complete_training_days(
    task='realtime',
    target_date='2026-07-03',
    ledger_root=Path('outputs/ledger'),
    expected_models=REALTIME_MODELS,
    required_days=30,
    max_lookback_days=180,
)
print(json.dumps(sel, ensure_ascii=False, indent=2, default=str))
PY
```

期望：

```text
status = PASS
selected_count = 30
```

---

## 8. 如果只想验证剩余链路

如果 7 个模型已经预测完成，可以只验证后续阶段：

```powershell
$TARGET_DATE = "2026-07-03"
$LEDGER_ROOT = "outputs/ledger"
$RUNS_ROOT = "outputs/_final_chain_verify_20260703/runs"

Copy-Item -Recurse -Force "outputs/runs/2026-07-03" "$RUNS_ROOT/"

python main.py --pipeline ledger_weight --date $TARGET_DATE --ledger-root $LEDGER_ROOT --runs-root $RUNS_ROOT --weight-max-lookback-days 180
python main.py --pipeline ledger_fuse --date $TARGET_DATE --ledger-root $LEDGER_ROOT --runs-root $RUNS_ROOT
python main.py --pipeline ledger_classifier --date $TARGET_DATE --ledger-root $LEDGER_ROOT --runs-root $RUNS_ROOT
```

如果 `ledger_weight` 显示：

```text
RT COMPLETE, DA FAIL
```

通常说明 Realtime adaptive 已通过，但 Dayahead 的连续 30 天 ledger 不足。此时需要补 Dayahead ledger，而不是改模型。

---

## 9. 如何补 ledger

### 9.1 使用已有完整 ledger

如果学长或生产机已有完整 ledger：

```bash
python main.py 2026-07-03 --data-path data/shandong_pmos_hourly_0702.xlsx --ledger-root path/to/complete/ledger
```

### 9.2 从零 backfill

需要先生成目标日前的历史 prediction/actual ledger：

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

注意：若 `2026-07-02 realtime actual` 只有部分小时，Realtime adaptive 会跳过这一天；Dayahead 仍需要 Dayahead prediction + actual 完整。

---

## 10. 输出文件

| 文件 | 说明 |
|---|---|
| `outputs/runs/YYYY-MM-DD/final/submission_ready.csv` | 最终交付文件 |
| `outputs/runs/YYYY-MM-DD/run_manifest.json` | 五阶段运行元信息 |
| `outputs/runs/YYYY-MM-DD/delivery_report.md` | 终端报告的 Markdown 版 |
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

交付优先级：

```text
NORMAL > DEGRADED_DELIVERED > FAILED_NO_DELIVERY
```

正式验收应优先使用 NORMAL。若使用 DEGRADED，需要说明 fallback 原因和后续修复计划。

---

## 12. Troubleshooting

| 问题 | 判断 | 处理 |
|---|---|---|
| LightGBM `NoneType` | 旧版本未兼容目标日 NaN | 拉取最新 main |
| SGDFNet 24 行 NaN | 旧版本 `da_anchor` 为 NaN | 拉取最新 main |
| `ledger_weight` Dayahead FAIL | D-30..D-1 连续 Dayahead ledger 不足 | 补 ledger / backfill |
| `ledger_weight` Realtime D-1 缺 actual | 正常陪跑场景 | 使用 adaptive，必要时提高 `--weight-max-lookback-days` |
| `UnicodeDecodeError: gbk` | Windows 默认编码读 JSON | 拉取最新 main，JSON 读写已显式 UTF-8 |
| `submission_ready.csv` 有 NaN | fuse/final 缺某个 task | 查 `delivery_report.md` 与 stage manifest |
| exit code 2 | fallback 交付 | 查看 `fallback_report.md/json`，修复后 `--force` 重跑 |
| exit code 1 | 无交付 | 查看 `run_manifest.json.errors` |

---

## 13. 交付前 Checklist

```bash
git pull
git log --oneline -5
python -m py_compile main.py cli/parser.py pipelines/ledger_weight.py pipelines/prediction_ledger.py pipelines/delivery_quality.py pipelines/ledger_classifier.py
python scripts/check_adaptive_realtime_weight_days.py
python scripts/check_delivery_stability.py
python scripts/check_target_day_nan_regression.py
python scripts/check_sync_dataset.py
python main.py YYYY-MM-DD --data-path data/shandong_pmos_hourly.xlsx --ledger-root outputs/ledger --weight-max-lookback-days 180
```

交付判断：

```text
run_manifest.json: status complete
postflight: PASS
delivery_status: NORMAL
final/submission_ready.csv: 24 rows, 0 NaN
```

---

## 14. Git 安全

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

提交代码时只应包含代码、测试、文档或静态复现资源。

---

## 15. 最近关键修复

| commit | 内容 |
|---|---|
| `bbe9b8c` | 修复 LightGBM target-day NaN / SGDFNet target-day NaN |
| `3cd629e` | Realtime adaptive complete training days |
| `55465be` | 严格 hour_business `{1..24}` + position-based `age_days` |
| 最新 main | Windows UTF-8 classifier manifest + 交付版 README |

---

## 16. 一句话结论

模型预测流程已经可执行；Realtime 正式陪跑的 D-1 partial actual 问题已解决；完整 NORMAL 交付的关键前提是：**Dayahead 的 D-30..D-1 连续 ledger 必须完整，Realtime 至少能在 lookback 范围内找到最近 30 个完整训练日。**
