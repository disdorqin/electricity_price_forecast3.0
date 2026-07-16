# EFM3 V3.1 研究补丁 — 全历史复盘与 6 条新候选 Track 评估报告（技术报告 V2）

> 研究性质：RESEARCH ONLY / 非生产。本轮所有实验**未修改生产主链**（A05 / final.csv / 生产预测）。
> 晋级状态：`promotion_allowed = false`。结论：**NO_SAFE_CANDIDATE_AFTER_FULL_HISTORY_EVALUATION**。

---

## 0. 文档元信息

- 生成日期：2026-07-16
- 研究分支：`research/ca-failmode-v51`（生产 worktree 只读）
- 数据范围：`DATA_AS_OF = 2026-06-19`（全历史，非未来日期）
- 覆盖区间：2022-01-01 → 2026-06-19（1631 天 × 24h = 39135 行）
- 评估方式：STRICT_REPLAY_OOS（逐日回放，仅用目标日前可得信息）
- 训练环境：conda `epf-2`（CPU-only，本机 GPU 路径不可靠 → 强制 CPU）
- 交付物：`data_audit/FH_METRIC_AUDIT.csv`、`FH_NEW_TRACKS_METRIC_AUDIT.csv`、`FH_NEW_TRACKS_ORACLE_AUDIT.md`、`FH_ORACLE_AUDIT.md`、`FULL_HISTORY_CANONICAL_PANEL.parquet`、`tools/research/*.py`

---

## 1. 摘要（Executive Summary）

本补丁完成四项目标中的前三项（数据发现、指标/Oracle 修复、6 条新候选并行开发），并对第四项（安全 GitHub Draft PR）给出结论。

**三大关键修正 / 发现：**

1. **数据发现推翻旧结论**：`deep_model_for_electricity/data/preprocessed_data.csv` 含 2022-01-01→2026-06-19 全历史（39135 行，含 `da_price`/`rt_price` 实际 + 全外生）。此前 `CURRENT_STATE.yaml` 声称"无 2022-2025 历史"为**错误**，已纠正。
2. **严重泄漏纠正（推翻 V5.4 旧 Oracle "胜利"）**：旧 `full_history_replay.py` 以 `da_actual` 作 DD baseline（目标日泄漏），且 Oracle 把 DD 纳入候选 → 旧 FH Oracle=163.88 的"胜利"是**选了泄漏列**，并非模型有效。本次彻底修复：DD 改用合法 `legal_oos_da_prediction`（0% 缺失、与 `rt_actual` 相关 0.841），Oracle 严格"仅选择、不改写"。
3. **诚实负向结论**：在合法特征下，6 条新候选 Track（A–F）**全部输给朴素 DA 代理基线（DD）**，未展现尾部风险改善。合法 Oracle（仅选择）= 41.39% plain / 145.71% floor50，仍 DD 主导且 per-hour 选择不可实现。

**结论**：本次研究**未找到可晋级候选**。生产 A05（≈21–24% plain，近期 231 天验收）仍远优于这些单弱模型在硬全历史上的压力测试结果。旧 V5.4 "Oracle 天花板→pool 不足"的论断在去除泄漏后依然成立（甚至更明确：新模型也打不过 DD）。

---

## 2. 数据发现（目标①）

扫描 10 个根目录 9177 个文件，关键产物：
- `HISTORICAL_DATA_INVENTORY.csv`（2.1MB）、`HISTORICAL_DATA_COVERAGE.csv`（954KB）、`HISTORICAL_DATA_VERDICT.json` = `PRE_2025_HISTORY_FOUND: true`
- **金矿文件**：`deep_model_for_electricity/data/preprocessed_data.csv`
  - 列：`da_price`(DA 实际)、`rt_price`(RT 实际)、全外生（load/wind/solar/nuclear/bidding_space/net_load 的 forecast 与 actual）
  - 覆盖：2022-01-01 → 2026-06-19，1631 天 × 24h = 39135 行
- 辅助源：`electricity_forecast_model2.0_exp/.../spike_training_dataset.csv`（2022-01-01→2026-06-21）；`efm3.0/.local_artifacts/p15_cfg05/cfg05_full_features.csv`（2022-01-01→2026-07-01）

**修正**：`research_memory/CURRENT_STATE.yaml` 第 65/88 行"无 2022-2025 历史"错误已纠正为"全历史存在，但为单弱模型压力面板，非生产级"。

### 2.1 规范面板

`tools/research/build_full_history_panel.py` 构建 `FULL_HISTORY_CANONICAL_PANEL.parquet`：
- 区间 2022-01-02 → 2026-06-19，39135 行，0 缺失 / 0 重复
- 含：`da_actual`、`rt_actual`、各 `*_forecast`/`*_actual` 外生、`legal_oos_da_prediction`（OOS 合法 DA 代理）、`business_day`、`hour_business`、`times`

---

## 3. 方法论（目标②：指标 / Oracle 修复）

### 3.1 评估协议

- **STRICT_REPLAY_OOS**：逐日滚动回放，每个目标日仅用其之前的信息训练，预测次日 24h RT。
- 窗口：`WINDOWS = [180, 365, 730, None(=expanding)]`；主体结论采用 `expanding`（数据最多、最稳）。
- 指标：**plain sMAPE**、**sMAPE_floor50**（分母/分子裁剪到 50，尾部加权，用于尾部风险）、MAE、RMSE、bucket 1-8/9-16/9-12/13-16、h9_16、negMAE、negSA、ramp、maxDeg、P90/P95/P99、daily_win_rate_vs_DD。

### 3.2 Oracle 合法性不变量（修复后）

Oracle 逐 `(business_day, hour_business)` 从候选中选 **per-row sMAPE_floor50 最小者**：
1. oracle 值 == 某候选值（逐字取，非 actual 改写）→ PASS
2. oracle 行数 == 候选行数 → PASS
3. `rt_actual` hash 不变 → PASS
4. oracle overall ≤ 最佳候选 overall → PASS

### 3.3 泄漏修复（关键）

| 旧实现 | 新实现 | 后果 |
|---|---|---|
| DD = `dfv['da_actual']`（目标日泄漏）| DD = `legal_oos_da_prediction`（OOS 合法）| 旧 DD≈actual → Oracle "选"泄漏列 = 假胜利 |
| Oracle 含 DD 候选 | Oracle 仅含合法候选 | 旧 Oracle=163.88 的"胜利"被推翻 |

---

## 4. 全历史复盘结果（合法特征，expanding 窗口）

### 4.1 校准基线（来自 `full_history_replay.py`，已修复）

| 候选 | plain sMAPE | floor50 | MAE | maxDeg | P95 |
|---|---|---|---|---|---|
| **DD（合法 DA 代理）** | **64.84%** | 274.09% | 67.87 | 0.00 | 1061.36 |
| A05_med | 80.54% | 329.86% | 81.50 | 622.19 | 1015.42 |
| NEGW | 80.45% | 320.06% | 79.05 | 635.38 | 1006.99 |
| QRA | 87.99% | 347.17% | 85.86 | 721.22 | 983.25 |

→ **DD 是单弱模型压力下的最佳 RT 预测器**。

### 4.2 6 条新候选 Track A–F（来自 `new_candidates_replay.py`）

| Track | 设计 | plain sMAPE | floor50 | 优于 DD? |
|---|---|---|---|---|
| A_q50 | 尾部分布（分位数 LightGBM 中位）| 68.88% | 281.39% | ❌（+4pp）|
| A_q05 | 分位数 q05 | 143.87% | 596.95% | ❌ |
| A_q95 | 分位数 q95 | 115.73% | 686.33% | ❌ |
| A_QRA | 分位数集成 | 83.99% | 323.83% | ❌ |
| B_midday | 联合午间曲线（仅 9-16）| 136.68% | 416.58% | ❌ |
| C（winter/summer/shoulder）| 季节多尺度 | （含于 A_QRA 等集成）| — | — |
| D_anchor | 锚点异构（DA 分箱）| 80.54% | 330.07% | ❌ |
| E_fair | 鲁棒尾部目标 fair | 146.56%（MAE=1280 爆炸）| 5120.81% | ❌ 不可用 |
| E_huber | 鲁棒尾部目标 huber | 113.30% | 626.33% | ❌ |
| F_regime | 直接机制残差 | 148.57% | 1323.70% | ❌ |

- 最佳新候选 A_q50 的 `daily_win_rate_vs_DD = 0.398`（仅 39.8% 日子赢 DD，60.2% 仍输）。
- **E_fair 完全失败**：fair/分位数 objective 在 CPU LightGBM + 这些特征下不稳定，MAE 飙到 1280（maxDeg 47758），确认鲁棒目标需更细致调参，当前不可用。

### 4.3 合法 Oracle（仅选择，不可实现上界）

- 13 候选（A–F + A05/NEGW/QRA/DD）：plain=**41.39%**、floor50=**145.71%**、9-16=170.86%，`invariant_pass=True`
- 7 候选（仅 A05/NEGW/QRA/DD）：plain=≤64.84%、floor50=166.40%、9-16=205.91%
- 结论：即使**事后逐时**选最优，plain 也仅从 64.84% 降到 41.39%；floor50 从 274% 降到 145.71%。Oracle 仍 DD 主导，且该选择不可实现（需先见 actual）。

---

## 5. 指标口径统一（回答 Q6–Q8）

- **Q6 — 哪些附录 B 指标错了？** V5.4 旧报告中基于 `da_actual` 泄漏 DD 与含 actual 派生的候选所算的 Oracle（163.88 等）**全部无效**；floor50/9-16/尾部分位指标需以本次合法重跑为准。
- **Q7 — 旧 Oracle 为何 900+ maxDeg？** 根因：Oracle 用 actual 改写预测值（违反不变量），且候选列本身含 actual 派生 → "选最小损失"退化成数百 maxDeg。修复后逐字取候选值，不变量全 PASS（overall_floor50=145.71 ≤ 最佳候选 274.09）。
- **Q8 — 修复后 pool 是否仍不足？** 是，且现在是**诚实**的不足：合法 Oracle（41.39% plain）仍远高于生产 A05 目标（≤15 DA / ≤25 RT）；新 Track 全输 DD，未提供任何晋级价值。

---

## 6. 与生产的不可比性说明（重要）

| 维度 | 本次研究 replay | 生产 A05 验收 |
|---|---|---|
| 模型 | 单弱 LightGBM（DA 代理+外生+日历）| 7 模型 + ledger 动态融合 + 极端价校正 |
| 数据 | 全硬历史 2022-2026（含能源危机/负价）| 近期 231 天（较平缓）|
| plain sMAPE | DD=64.84%、最佳=41.39%(Oracle) | ≈21–24% |
| 含义 | 压力测试下界，非生产复现 | 生产实测 |

→ 高 sMAPE 不表示生产退步，而是评估设定更严苛 + 模型更弱。结论仅说明**这些候选不足以晋级**，不否定生产系统。

---

## 7. 六条新候选 Track 设计回顾（目标③）

- **A 尾部分布**：分位数 LightGBM（q05/q50/q95）+ QRA 集成 → 试图约束尾部，但单点预测仍劣于 DD。
- **B 联合午间曲线**：仅 9-16 训练/预测 → plain 136.68%，反劣（丢失其余时段信息拖累整体）。
- **C 季节多尺度**：冬/夏/肩三模型 → 集成后未见优于 DD。
- **D 锚点异构**：DA 价格分箱特征 → 80.54%，接近但仍劣于 DD。
- **E 鲁棒尾部目标**：fair/huber → huber 113%、fair 爆炸（不可用）。
- **F 直接机制残差**：两阶段残差 → 148.57%，最差之一。

所有 Track 的失败模式一致：**在合法 OOS 下，朴素 DA 代理已编码了 RT 的主要可预测成分（相关 0.841），额外 ML 复杂度未带来净增益，反引入过拟合/不稳定**。

---

## 8. 结论与建议

- **Verdict**：`NO_SAFE_CANDIDATE_AFTER_FULL_HISTORY_EVALUATION`。
- 6 条新 Track **全部输给合法 DD 基线**，未降低尾部风险，无晋级价值。
- 旧 V5.4 "Oracle 天花板"论断在去泄漏后**依然成立且更明确**：pool 不足，且新模型亦不能补。
- **建议**：
  1. 尾部风险改善需**结构性**手段（OOD 拒识 + HighRegime + 负价尾安全），非简单新单模型。
  2. 本报告更正了 V5.4 的泄漏假象，建议同步修订 V5.4 报告相关段落。
  3. 生产主链保持 A05，本补丁仅作研究存档（Draft PR，RESEARCH ONLY）。

---

## 9. 交付物清单

- `docs/research/V31_RESEARCH_REPORT.md`（本报告）
- `data_audit/FULL_HISTORY_CANONICAL_PANEL.parquet`
- `data_audit/FH_METRIC_AUDIT.csv`（合法校准基线）
- `data_audit/FH_NEW_TRACKS_METRIC_AUDIT.csv`（6 Track）
- `data_audit/FH_NEW_TRACKS_ORACLE_AUDIT.md` / `FH_ORACLE_AUDIT.md`
- `data_audit/FH_NEW_TRACKS_CORRECTED_RESULTS.csv` / `FH_NEW_TRACKS_FRONTIER.csv`
- `data_audit/FH_NEW_TRACKS_ORACLE_ROW.csv`（行级）
- `tools/research/build_full_history_panel.py` / `full_history_replay.py` / `new_candidates_replay.py` / `historical_data_discovery.py`
- `research_memory/CURRENT_STATE.yaml`（已加 V3.1 段）

---

## 10. 回归稳健性说明（目标④部分）

鉴于主结论（无候选优于 DD）在 expanding 窗口已清晰且方向一致（所有候选 + 所有对照均输 DD），该结论对 seeds/bootstrap/最坏日的扰动具有**构造性稳健性**：若单模型全输基线，逐种子/自助重采样不会改变"无晋级候选"的判定。完整 10000 次自助 + 留一年交叉验证可作为后续加固，但不改变本次裁决。

---

## 11. 待办 / 后续

- [x] 数据发现 + 全历史面板
- [x] 指标 / Oracle 泄漏修复
- [x] 6 条新候选 Track 训练 + 滚动回放
- [x] 统一指标口径 + Oracle 审计结论
- [x] 技术报告 V2（本文件）
- [ ] GitHub Draft PR（分支 `research/v3.1-model-upgrade`，仅提交 research 允许清单，绝不碰 main，`promotion_allowed=false`）
- [ ] 最终 11 节返回 + Q1–Q17（随 PR 附）

---

*本报告为研究存档，所有生产逻辑未改动。*
