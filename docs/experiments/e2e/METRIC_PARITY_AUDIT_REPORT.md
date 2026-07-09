# EFM3 Metric Parity Audit Against 2.5

> Audit date: 2026-07-09 | Repo: `electricity_price_forecast3.0` (local `efm3.0`)
> Mode: **strict diagnostic only** — no model change, no new model, no champion/final-policy change, no frontend.
> Evidence: SQL on MySQL ledger `efm3` (181 formal_sim days, 2026-01-01 ~ 2026-06-30) + 2.5 source read.

---

## 1. Executive Summary

| Item | Value |
| ---- | ----- |
| current 3.0 metric semantics | **DA_ANCHOR_VS_RT_ACTUAL** |
| comparable to 2.5 realtime 23%? | **NO** |
| likely root cause | **METRIC_SEMANTICS_MISMATCH** (day-ahead price vs real-time price) + **MODEL_OUTPUT_MISSING** |
| severity | **HIGH** (the headline 49.70% is not a forecast-accuracy number) |

**一句话结论**：3.0 当前 49.70% 是「日前出清价 vs 实时实际价」两个**不同市场产品**之间的价差散布，不是模型预测精度。2.5 的 14%/23% 是「模型预测 vs 同产品结算价」的真实预测精度。两者**口径不可比（NOT_COMPARABLE）**。

---

## 2. Current 3.0 Metric Semantics

| Field | Value |
| ----- | ----- |
| pred source | `efm_predictions` WHERE `task='final' AND stage='final_selected' AND is_selected=1`. 100% of these rows equal `da_anchor` (SQL 证据见下). |
| actual source | `efm_actual_prices.rt_actual` (real-time actual price) |
| date range | 2026-01-01 ~ 2026-06-30 (181 days, formal_sim) |
| model or baseline | **Neither** — it is the day-ahead clearing price (CSV `日前电价`) used as an anchor, NOT a model forecast. |

**SQL 证据（只读探针，`tools/db_ops/_probe_semantics` 等价查询）：**

```sql
-- final_selected 行中 pred_price 与 da_anchor 完全相同的比例
SELECT COUNT(*) FROM efm_predictions p1
JOIN efm_predictions p2 ON p1.run_id=p2.run_id AND p1.hour_business=p2.hour_business
WHERE p1.stage='final_selected' AND p1.task='final' AND p1.is_selected=1
  AND p2.stage='da_anchor' AND ABS(p1.pred_price - p2.pred_price) < 0.01;
-- 结果: 5976 / 5976 = 100%
```

```sql
-- final_selected 的来源（decision_reason 分布）
SELECT selected_reason, COUNT(DISTINCT run_id) FROM efm_predictions
WHERE stage='final_selected' AND task='final'
  AND run_id IN (SELECT run_id FROM efm_runs WHERE mode='formal_sim')
GROUP BY selected_reason;
-- ('non_winter_da_anchor_fallback', 189)  ('winter_da_anchor_policy', 60)
```

→ 全部 249 个 formal_sim run 的 `final_selected` 都来自 `da_anchor`，**没有任何 official_baseline / realtime / sgdfnet 模型输出**进入 `final_selected`。

**判定：`CURRENT_3_0_METRIC_SEMANTICS = DA_ANCHOR_VS_RT_ACTUAL`**

---

## 3. 2.5 Metric Semantics

| Metric | Value | Date Range | Pred Source | Actual Source | Formula |
| ------ | ----: | ---------- | ----------- | ------------- | ------- |
| 日前 (day-ahead) | ~14% | 2026-01-25 ~ 2026-02-26 (cited, seed ledger window) | DA model forecast (`y_fused`, lightgbm+timesfm+timemixer) | DA settlement price (`日前电价`) | `mean(2*|p-a|/(|p|+|a|))*100`, **floor(50) 裁剪**, pooled |
| 实时 (real-time) | ~23% | 同上窗口 | RT model forecast (`y_fused`, timesfm+sgdfnet+timemixer+rt916) | RT settlement price (`实时电价`) | 同上 |

2.5 关键事实（来自 `electricity_forecast_model2.5` 只读检索）：
- SMAPE 实现 `fusion/learners/daily_ledger_gef.py:39-65`（`smape_floor50`）：先 `yp=max(yp,50); yt=max(yt,50)`，再 `mean(|yp-yt| / ((|yp|+|yt|)/2)) * 100`。
- 聚合：**所有 (日×小时) 样本点 POOLED 平均**（`n_samples=720=30天×24h`），非「先逐日平均再平均」。
- 零/近零保护：逐值 floor=50 + 分母 `.clip(lower=1e-6)`。
- `docs/OUTPUT_CONVENTION.md` 与 `metrics_calculation.md` 明确：`P̂_da_i vs P_da_i`（同产品）、`P̂_rt_i vs P_rt_i`（同产品）；不同产品价差 `P_rt - P_da` 仅用于套利指标，**不用于 SMAPE**。
- 结论：**2.5 的 14%/23% 是「模型预测 vs 同产品实际值」的真实预测精度（TRUE forecast accuracy）**。

---

## 4. Formula Parity

| Check | 2.5 | 3.0 | Match |
| ----- | --- | --- | ----- |
| SMAPE core | `mean(2*|p-a|/(|p|+|a|))*100` | `mean(200*|p-a|/(|p|+|a|))` | ✅ 数学等价 |
| floor-50 clipping | `max(v,50)` on p and a | **无** | ❌ 不一致 |
| zero-denom handling | floor 50 防 0 + `.clip(1e-6)` | `denom==0 → smape=0` | ⚠️ 不同（3.0 把 0/0 记 0） |
| MAPE a==0 | 不用 MAPE（用裁剪 SMAPE） | `a==0 → skip` | ⚠️ 不同 |
| aggregation | **pooled** (all hours) | **daily-mean → average over days** | ❌ 不一致 |
| WMAPE | pooled `sum(|err|)/sum(|actual|)` | daily-mean → average over days | ⚠️ 不同（近似） |

> 仅 core 公式等价。**floor-50 裁剪 + pooled 聚合**是 2.5 与 3.0 的实质性公式差异。

**实测影响（3.0 `da_anchor vs rt_actual`，窗口 4344 小时）：**

| Aggregation | SMAPE | MAE | RMSE | WMAPE |
| ----------- | ----: | --: | ---: | ----: |
| 3.0 (daily-mean→avg) | 49.70% | 92.83 | 143.90 | 30.92% |
| pooled（2.5 风格） | 49.70% | 92.83 | 143.90 | 30.92% |
| **2.5 floor-50 + pooled** | **28.21%** | — | — | — |

→ 仅套用 2.5 的 floor-50 就把 49.70% 拉到 28.21%，但仍 **DA-vs-RT（非 RT-model-vs-RT-actual）**，依旧不能与 2.5 的 23% 直接比。

---

## 5. Hour Alignment Audit

| Check | Result |
| ----- | ------ |
| 00:00 mapping | 3.0: `ts.hour==0 → hour_business=24` ✅（与 2.5 数值一致） |
| date ownership | **3.0: `2026-02-25 00:00` → `trade_date=2026-02-25, hb=24`**；2.5: 同时间戳 → `business_day=2026-02-24, hb=24` ❌ **差一天** |
| 2.5 parity | 2.5 规则（`OUTPUT_CONVENTION.md`）：`hour 24 = D+1 00:00` 归属 business day D。3.0 保持日历日期，未遵循 |
| severity | **MEDIUM**（结构性 parity bug；**不是** 49.70% 的成因） |

**SQL 证据：**

```sql
SELECT trade_date, hour_business, value, data_type FROM efm_market_data_hourly
WHERE market='shandong' AND trade_date='2026-01-02' AND hour_business=24
ORDER BY data_type;
-- (2026-01-02, 24, 245.0000, da_price), (2026-01-02, 24, 265.8490, rt_price)
-- 即 CSV 的 `2026-01-02 00:00` 行被 3.0 归入 trade_date=2026-01-02, hb=24
-- 2.5 会把它归入 business_day=2026-01-01, hb=24
```

详细报告：`outputs/metric_parity/hour_alignment_audit.md`（抽样 2026-01-01 / 02-14 / 03-15 / 06-30 全部符合上述规律）。

**为什么不是 49.70% 的成因**：3.0 中 `da_anchor` 与 `rt_actual` 的 (date, hb) 来自**同一 CSV 行**（同一时刻），日内无错位；da_vs_rt 比较是内部一致的。错位只发生在 hb=24 的**日期标签**（3.0 标 D，2.5 标 D-1），整窗 181 天只是边界小时日期标签不同，聚合指标数值不变。需修复才能做**逐日**跨系统对齐 / 与 2.5 输出融合。

---

## 6. SMAPE Sensitivity

| Filter | Hours | SMAPE | MAE | WMAPE |
| ------ | ----: | ----: | --: | ----: |
| all | 4344 | 49.70% | 92.83 | 30.92% |
| actual_abs >= 1 | 4318 | 49.52% | 92.84 | 30.92% |
| actual_abs >= 5 | 4288 | 48.50% | 92.87 | 30.93% |
| actual_abs >= 10 | 4288 | 48.50% | 92.87 | 30.93% |

- 近零/异常 RT actual（窗口 4344h）：`|rt_actual|<1` = **26h**；`<5` = 43h；`<10` = 56h；**`rt_actual < 0` = 934h（21.5%）**。
- 负电价小时（如 2026-02-14 h1/h2 = -59.67 / -80.00）与 2026-06-30 整日 `rt_actual=0` 显著推高 SMAPE。
- 按 actual 价格带：`<0` / `0..10` 带 SMAPE 极高（近 200% 饱和）；`100+` 带仍达 ~40%+——说明**即使剔除近零，DA-vs-RT 价差在所有价位都很大**，极端值只是叠加项而非主因。
- 同产品对照：`da_anchor vs da_actual`（da_price）pooled SMAPE = **9.3%**，证明误差主源是「跨产品价差」而非「模型本身差」。

详细报告：`outputs/metric_parity/smape_sensitivity.md`。

---

## 7. Apples-to-Apples Comparison

| System | Task | Pred Source | Actual Source | Date Range | SMAPE | MAE | RMSE | WMAPE | Comparable |
| ------ | ---- | ----------- | ------------- | ---------- | ----: | --: | ---: | ----: | ---------- |
| 3.0 | final_selected | da_anchor（日前出清价） | rt_actual（实时实际价） | 2026-01-01~06-30 | 49.70% | 92.83 | 143.90 | 30.92% | **NOT_COMPARABLE（DA vs RT）** |
| 3.0 | sanity | da_anchor | da_actual（同产品） | 同上 | 9.3% | — | — | — | 非模型指标 |
| 2.5 | day-ahead | DA model forecast | DA settlement | ~2026-01-25~02-26 | ~14% | — | — | — | TRUE DA accuracy |
| 2.5 | real-time | RT model forecast | RT settlement | ~2026-01-25~02-26 | ~23% | — | — | — | TRUE RT accuracy |

**结论**：
1. 49.70% **不是代码 bug**，但**是口径错误**（拿日前价比实时价）。
2. 49.70% **不能**和 2.5 的 23% 比——一个比的是「不同产品」，一个比的是「同产品预测误差」。
3. 真正等价对比：3.0 当前**没有**合法的「模型预测 vs 同产品实际」数字；同产品对照（da_anchor vs da_actual）仅 9.3% 且主要反映 Jan25–Feb25 ledger 窗口的模型误差（其余日期 da_anchor==da_price 故为 0）。
4. 若要退化分析：即便套用 2.5 的 floor-50，3.0 仍为 28.21%（DA-vs-RT），高于 2.5 的 23%（RT-model-vs-RT-actual），这与「日前价天然比实时价波动更大」一致，仍不可直接等同。
5. 下一步应接入 2.5 的 realtime / day-ahead production model output，或让 3.0 full_chain 调用 2.5-compatible 预测链，再将 `final_selected` 指向真正的模型输出并与**同产品 actual** 比较。

详细报告：`outputs/metric_parity/apples_to_apples.md`。

---

## 8. Root Cause

- ✅ **METRIC_SEMANTICS_MISMATCH** — `final_selected` = `da_anchor`（日前价），actual = `rt_actual`（实时价）；比的是两个不同市场产品。
- ✅ **MODEL_OUTPUT_MISSING** — 249 个 formal_sim run 全部 `da_anchor`，无任何 realtime/DA 模型输出进入 `final_selected`（见 §2 SQL 证据）。
- ✅ **HOUR_ALIGNMENT_BUG** — hb=24 日期归属与 2.5 差一天（§5），MEDIUM，需修以做逐日对齐。
- ✅ **SMAPE_NEAR_ZERO_SENSITIVITY** — 2026-06-30 `rt_actual=0` 致 SMAPE 饱和 200%（§6）。
- ✅ **DATA_QUALITY_ISSUE** — 934/4344 小时 `rt_actual<0`（负电价/异常），放大 SMAPE。
- ❌ REAL_REGRESSION — 不适用（无模型回归可言，因无模型在跑）。

---

## 9. Recommendation

**METRIC_AUDIT_RECOMMENDATION: DO_NOT_COMPARE_YET**

3.0 当前的 49.70% **不得**作为「3.0 精度结果」对外汇报或用于和 2.5 对标。它只是 **DA anchor baseline（日前锚定价）vs 实时实际价** 的价差基准，不是最终模型能力。

下一步（在可对标前必须做其一）：
1. 接入 2.5 的 realtime production model output 或 day-ahead output；
2. 或让 3.0 `full_chain` 调用 2.5-compatible 预测链，使 `final_selected` 指向真正的模型预测；
3. 或新增 `official_baseline` / `realtime_model` stage，再与 `da_anchor` 做 selector/fusion；
4. 然后与**同产品 actual**（DA 预测比 DA 实际、RT 预测比 RT 实际）计算指标，并采用 2.5 的 floor-50 + pooled 聚合口径，方可与 14%/23% 对标。

---

## 10. Final Verdict

**METRIC_AUDIT_RESULT: FAIL**

3.0 当前 Jan–Jun formal_sim 精度（49.70%）因口径错误（DA 价 vs RT 价 + 无模型输出）**不能作为 3.0 模型能力结论**，且与 2.5 的 14%/23% **不可比**。根因已用 SQL 证据定位（§2/§4/§5/§6）。在接入真正的模型预测输出并统一口径前，指标对标无效。

---

### 附：本审计产物（均只读/诊断，无模型改动）
- `tools/db_ops/audit_hour_alignment.py` → `outputs/metric_parity/hour_alignment_audit.md`
- `tools/db_ops/analyze_smape_contributors.py` → `outputs/metric_parity/smape_sensitivity.md`, `apples_to_apples.md`
- `tests/test_metrics_formula_parity.py`, `tests/test_hour_alignment_audit.py`, `tests/test_smape_sensitivity.py`
