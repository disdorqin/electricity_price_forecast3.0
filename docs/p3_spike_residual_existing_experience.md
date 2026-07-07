# P3 — 现有残差 / 分类器 / 尖峰经验梳理（阶段 A）

> 研究对象：山东电力现货 **实时(realtime)** 电价极端价格修正系统（3.0 候选）。
> 经验来源：本地仓库 `electricity_forecast_model2.0_exp`（经验源）、`electricity_price_forecast3.0`(efm3.0，含 2.5 衍生代码)、`electricity_forecast_model2.5`（只读锁定）、`deep_sgdf_delta_repo`。
> 目标：提取**可迁移思路**，识别**不可直接搬运**的旧代码，给出 3.0 重构方向。
> 日期：2026-07-06

---

## 1. RT916_SpikeFusionNet —— 可迁移经验

**位置**：`electricity_forecast_model2.0_exp/RT916_SpikeFusionNet/`
**关键文件**：`src/rt916_spikefusionnet/core.py`(AnnualSpikeGatedTimesNetV2)、`model.py`(SMAPEFloor50Loss / SegmentedSMAPECappedLoss)、`policy.py`(rule_policy, stage3_hour_mask, capped_smape_series floor=50)。

**核心思想（可迁移）**：
- **分段建模头**：按 `1_8 / 9_16 / 17_24` 分三个时段头，分别学习峰谷形态 —— 对极端价分段处理有效。
- **SMAPE-floor50 损失**：分母加 floor=50 压制近零/负价/极端值的梯度爆炸，使模型敢于预测负价而不被惩罚崩坏。
- **滚动训练护栏**：`rule_policy` 用滚动 12 月训练 + 1 月验证，要求 stage3 改善≥+0.3 且恶化天数不超改善天数才采用。

**结论**：RT916 是**基础预测器**，不是校正器。其"分段头 + floor-50 损失 + 滚动护栏"可作为 3.0 基础模型训练思路，但**整包 DO-NOT-COPY**（69KB 单体，强耦合旧 pipeline）。
**重要安全提示**：RT916 曾存在目标泄漏（目标派生滞后/滚动特征），已修复为"推断时目标派生特征在 asof cutoff 后重算"。3.0 任何校正特征必须 cutoff 安全。

---

## 2. SGDFNet —— 可迁移经验（最像残差校正器）

**位置**：`electricity_forecast_model2.0_exp/SGDFNet/`
**关键文件**：`src/sgdfnet/protocol_b_cutoff.py`(run_protocol_b_cutoff_experiment)、`protocol_b.py`(`_build_hour_bias_map` / `_build_segment_hour_bias_map` / `_build_error_gate_bias_map` / `_apply_calibration_map`)、`models.py`(DeltaRegressor / SegmentConditionedDeltaRegressor)。

**核心思想（高度可迁移 → 作为 residual_corrector 原型）**：
- **delta 分解**：预测 `delta = RT − DA`，再 `rt_hat = da_anchor + delta_hat`。即把实时预测拆成"日前锚 + 实时残差"，残差更易建模。
- **分段偏差校准映射**：按 `(segment, hour, month_bucket)` 构建中位数残差校准表（`_apply_calibration_map`），对预测做偏差修正。
- **error_gate 门控**：仅当误差门控通过才施加校准，避免噪声小时被带偏。
- **D15 cutoff + walk-forward**：同日内 cutoff 后窗口用 DA 填充并加 delta bias，严格时序安全。
- 实测 RT capped SMAPE 16.59（2026-01~05），是较优基线。

**结论**：SGDFNet 的 **delta-on-delta + 分段/时段偏差校准** 是 3.0 residual_corrector 的最佳数学原型。但它是**完整模型**而非 shadow 层；3.0 若只做影子校正，应**只取其"分段偏差校准映射"思路**，不要整体训练 SGDFNet。
**DO-NOT-COPY**：旧 `protocol_b.py`（非 cutoff 版本）路径有泄漏，禁用。

---

## 3. TimeMixer —— 可迁移经验（教训为主）

**位置**：`electricity_forecast_model2.0_exp/TimeMixer/`
**发现**：该目录**无独立 README / 失败案例 postmortem**；spike 处理仅以配置候选形式存在（`candidate_configs/module_b_spike_residual_v1.json`，已被 fusion V1 采纳为默认 TM leg）；可调参记录散见 `docs/fusion_experiments_summary.md`。

**结论**：TimeMixer 自身 spike 残差逻辑**未沉淀为可复用文档**。建议 3.0 **不依赖 TM 的 spike 残差逻辑**，直接将其作为基础模型输入之一即可。其价值在于作为 4 个 RT 基模型之一提供多样性（与 timesfm/sgdfnet/rt916 形成分歧度特征）。

---

## 4. 2.5 当前 classifier / realtime correction 现状（efm3.0 内）

**位置**：`efm3.0/ExtremPriceClf/`（源自 2.5）
**关键文件**：`merge_model/core/extreme_price_radar/{classifier,pipeline,features,data_builder}.py`、`merge_model/core/stage2_model/{lightgbm,xgboost,catboost}_model.py`、`merge_model/core/cascade_daily.py`。

**架构（两阶段级联负价分类器）**：
- 阶段1 `ExtremePriceRadar`：轻量特征工程分类器 → `p1_prob`。
- 阶段2：对灰度区(`gray_low=0.13 ~ gray_high=0.68`)样本用 XGBoost/LightGBM/CatBoost 重训 → `p2_prob`。
- 决策：`p1 > gray_high → 正例`；`p1 < gray_low → 负例`；否则取 `p2`。
- **动态灰度**：90 天窗口搜索阈值，约束 `recall≥0.95, precision≥0.80`。
- 标签：极端负电价 `≤ -50`。

**融合改价逻辑（历史，现已弃用）**：`final_pred==1 且 预测实时电价 ≤ 100 → 输出 -80`；否则保留原预测。

**⚠️ 2.5 后期关键变更（来自 git log）**：
- `ceb1574` 最终输出**纯融合模型电价**，单独输出一份 -80 概率文件。
- `90502e8` **分类器运行失败时，不对预测电价进行修正**（删除原降级修正策略）。
- 即：**2.5 当前正式链路中，分类器只产出概率，不再改价**。final_outputs 是纯融合。

**对 P3 的意义**：P3 要验证的正是"是否值得在 3.0 重新引入改价"。2.5 的保守改价规则（`预测≤100 才推 -80`）是我们可以直接借鉴的护栏基准。

---

## 5. 2.5 final_outputs / postflight 安全边界（efm3.0）

**位置**：`efm3.0/pipelines/`、`efm3.0/main.py`（只读审查，不修改）
**从 README + PROJECT_LAYOUT 提炼的约束（必须被 3.0 修正系统继承）**：
- 交付文件 `submission_ready.csv`：**24 行 × 6 列、0 NaN**，否则 postflight 失败。
- 交付状态机：`NORMAL(0) / DEGRADED_DELIVERED(2) / FAILED_NO_DELIVERY(1)`，含 fallback 兜底。
- 任何修正模块**不得破坏 24h 完整性 / 不得引入 NaN / 不得越界**（价格生理范围）。
- 实时模型必须遵守 **D14 cutoff-safe**：预测 D+1 实时价只能用 D 日 14:00 前可见信息。

**对 P3 的约束**：修正系统的 guard 必须复刻这些边界（NaN guard、24h 完整 guard、价格范围 guard、cutoff 审计）。

---

## 6. 不建议直接搬运的旧代码（DO-NOT-COPY）

| 来源 | 文件/模块 | 原因 |
|------|-----------|------|
| 2.0_exp/RT916 | 整包 SpikeFusionNet | 强耦合旧 pipeline，单体 69KB，是基础预测器非校正器 |
| 2.0_exp/SGDFNet | 旧 `protocol_b.py`（非 cutoff） | 含目标泄漏路径 |
| 2.0_exp/fusion | `meta_learner_v3.py` / `dynamic_router.py` 作**正式路径** | fusion README 明确 V1 正式路径用固定分段权重，元学习/路由非默认 |
| 任何仓库 | 使用 D 日 14:00 后或 D+1 actual 的特征工程 | 目标泄漏，违反 D14 cutoff |
| 2.5 | 旧"降级修正策略"（已被删除） | 已被 2.5 证明不安全而移除 |

---

## 7. 3.0 推荐重构方向（可迁移 → 新模块）

从 2.0_exp / 2.5 提炼，重构成 **shadow-only、可解释、可关闭、可回滚** 的 Extreme Price Correction System：

1. **negative_price_classifier**：沿用两阶段级联 + 动态灰度思路，但标签统一为 `≤ -50`（或按山东实际用 `≤ 0` 聚焦 -80 地板），特征 cutoff 安全。
2. **spike_price_classifier**：复用 `fusion/spike_detector.py` 两阶段 GBM（`|spread|>100 或 >800 或 <-80` 标签，momentum_factor=0.85, prob_threshold=0.35），但 `rt_prev/da_prev` 特征须仅用 cutoff 前实际值。
3. **residual_corrector**：采用 SGDFNet 式 `delta=RT-DA` + `(segment,hour,month_bucket)` 分段中位数偏差校准（expanding 历史残差，leakage-free）。
4. **correction_guard**：复用 `extreme/` 的 `max_lift_ratio=0.35 / max_absolute_lift=350 / period_9_16_boost` 与 NegativeResidualCorrector 的 `max_downward_ratio / min_pred_floor=-100 / protect_9_16`。
5. **rollback_guard**：复用 residual_stack 的"上行只正 / 下行只负 / 互斥"原则 + 全链路 reason 追溯（`schema.py` MODULE 序列）。
6. **correction_reporter**：before/after 指标（MAE/RMSE/sMAPE_floor50 + 子集 + 分时段 + 分类 P/R/F1）。
7. **影子范式**：直接复用 `docs/PHASE12_INTRADAY_SHADOW_REPLAY.md` 的 SHADOW_ONLY / LOW_WEIGHT / FULL_DAY 门控 —— shadow 模式不修改任何预测列（`applied=false`），low_weight=`(1-w)*base + w*corrected`（w≈0.093）作为可选软启用。

**山东场景适用性论证**：
- 负价几乎恒为 **-80 地板**（本数据 167/213=-80，中位 -80.0）→ 一旦 classifier 高置信且 fused 已偏低，推向 -80 几乎零误差，价值极高且风险可控（保守护栏：仅当 fused≤阈值才改）。
- 尖峰 500~1291，fused 系统性低估（88% 低估>100）→ 有界上行 lift 可缓解系统低估偏差，但需严格 cap 防止正常时段被带飞。
- 跳变/深 V：分段偏差校准 + 模型分歧度特征可捕捉部分结构跳变。

---

## 8. 数据验证补强（来自本仓 ledger，非旧代码）

见 `docs/p3_technical_live_log.md` §3。结论：负价漏判是最高价值修正点（fused 中位 -4.3 vs actual -80），尖峰系统性低估次之，正常时段已较好（sMAPE 25.9）需重点防伤。
