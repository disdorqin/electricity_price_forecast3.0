# P1 Dayahead Candidate Review Report

> Reviewer: validation gate (no model replacement, no submission_ready.csv write)
> Review date: 2026-07-07
> Package under review: `models/exports/efm3_candidates/dayahead/efm3_candidates_20260707/`
> Source run: `run_full_rich_v4` (rich frame, 90d window) + `ablation_cfg05_180` (180d)

---

## 0. 一句话结论（先讲红线）

**cfg05 在 2025-11~2026-02 四个硬月份上 sMAPE_floor50 = 14.68%，确实优于「忠实复刻的 2.5 ThreeStageLGBM」（21.87%），证明 rich 特征工程显著优于 2.5 特征。但它并没有、也不能据此声称「击败 2.5 日前融合系统」——因为 2.5 受信任冠军 `best_two_average` = 11.85% 是在一个更easy的单月窗口（Feb1–Mar2）测的，二者不同窗口、不可直接比较。历史 11.27% 因数据泄露已被作废。**

因此：
- **recommended_status = candidate**（不是 shadow，不是 champion）
- **P1_MERGE_DECISION = MERGE_CANDIDATE_REGISTRY**（只写文档 + 候选注册，禁止进 shadow/champion，禁止改 main.py / final_outputs.py / ledger_predict.py / submission_ready.csv）
- **P1_REVIEW_RESULT = PARTIAL**（包缺 gating 文件 + baseline 口径未对齐到同窗口 2.5 融合）

---

## 1. Candidate Package

- **package path**: `models/exports/efm3_candidates/dayahead/efm3_candidates_20260707/`
- **files complete**:
  | 要求文件 | 状态 |
  |---|---|
  | FINAL_REPORT.md | ✅ 存在（但对比口径有误导，见 §2/§9） |
  | cfg05_predictions.csv | ✅ 存在，schema 通过（见 §3） |
  | metrics.json | ❌ **包内缺失**（源文件在 `models/outputs/p1_dayahead/run_full_rich_v4/metrics/metrics.json`，本次审阅已读取） |
  | comparison_report.md | ❌ 命名不符（实际为 `phase_d_comparison.md`） |
  | ablation_report.md | ❌ 命名不符（实际为 `phase_e_ablation.md`） |
  | manifest.json | ❌ **缺失** → 本次审阅已补生成 |
  | promotion_decision.json | ❌ **缺失** → 本次审阅已补生成 |
  | config snapshot | ⚠️ 有 `p1_daemon_config.yaml` + `daemon_state.json`（daemon 配置快照，非模型 config 快照） |

  → **PARTIAL**：缺 `metrics.json` / `manifest.json` / `promotion_decision.json`，且两份报告文件名不符合候选包约定。

- **manifest**: 本次审阅补生成（见包内 `manifest.json`）。
- **promotion_decision**: 本次审阅补生成（见包内 `promotion_decision.json`），`decision = candidate`，`gate = shadow_blocked_until_same_window_validation`。
- **predictions**: `cfg05_predictions.csv`（2880 行 = 120 天 × 24，schema 通过）。
- **config snapshot**: `p1_daemon_config.yaml`（含 `gpu_disabled` 上下文）、`daemon_state.json`（`gpu_disabled: true`）。

---

## 2. Baseline Alignment

| Baseline | sMAPE_floor50 | Data Window | Comparable? | Notes |
| --- | ---: | --- | --- | --- |
| baseline_lgbm25 (忠实 2.5 ThreeStageLGBM) | 21.87（11月均值）/ 17.51（Feb） | 2025–2026 硬月份 | YES（同硬窗口） | cfg05 14.68% **诚实地优于**它 |
| 24f best catboost | 20.15 | 同硬窗口 | YES | cfg05 优于 |
| rich cfg05 90d | 14.68 | 2025-11~2026-02（4月, 120d） | self | 候选 |
| rich cfg05 180d | 14.25 | 同 | self | 候选 |
| **2.5 fused / trusted champion (best_two_average)** | **11.85** | **Feb1–Mar2（30d, easy 窗口）** | **NO（不同窗口）** | **未在同 4 硬月窗口重测 → 不能声称 cfg05 击败它** |
| old cfg05 (prior) | 11.48 | prior 30d（easier） | NO（不同窗口） | 同一模型，乐观窗口；本包 4 硬月=14.68% 才是硬窗口诚实值 |
| old lgbm_spike_residual | 11.27 | 旧实验 | **INVALIDATED（data leakage）** | **严禁作为基线**（见 `dayahead_trusted_champion_report.md`） |

**口径澄清（关键）**：
- 用户口中的「2.5 日前融合 ≈ 12%」实际指的是 **cfg05 量级 / best_two_average 量级**，并非「忠实复刻的 2.5 ThreeStageLGBM」。忠实复刻 2.5 = 21.87%/17.51%（见 `p1_dayahead_experience.md` 第 26 行权威校正）。
- 实验只算了「忠实 2.5 单模型基线」(21.87%)，**从未计算 2.5 融合/受信任冠军在相同 4 硬月窗口上的数字**。FINAL_REPORT 把 21.87% 当成「2.5 系统」对比并得出「+5.47pp 提升」，属于红线禁止的第 4/5 条（只和 baseline_lgbm25 比就声称超过 2.5 系统）。
- 11.27% 已因 `y_true` 泄露作废，不得纳入任何对比。

---

## 3. Schema / Safety Audit

| Check | Result | Notes |
| --- | --- | --- |
| 24 rows per day | PASS | 120 天全部恰好 24 行（0 天异常） |
| hour_business 1..24 | PASS | 集合 = {1..24} |
| period valid | PASS | 仅 `1_8` / `9_16` / `17_24` |
| y_pred no NaN | PASS | NaN 计数 = 0（与 metrics.json 一致） |
| leakage check | PASS | CSV 仅含预测列 + 评估用 `y_true`（允许项）；无任何 D+1 特征/leakage 列 |
| D+1 actual not used | PASS | 无特征列，无未来信息；`y_true` 为同日评估目标，非训练特征 |
| ds parseable | PASS | 全部可解析；注：hour=24 的 `ds` 写为次日 `00:00:00`（滚动表示），`business_day`+`hour_business` 仍唯一确定小时，无害 |
| column contract | PASS | 必含列 `business_day,ds,hour_business,period,y_pred,model_name,model_version,source_repo,run_id` 齐全；`y_true` 为允许额外列 |

⚠️ 轻微瑕疵：`run_id` 在预测文件里是 `run_full_rich_v4_cpu`（CPU 组），而 metrics/包名为 `run_full_rich_v4`，属命名不一致（数据真实，仅标记不一致）。

---

## 4. Metrics Review (period / spike)

| Model | Overall | 1_8 | 9_16 | 17_24 | Spike | Normal |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| cfg05 | 14.68 | 13.91 | 16.01 | 14.12 | 13.51 | 14.81 |
| xgboost_rich | 14.72 | 13.36 | 16.72 | 14.07 | 12.99 | 14.91 |
| catboost_rich | 15.75 | 15.64 | 17.17 | 14.44 | 13.84 | 15.96 |

- **cfg05 是否只提升 normal 而伤害 spike？** 否。cfg05 spike=13.51 < normal=14.81，spike 段反而更好。
- **cfg05 是否伤害 17_24？** 否。17_24=14.12，为三段落中次优，稳定。最弱段是 9_16=16.01。
- **xgboost_rich 是否某些 period 更强？** 是。xgboost_rich 在 1_8(13.36) 与 spike(12.99) 上最优 → 作为 period/spike diversity 备份**合理且推荐**。
- **catboost_rich 是否更稳？** 否，三段落均最弱（15.75），仅作对照。
- **是否应保留 xgboost_rich 作 ensemble diversity？** 是（见 §6）。
- **是否需要 period-aware ensemble？** 建议（cfg05 强于 9_16/17_24，xgboost 强于 1_8/spike，二者互补）。

---

## 5. Month Breakdown

| Month | Days | Baseline(24f) | cfg05 | xgboost_rich | catboost_rich | Winner | Notes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 2025-11 | 30 | 20.15(overall) | 15.28 | 15.39 | 15.66 | cfg05 | 硬月，cfg05 仍优于 24f |
| 2025-12 | 31 | 20.15(overall) | 16.39 | 16.59 | 17.77 | cfg05 | 全模型最差月（冬季高价波动） |
| 2026-01 | 31 | 20.15(overall) | 15.10 | 14.73 | 16.42 | xgboost_rich | cfg05 略逊 xgboost_rich |
| 2026-02 | 28 | 20.15(overall) | 11.67 | 11.91 | 12.87 | cfg05 | 含春节(02-17)，异常 easy，拉低均值 |

- **覆盖 2025 与 2026？** 是（Nov/Dec 2025 + Jan/Feb 2026）。
- **≥3 个月？** 是（4 个月）。
- **只是单月/少数月？** 否。
- **哪些月 cfg05 输给 baseline？** 对 24f 整体(20.15)全胜；对同期 xgboost_rich 仅在 2026-01 略逊。
- **哪些月波动最大？** 2026-02(11.67) 远优于其余(≈15–16) → **月度方差大**，4 月均值被 2 月拉低。去掉 2 月，3 硬月均值 ≈ 15.6%。
- **17_24 稳定？** 是（14.12）。
- **尖峰稳定？** 是（spike 13.51）。
- **春节窗口是否拖累？** 2026-02 实际更优（11.67），但仅 1 个春节月 → 无法下结论。
- **负价/极端值异常？** 预测含 -80 下限（山东负价地板，合法）；`negative_price_hit_rate` cfg05=72.4% → rich 模型**无负价分类器**（见 `p1_dayahead_ablation_plan.md` 第 14 行），负价段处理是已知弱项。

---

## 6. Technical Lessons

- **rich features**: rich 帧(~55 列, 90d) 相对忠实 2.5 24 特征帧，sMAPE 从 21.87% → 14.68%（同硬窗口），**特征丰富度 > 模型族**，是本次最大收益来源。
- **model family**: LightGBM(cfg05) ≈ XGBoost(xgboost_rich) ≫ CatBoost(catboost_rich) 在 rich 帧；cfg05 与 xgboost_rich 互补（period/spike 维度）。
- **window length**: 180d(14.25%) 略优于 90d(14.68%)，更长窗口方向正确，但增益小（0.43pp），需更多窗口验证。
- **CPU-only**: 本机 GPU 路径不稳定（lightgbm GPU 死锁 + catboost GPU 崩溃 rc=1073807364，且笔记本睡眠杀 CUDA context）→ 全局改 CPU-only（`gpu_disabled:true`，引擎 `--cpu-only`）。结果可复现，但**引擎默认仍 GPU-preferred**（见 §9 fix 2），生产需显式或改默认。
- **failed attempts**: ①GPU 组 cfg05 死锁（num_threads=1 触发）；②catboost GPU 崩溃；③机器睡眠杀 daemon；④365d 窗口超时撞看门狗；⑤ablation 配置字符串被逐字符迭代；⑥`_finalize` 类体 `run_id` NameError；⑦phase_e 表头错位。
- **bugs fixed**: 上述 ④⑤⑥⑦ 已修；`_lgbm_device` 去 `num_threads=1`；引擎加 `--cpu-only`；daemon 加看门狗/产物判定成功/自动回退 CPU。

---

## 7. Merge Decision

**P1_MERGE_DECISION: MERGE_CANDIDATE_REGISTRY**

允许写入 3.0 的内容（本次已写入）：
- `docs/experiments/dayahead/P1_DAYAHEAD_FINAL_REVIEW.md`（本报告）
- `docs/experiments/dayahead/P1_DAYAHEAD_CFG05_CANDIDATE.md`（候选档案）
- `docs/experiments/dayahead/P1_DAYAHEAD_FEATURE_LESSONS.md`（特征经验）
- `configs/candidate_registry/dayahead_cfg05.yaml`（候选注册，status=candidate）

**禁止**（未触碰）：
- 不改 `main.py` / `final_outputs.py` / `ledger_predict.py` 正式模型列表
- 不写 `submission_ready.csv`
- 不进 shadow registry、不当 champion
- 不直接替换 3.0 正式日前模型

**升级到 shadow 的前置条件（gate）**：必须在**相同 4 硬月窗口（2025-11~2026-02）**重测 2.5 受信任冠军 `best_two_average`（及忠实 2.5 融合），证明 cfg05(14.68%) 同窗口优于 11.85% 量级，且通过负价分类器补齐 + period-aware ensemble 验证。

---

## 8. Recommended Status

**recommended_status = candidate**

（禁止 champion；shadow 被 gate 阻断，理由见 §2/§7）

---

## 9. If Fix Required（最小修复任务，不泛泛而谈）

- **fix 1（baseline 对齐，必须）**: 在 `run_full_rich_v4` 相同 4 硬月窗口重测 `best_two_average`（2.5 受信任冠军）与忠实 2.5 融合，产出同窗口对比表。这是进入 shadow 的唯一硬门槛。
- **fix 2（引擎 GPU 默认）**: 引擎 import 时 `_detect_gpu()` 与 `_lgbm_device()` 仍 GPU-preferred；建议加 `EFM_CPU_ONLY` 环境变量或把 `--cpu-only` 改为默认（仅在显式 `--gpu` 时启用），避免脱离 daemon 直接跑触发死锁。
- **fix 3（负价分类器）**: rich 帧模型无负价分类（hit_rate 仅 72%），补光伏负价分类（参考 2.5 的 0.7 阈值 -80 校正）后再评估负价段。
- **fix 4（包 gating 文件）**: 候选包补 `metrics.json`（从 outputs 拷贝）、`manifest.json`、`promotion_decision.json`；报告文件名统一为 `comparison_report.md` / `ablation_report.md`。
- **fix 5（FINAL_REPORT 误导）**: 原报告把 21.87% 当「2.5 系统」对比；需改为同时报告「忠实 2.5 基线 21.87%」与「受信任冠军 best_two_average 11.85% / cfg05 标杆 11.67%」两列，明确非同窗口不可比。
- **fix 6（cfg05 计时）**: metrics.json 中 cfg05 `train_infer_time_s=0.0`（skip 路径未计时），补真实 CPU 训练耗时以便生产容量评估。
- **rerun scope**: 仅补 fix 1（同窗口重测冠军）+ fix 3（负价）+ fix 4/5/6（文档/计时），**无需重跑 cfg05 主体**（已可复现）。
- **expected output**: 同窗口对比表 + 负价段误差 + 完整候选包 → 满足 gate 后可重评 `recommended_status = shadow`。

---

## 10. Final Verdict

**P1_REVIEW_RESULT: PARTIAL**

理由：
1. 实验本身真实、可复现、schema 通过、cfg05 诚实地优于忠实 2.5 基线（21.87% → 14.68%）。
2. 但 **baseline 口径未对齐**：未在同窗口证明 cfg05 优于 2.5 受信任冠军（11.85%，easy 窗口），故不能进 shadow。
3. 候选包缺 `metrics.json` / `manifest.json` / `promotion_decision.json`，且原始 FINAL_REPORT 对比口径有误导（红线第 4/5 条）。
4. 历史 11.27% 已作废、11.48%/11.67% 为更 easy 窗口，均不改变「cfg05 当前 4 硬月 = 14.68%」的诚实结论。

→ 处置：**登记为 candidate（MERGE_CANDIDATE_REGISTRY），shadow 被 gate 阻断**，待 fix 1/3 完成后重评。
