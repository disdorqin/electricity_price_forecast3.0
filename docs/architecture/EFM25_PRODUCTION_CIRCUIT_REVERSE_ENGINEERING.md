# EFM 2.5 生产电路逆向工程报告

> 源仓：`electricity_forecast_model2.5`（本地 `其他资料/electricity_forecast_model2.5`）
> 目的：为 EFM3 生产电路重构提供**经代码核实**的 2.5 生产链路事实基线，避免凭记忆或想象拼装。
> 所有结论均来自对 `pipelines/ledger_*.py`、`fusion/`、`pipelines/emergency_fallback.py` 的直接读取与 grep 取证。
> 日期：2026-07-09

## 0. 总览：2.5 生产电路拓扑

```
            ┌─────────────────────── DAY-AHEAD 子链 ───────────────────────┐
ledger_predict ──▶ ledger_weight ──▶ ledger_fuse ──▶ (无 day-ahead classifier)
   lightgbm         DailyLedgerGEF    apply_daily_       │
   timesfm          (BGEW 动态权重)    ledger_weights    │
   timemixer                                          dayahead final
            └──────────────────────────────────────────────────────────────┘

            ┌─────────────────────── REAL-TIME 子链 ────────────────────────┐
ledger_predict ──▶ ledger_weight ──▶ ledger_fuse ──▶ ledger_classifier ──▶ realtime final
   timesfm         DailyLedgerGEF    apply_daily_       ExtremPriceClf        │
   sgdfnet         (BGEW 动态权重)    ledger_weights    (负电价分类器)         │
   timemixer                                            │  mask→ -80            │
   rt916                                               realtime_final(_corrected)
            └──────────────────────────────────────────────────────────────┘

                         ▼ 两子链汇聚 ▼
                  final_outputs (delivery, 区分 DA/RT 来源)
                         ▼
                  postflight (质量门)
                         ▼
              NORMAL / DEGRADED_DELIVERED / FAILED_NO_DELIVERY
                         ▼ (失败)
                  emergency_fallback (historical_same_hour_median)
```

**关键事实 0.1 — 日前与实时严格分离，各走独立模型集合、独立权重、独立融合：**

| 维度 | Day-ahead | Real-time |
|---|---|---|
| 模型集合（代码实证） | `DAYAHEAD_MODELS = ["lightgbm", "timesfm", "timemixer"]`（3×24=72 行） | `REALTIME_MODELS = ["timesfm", "sgdfnet", "timemixer", "rt916"]`（4×24=96 行） |
| 数据截止 | `da_cutoff = target_date - 1 天` | `rt_cutoff = (target_date-1) {realtime_cutoff_hour}:00:00`，默认 `realtime_cutoff_hour=14` |
| 权重学习 | 独立 `ledger_weight --task dayahead` | 独立 `ledger_weight --task realtime` |
| 融合 | 独立 `ledger_fuse --task dayahead` | 独立 `ledger_fuse --task realtime` |
| 分类器 | **无**（day-ahead 不过分类器） | 有（ExtremPriceClf，2.5 独有 RT 环节） |

> **设计差异警示**：RT916 / TimeMixer **确实存在于 2.5 实时候选集**（`REALTIME_MODELS` 已实证）。因此 3.0 之前把它们排除在在线关键路径之外，是一个**主动的设计选择**，而非"2.5 本来就没有"。重构时是否重新纳入 RT 候选需显式决策，不能假装 2.5 只有 timesfm/sgdfnet。

---

## 1. Day-ahead Circuit（日前电路）

### 1.1 入口 `ledger_predict`
- 源文件路径：`pipelines/ledger_predict.py`
- 模型清单常量：`DAYAHEAD_MODELS = ["lightgbm", "timesfm", "timemixer"]`（第 45 行）。
- 数据截止：第 144–147 行
  - `da_cutoff_date = (target_date - 1 day)`
  - 写 manifest：`data_cutoff_dayahead = da_cutoff_date`
- 每个模型在 `epf_v1_mode`（默认 `"exact"`）下运行，lightgbm 走 bundled，timesfm/timemixer 走各自 bundled 后端。
- **产出**：每个模型 24 行预测（`hour_business 1..24`），写入 prediction ledger（文件版 `outputs/ledger/dayahead/{date}/...`）。

### 1.2 权重 `ledger_weight`（Day-ahead 段）
- 源：`pipelines/ledger_weight.py` → `_learn_weights_for_task(task="dayahead", ...)`
- 训练窗口：`window_days = 30`（默认 `validation_days`），`max_lookback = 90`。
- **day_gate**：`recent_week_boost=True`，`recent_week_max_gate=0.85`（近一周表现好的模型门限上限 0.85）。
- 覆盖检查：要求 `actual_rows == expected_rows == window_days * len(expected_models) * 24`，否则 `status=failed`（硬门）。
- 实际学习器：`DailyLedgerGEF`（见 §3 权重公式）；按 `(task, period)` 输出权重表 `weights.csv`。

### 1.3 融合 `ledger_fuse`（Day-ahead 段）
- 源：`pipelines/ledger_fuse.py` → `_fuse_for_task(task="dayahead")`
- 调用 `fusion/apply_daily_ledger_weights.apply_daily_ledger_weights(weights, predictions, task="dayahead")`。
- **day-ahead 无分类器**：融合结果直接作为 `dayahead final`。
- 输出 `fused_predictions.csv`（24 行），`y_fused = Σ renorm_weight × y_pred`。

### 1.4 Day-ahead 尾节点
- 不进入 `ledger_classifier`（该 pipeline 只处理 realtime，见 §2.3）。
- 直接进入 `final_outputs` 的 day-ahead 分支，作为 delivery 的 DA 来源。

---

## 2. Real-time Circuit（实时电路）

### 2.1 入口 `ledger_predict`
- 模型清单：`REALTIME_MODELS = ["timesfm", "sgdfnet", "timemixer", "rt916"]`（第 46 行）。
- 数据截止：`rt_cutoff_date = f"{da_cutoff_date} {rt_cutoff_hour:02d}:00:00"`，`rt_cutoff_hour` 默认 14（注意：部分 config 文件使用 15，存在**截止小时不一致**隐患，3.0 必须统一为单一真相来源）。
- 每个模型有各自 `cutoff_hour_rt` / `asof_hour` / `decision_hour` 配置（timemixer→rt_cutoff_hour；rt916→asof_hour；sgdfnet→decision_hour；timesfm→epf_v1_mode）。

### 2.2 权重 + 融合（`ledger_weight` / `ledger_fuse`，Realtime 段）
- 与 day-ahead 同构，但 `task="realtime"`，独立窗口、独立权重表、独立融合。
- 实时候选缺失时：融合层 `allow_equal_weight_fallback`（默认 False 严格；开启时退化为等权）。

### 2.3 分类器 `ledger_classifier`（**实时独有**）
- 源：`pipelines/ledger_classifier.py` + `fusion/classifier_bridge.py`
- 仅处理 `realtime`：`realtime_final_dir = runs_root/{D}/realtime/final/`
- 流程：
  1. **先落一份 uncorrected final**：`fused_df.to_csv(realtime_final_predictions.csv)`（第 76–78 行）—— 这是 delivery 时"RT 用 UNCORRECTED 融合"的来源。
  2. 再尝试跑负电价分类器 `_run_extreme_price_classifier`（ExtremPriceClf 包）。
  3. **修复规则（代码实证 `classifier_bridge.py` 第 91–92 行）**：
     ```python
     mask = (merged["final_pred"] == 1) & (merged["y_fused"] <= 100)
     merged.loc[mask, "y_fused_corrected"] = -80.0
     ```
     → 即"分类器判负电价且融合价 ≤100 → 校正为 -80"。落 `realtime_final_predictions_corrected.csv`。
  4. 分类器失败**不致命**：除非 `--strict-classifier`，否则 `status=complete_with_warnings`，保留 uncorrected 版本。
- **day-ahead 不过分类器** —— 这是 2.5 与"全都过一遍分类器"设想的本质区别。

---

## 3. Fusion / Classifier / Repair（融合、分类、修复统一说明）

### 3.1 动态权重公式（BGEW / `DailyLedgerGEF`）
源：`fusion/learners/daily_ledger_gef.py`

- 配置（第 134–135 行）：
  - `eta: float = 0.8`（学习率）
  - `weight_floor: float = 0.03`（每模型最小权重）
- 更新规则（第 15–16、309、329、331 行）：
  ```
  逐日逐模型：
    norm_loss_m  = 该模型当日归一化损失（基于 smape_floor50 / composite）
    gate         = day_gate（近期表现门限，<= recent_week_max_gate=0.85）
    decay        = exp(-eta * gate * norm_loss_m)
    w_m         *= decay
    w_m          = max(w_m, weight_floor)        # 地板裁剪
  初始权重：等权开始（equal start）
  ```
- 损失函数（第 102 行）：默认 `smape_floor50`；可选 `composite = 0.7*smape_floor50 + 0.3*mae_percent`。
- **`smape_floor50` 定义（第 39–59 行，按 `docs/metrics_calculation.md`）**：
  > 在计算 SMAPE **之前**，先把每个 `y_true` 和 `y_pred` 单独裁剪到 `floor=50`（`clip per value, not per pair sum`）。
  ```python
  y_true_c = np.clip(y_true, 50, None)
  y_pred_c = np.clip(y_pred, 50, None)
  smape = 200 * mean(|y_true_c - y_pred_c| / (|y_true_c| + |y_pred_c|))
  ```
- 周期：`period ∈ {"1_8", "9_16", "17_24"}`（`fusion/contracts.py` 实证 `VALID_PERIODS`），权重按 `(task, period)` 分别学习。

### 3.2 融合（无 fillna(0)）
源：`fusion/apply_daily_ledger_weights.py`
- **缺失模型直接排除**，绝不 `fillna(0)`（第 10–11 行注释 + 第 191–192 行验证：若 `y_fused==0` 则告警"疑似 fillna(0)"）。
- 重归一化：仅对**可用模型**重新归一化权重（`renorm_w = w / sum(available_w)`），保证 `Σw=1`（第 133–139 行）。
- 严格模式：缺小时 / 缺模型 / 缺权重 → 报错（除非 `--allow-equal-weight-fallback`）。

### 3.3 修复规则（-80）归属
- **该规则属于实时分类器环节**（`classifier_bridge.py`），不是通用 fusion repair。
- day-ahead 链路不存在 -80 修复。

---

## 4. Metrics Semantics（指标口径）

### 4.1 2.5 内部口径（来自 `fusion/learners/daily_ledger_gef.py` 与 `fusion/metrics.py`）
- 权重学习损失与候选评估使用 **`smape_floor50`**（逐点 clip 到 50 后 pooled 计算）。
- 评估对象：**融合预测 vs 同产品结算价（actual ledger）**，且 DA 与 RT 各自独立评估。

### 4.2 关于"14% / 23%"的真相
- **仓库内无任何 14%/23% 的源头数据或脚本**（已全仓 grep 验证）。这两个数字属于对外叙述，不在可复现代码资产中。
- 正确做法：在 3.0 中用 `smape_floor50`（融合 vs 同产品 actual，区分 DA/RT）**复现**对应口径，而不是引用无法溯源的数字。
- **关键陷阱**：3.0 当前 49.70% 的 SMAPE 是 `da_anchor`（日前出清价）vs `rt_actual`（实时实际价）—— 即**跨产品价差**，与 2.5 的"融合 vs 同产品结算价"**不可比较（NOT_COMPARABLE）**。详见 `docs/experiments/e2e/METRIC_PARITY_AUDIT_REPORT.md`。

### 4.3 周期拆分口径
- 指标按 `overall / 1_8 / 9_16 / 17_24` 分别汇总（`fusion/pipeline_common.py: period_summary`）。

---

## 5. What 3.0 Must Preserve（3.0 必须保留的 2.5 语义）

| # | 必须保留的语义 | 2.5 实证位置 | 3.0 现状 | 风险 |
|---|---|---|---|---|
| P1 | **日前 / 实时严格双子链**，独立模型集、权重、融合 | `ledger_predict` DAYAHEAD/REALTIME_MODELS | 3.0 仅 `da_anchor → seasonal_da_router → final_selected`，**无 RT 子链** | 🔴 BLOCKER |
| P2 | 动态权重 `w *= exp(-eta·gate·norm_loss)`，`floor=0.03`，`eta=0.8` | `daily_ledger_gef.py` | 3.0 无动态权重学习 | 🔴 BLOCKER |
| P3 | 融合**重归一化 + 绝不 fillna(0)** | `apply_daily_ledger_weights.py` | 3.0 融合环节缺失/退化为锚点 | 🔴 BLOCKER |
| P4 | **分类器仅实时**，修复规则 `(final_pred==1)&(y_fused<=100)→-80` | `classifier_bridge.py` | 3.0 无分类器 | 🟠 HIGH |
| P5 | delivery **区分来源**（RT 用 UNCORRECTED 融合） | `ledger_classifier.py` uncorrected 落盘 | 3.0 delivery 无 DA/RT 区分 | 🟠 HIGH |
| P6 | 状态机 `NORMAL / DEGRADED_DELIVERED / FAILED_NO_DELIVERY` + `historical_same_hour_median` 兜底 | `ledger_full._finalize_delivery` + `emergency_fallback.py` | 3.0 无状态机/兜底 | 🟡 MEDIUM |
| P7 | 指标 **`smape_floor50`** 且 **DA/RT 分别评估同产品结算价** | `daily_ledger_gef.py` / `metrics.py` | 3.0 无 floor50，跨产品混算 | 🔴 BLOCKER |
| P8 | 周期 `1_8 / 9_16 / 17_24` 权重与指标拆分 | `contracts.py VALID_PERIODS` | 3.0 无周期概念 | 🟡 MEDIUM |
| P9 | 双子链汇聚为 **delivery final**，保留完整血缘 | `final_outputs` | 3.0 `final_selected` 单表无血缘 | 🟠 HIGH |

> **核心结论**：3.0 当前"能跑出数字"≠"跑出 2.5 语义的生产结果"。本次重构目标不是提高模型精度，而是**把 2.5 的生产电路骨架（双子链 + 动态权重 + 真实融合 + 实时分类器 + 状态机 + floor50 同产品指标）在 3.0 中以 DB-Ledger V2 形式重建**，并明确标注"哪些节点已用真实模型、哪些仍是 benchmark/placeholder，绝不把 da_anchor 伪装成最终模型输出"。
