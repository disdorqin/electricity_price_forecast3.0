# P3 — 3.0 Extreme Price Correction System 设计（阶段 B）

> 设计目标：一个 **shadow-only、可解释、可关闭、可回滚** 的极端价格修正系统，运行在 3.0 主融合之后、final_outputs 之前的"影子旁路"。
> 适用任务：**realtime**（实时现货电价）。cutoff 口径：**D14**（只用 D-1 14:00 前可见信息）。
> 日期：2026-07-06

---

## 1. 系统架构

```
                          fused realtime prediction (original_pred)
                                         │
                                         ▼
        ┌────────────────────────────────────────────────────────┐
        │            Extreme Price Correction System (shadow)      │
        │                                                            │
        │  negative_price_classifier ──┐                            │
        │  spike_price_classifier    ──┤──► residual_corrector      │
        │  (均输出 prob/type/conf)     │     (delta=RT-DA 分段校准)  │
        │                              ▼                            │
        │                       correction_guard ──► rollback_guard│
        │                              │                  │         │
        │                              ▼                  ▼         │
        │                       corrected_pred      (失败→original) │
        └────────────────────────────────────────────────────────┘
                                         │
                          applied = FALSE (默认 shadow)
                                         ▼
                       spike_residual_predictions.csv  (不进 submission_ready.csv)
                                         │
                                         ▼
                              correction_reporter (before/after)
```

六个子模块，全部可独立开关（配置 `enabled` 标志），便于 ablation。

---

## 2. 模块职责与 I/O

### 2.1 negative_price_classifier
- **输入（cutoff-safe）**：`original_pred`, `model_std`, `model_min/max`, `da_anchor`, `hist_neg_rate_samehour`, `period`, `hour_business`, `dow`, `is_holiday`。
- **算法**：两阶段级联（沿用 2.5 思路，但标签聚焦 `actual ≤ -50`；训练用 expanding-window walk-forward，绝不用未来标签）。
  - 阶段1：轻量逻辑回归/GBM → `p1_prob`
  - 阶段2：灰度区(`gray_low~gray_high`)重训 GBM → `p2_prob`
  - 动态灰度：90 天窗口搜索 `recall≥0.95, precision≥0.80`
- **输出**：`negative_probability`, `negative_reason`, `negative_confidence`。
- **可关闭**：`negative_classifier_enabled`。

### 2.2 spike_price_classifier
- **输入（cutoff-safe）**：`original_pred`, `model_std`, `da_anchor`, `hist_p90_samehour`, `period`, `hour_business`。
- **算法**：两阶段 GBM（标签 `actual>800 或 |spread|>100`；`momentum_factor=0.85, prob_threshold=0.35`），`rt_prev/da_prev` 特征**仅用 cutoff 前实际值**。
- **输出**：`spike_probability`, `spike_type`（high/low/none）, `spike_reason`, `spike_confidence`。
- **可关闭**：`spike_classifier_enabled`。

### 2.3 residual_corrector
- **输入**：`original_pred`, `da_anchor`, `period`, `hour_business`, `month_bucket`, expanding 历史残差表。
- **算法（SGDFNet 式）**：`delta_hat = RT - DA`；计算 `(segment, hour, month_bucket)` 历史中位数残差 `bias`；`corrected = original + α·bias`（α=error_gate，仅当 |bias| 显著且样本充足时启用）。可叠加"模型分歧度"修正。
- **输出**：`correction_amount`, `corrected_pred`, `correction_reason`, `correction_confidence`。
- **可关闭**：`residual_corrector_enabled`。

### 2.4 correction_guard（限制过度修正）
- **规则**：
  1. **correction cap**：`|correction_amount| ≤ cap_abs`（默认 350）且 `|correction_amount/original_pred| ≤ cap_ratio`（默认 0.35）。
  2. **price range guard**：`corrected_pred ∈ [PRICE_FLOOR, PRICE_CEIL]`（默认 [-100, 1500]）。
  3. **normal-hour damage guard**：若 `original_pred` 已"正常"，禁止大改；`protect_9_16` 时段更保守。
  4. **NaN guard** / **24h completeness guard**。
- **输出**：`applied`（bool）、`cap_hit`（bool）、`guard_reason`。

### 2.5 rollback_guard
- **触发回滚条件**（任一即回退 `original_pred`）：NaN / 缺小时 / 超 cap / 正常时段误差恶化 / confidence 低于阈值。
- **输出**：`rollback_reason`（空串=未回滚）。

### 2.6 correction_reporter
- **输出**：before/after 报告（MAE/RMSE/sMAPE_floor50、负价/尖峰/正常子集、period、分类 P/R/F1、纠正统计）。

---

## 3. 修正决策流（伪代码）

```
for each (target_day, hour):
    neg_p, neg_reason, neg_conf = negative_classifier(features)        # if enabled
    spk_p, spk_type, spk_reason, spk_conf = spike_classifier(features) # if enabled
    corr_amt, corr_reason, corr_conf = residual_corrector(...)         # if enabled

    corrected = original
    applied = False
    cap_hit = False

    # 负价修正（保守：仅当 classifier 高置信 且 original 已偏低）
    if negative_classifier_enabled and neg_p >= NEG_THRESH and original <= NEG_ACT_PRED_CAP:
        target = -80
        amt = target - original
        if guard_pass(amt, original, ...):
            corrected = target; applied = True; reason += neg_reason
        else: cap_hit = True

    # 尖峰修正（有界上行 lift，仅当 classifier 高置信）
    elif spike_classifier_enabled and spk_p >= SPK_THRESH:
        lift = min(SPK_LIFT_RATIO * original, SPK_LIFT_ABS) * (1.15 if period==9_16 else 1.0)
        amt = +lift
        if guard_pass(amt, original, ...):
            corrected = original + amt; applied = True; reason += spk_reason
        else: cap_hit = True

    # 残差修正（一般偏差校准，叠加或独立）
    if residual_corrector_enabled and not (applied and extreme):
        amt2 = residual_bias_correction(...)
        if guard_pass(amt2, corrected, ...):
            corrected += amt2; reason += corr_reason

    if rollback_triggered(corrected, original, conf, ...):
        corrected = original; rollback_reason = ...

    record(...)
```

> 注：`elif` 实现"负价优先、与尖峰互斥"的 residual_stack 原则（上行只正、下行只负、互斥）。

---

## 4. cutoff-safe 特征清单（D14）

✅ 允许（D-1 14:00 前可见）：
- `original_pred` / 4 模型预测 / `model_std` / `model_min/max`（均为 D-1 14:00 前产出）
- `da_anchor`（日前预测，更早产出）
- `hist_neg_rate_samehour` / `hist_p50/p90_samehour`（D 日之前的真实值统计，expanding）
- `period` / `hour_business` / `dow` / `is_holiday`（日历）

❌ 禁止（目标泄漏）：
- D 日 14:00 之后 realtime actual、D+1 realtime actual
- D+1 spike/negative 标签作为在线特征
- partial actual 补成完整 actual
- 任何未来 actual 派生特征

---

## 5. shadow-only 机制

- 默认 `applied = FALSE`：所有修正只记录在 `spike_residual_predictions.csv` 的 `corrected_pred` 列，**绝不写入 `submission_ready.csv`**。
- 即便某行 `applied = TRUE`，也仅存在于实验输出；正式链路读取需显式 `promotion_decision` 授权。
- `run_p3_spike_residual_shadow.py` 不调用任何 final_outputs / postflight 写盘逻辑；只读 ledger，写实验目录。

---

## 6. 可配置旋钮（供 ablation）

| 旋钮 | 默认 | 说明 |
|------|------|------|
| `negative_classifier_enabled` | True | 负价分类器开关 |
| `spike_classifier_enabled` | True | 尖峰分类器开关 |
| `residual_corrector_enabled` | True | 残差校正开关 |
| `NEG_THRESH` | 0.5 | 负价触发概率 |
| `NEG_ACT_PRED_CAP` | 100 | 仅当 original≤此值才推 -80（保守护栏） |
| `SPK_THRESH` | 0.35 | 尖峰触发概率 |
| `SPK_LIFT_RATIO` | 0.35 | 尖峰上行 lift 比例 |
| `SPK_LIFT_ABS` | 350 | 尖峰上行 lift 绝对值上限 |
| `CAP_ABS` | 350 | 总修正绝对值上限 |
| `CAP_RATIO` | 0.35 | 总修正相对 original 比例上限 |
| `PRICE_FLOOR/CEIL` | -100 / 1500 | 价格生理范围 |
| `ROLLBACK_MIN_CONF` | 0.3 | 低于此置信回滚 |
| `residual_alpha` | 0.5 | 残差校准强度 |
| `residual_error_gate` | True | 误差门控 |

---

## 7. 通过标准映射

本系统设计即按 16 条通过标准构建：shadow-only、24h 完整、无 NaN、cap、reason、confidence、rollback、尖峰/负价改善、正常时段不恶化、period 17_24 不恶化、postflight 安全、无泄漏、D14 cutoff、before/after 报告、ablation 报告、失败案例说明。最终 `recommended_status` 由 F 阶段按实测数据裁定（candidate / shadow / no_go，严禁 champion）。
