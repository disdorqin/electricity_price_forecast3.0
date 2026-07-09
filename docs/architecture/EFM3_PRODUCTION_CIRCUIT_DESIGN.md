# EFM3 生产电路设计文档（Production Circuit Design）

> 分支：`agent/production-circuit-gap-audit-db-redesign`
> 配套：`EFM25_PRODUCTION_CIRCUIT_REVERSE_ENGINEERING.md`、`EFM3_PRODUCTION_CIRCUIT_GAP_AUDIT.md`、`EFM3_DB_LEDGER_V2_DESIGN.md`
> 日期：2026-07-09
> 设计目标：**在 3.0 中以 DB-Ledger V2 形式重建 2.5 生产电路骨架**，每步落库、诚实标注 benchmark/placeholder，**绝不把 da_anchor 伪装成最终模型输出**。

---

## 1. 设计原则（不可妥协）

1. **诚实优先（Honesty over vanity）**：没有真实模型输出时，节点显式标记为 `PARTIAL` / `NEEDS_MODEL_OUTPUT` / `SKIPPED`，指标标 `UNCLEAR`；**绝不 fillna(0) 式伪造、绝不把 benchmark 当模型口径**。
2. **每步落库（Every step persists）**：从 `data_update` 到 `delivery_final`，每个节点写一行 `efm_pipeline_steps`，并在相应 V2 表留下结构化痕迹。
3. **双子链分离（DA / RT separation）**：日前与实时各自走 `raw_model → module_repair → weighted → fused → classifier_adjusted → task_final`，互不污染；最终 `delivery_final` 显式记录 DA/RT 来源。
4. **血缘可溯（Lineage）**：`efm_prediction_lineage_edges` 记录 raw→repair→fuse→select→classifier→separator 的每一条边。
5. **非破坏（Non-breaking）**：仅新建表 + 新建 `pipelines/production_circuit/`，旧 `full_chain_orchestrator` 与 `--chain official/seasonal_da_router` 完全保留。
6. **指标分 scope（Scope-separated metrics）**：`benchmark` vs `dayahead`/`realtime`/`delivery` 严格隔离，防 benchmark 伪装成生产口径。

---

## 2. 模块拓扑（`pipelines/production_circuit/`）

| 文件 | 职责 | 关键导出 |
|---|---|---|
| `contracts.py` | 任务/阶段/状态枚举 + 数据契约（无 DB/模型副作用，单测安全） | `CircuitTask` `CircuitStage` `RepairStage` `StepStatus` `PredictionBatch` `CircuitStepResult` `RepairDecision` `FusionCandidate` `TaskFinal` `DeliveryFinal` `STAGE_TO_TASK` |
| `step_recorder.py` | 所有 V2 表写入（幂等 upsert）+ 批次 hash | `StepRecorder` `insert_metric_run` |
| `dayahead_chain.py` | 日前子链入口 + 日前 task_final（分离写 `efm_task_finals`） | `run_day_ahead_chain` `run_day_ahead_task_final` |
| `realtime_chain.py` | 实时子链入口 + 实时 task_final（无模型→SKIPPED） | `run_real_time_chain` `run_real_time_task_final` |
| `repair_chain.py` | 模块修补（no_nan/range_guard/spike_guard），记录每条决策 | `run_repair` |
| `fusion_chain.py` | 融合（重归一化、记录全部候选、绝不 fillna(0)） | `run_fusion` |
| `classifier_chain.py` | 日前直通 / 实时 placeholder（2.5 分类器未迁移） | `run_classifier` |
| `separator_chain.py` | 分离器修补（cross_task_fusion → separator_repaired） | `run_separator_repair` |
| `delivery_chain.py` | 跨任务融合 + 交付终值（显式 provenance） | `run_cross_task_fusion` `run_delivery_final` |
| `circuit_orchestrator.py` | 19 步编排入口 | `CircuitContext` `run_production_circuit` |
| `__init__.py` | 包导出 | `CircuitContext` `run_production_circuit` |

---

## 3. 19 步编排（与 2.5 语义对齐）

| # | 步骤 | 状态（skeleton 下） | 落库表 |
|---|---|---|---|
| 1 | data_update | COMPLETE（来源已回填） | efm_pipeline_steps |
| 2 | feature_snapshot | COMPLETE（轻量记录） | efm_pipeline_steps |
| 3 | dayahead_chain（raw→`benchmark_da_anchor`） | PARTIAL / MISSING_MODEL_OUTPUT | efm_predictions, efm_pipeline_steps, efm_prediction_batches |
| 4 | dayahead_repair | COMPLETE/SKIPPED | efm_repair_decisions, lineage |
| 5 | dayahead_fusion | COMPLETE（单候选=da_anchor） | efm_fusion_candidates, efm_predictions |
| 6 | dayahead_classifier | COMPLETE（直通） | efm_predictions |
| 7 | dayahead_task_final | COMPLETE（分离写 task_finals） | efm_task_finals, lineage |
| 8 | realtime_chain | **PARTIAL / NEEDS_MODEL_OUTPUT** | efm_pipeline_steps |
| 9 | realtime_repair | SKIPPED（无源） | efm_pipeline_steps |
| 10 | realtime_fusion | SKIPPED | efm_pipeline_steps |
| 11 | realtime_classifier | SKIPPED（placeholder） | efm_pipeline_steps |
| 12 | realtime_task_final | SKIPPED（RT final 缺席） | efm_pipeline_steps |
| 13 | cross_task_fusion | PARTIAL（仅 DA） | efm_predictions, efm_pipeline_steps |
| 14 | separator_repair | COMPLETE/SKIPPED | efm_repair_decisions, efm_predictions |
| 15 | delivery_final | PARTIAL（无 RT→dayahead fallback） | efm_delivery_finals, efm_predictions, lineage |
| 16 | postflight | COMPLETE/FAIL | efm_pipeline_steps（复用现有 db_postflight） |
| 17 | metrics | COMPLETE（benchmark 落库；生产 scope UNCLEAR 跳过） | efm_metric_runs |
| 18 | finish_run | COMPLETE | efm_runs, efm_pipeline_steps |

> **诚实结论**：在 2.5 模型输出迁移前，`run_production_circuit` 整体返回 `status=PARTIAL`、`recommendation=READY_TO_MIGRATE_2_5_MODEL_OUTPUTS`、`smoke_result=PARTIAL`。实时子链不写任何伪造行。

---

## 4. 诚实状态语义（Honest Status Contract）

`contracts.StepStatus`：
- `PENDING / RUNNING / COMPLETE / PARTIAL / FAIL / SKIPPED / NEEDS_MODEL_OUTPUT`

规则：
- 真实模型输出缺失 → 该节点 `NEEDS_MODEL_OUTPUT` 或 `SKIPPED`，且**不向下游注入任何预测行**。
- 仅有 benchmark（da_anchor）→ 该子链 `PARTIAL`，`task_final` 标注 `source_policy="benchmark_da_anchor"`，其 dayahead-scope 指标**不计算**（计算会≈0% 且误导）。
- 交付终值无 RT → `delivery_policy="dayahead_only_fallback"`，`fallback_reason="realtime_final_missing"`，状态 `PARTIAL`。
- 指标：仅 `benchmark` scope 落 `efm_metric_runs`；`dayahead`/`realtime` 生产 scope 在有真模型 final 时才计算，否则 `UNCLEAR`/`NO_DATA`，**绝不伪造**。

---

## 5. 与 2.5 语义的对齐映射（待迁移后启用）

| 2.5 语义 | 3.0 落点（迁移后） |
|---|---|
| `DAYAHEAD_MODELS=[lightgbm,timesfm,timemixer]` | `dayahead_chain` 读 `raw_model` 阶段（3 模型）→ 写 `efm_predictions.stage=dayahead_raw_model` |
| `REALTIME_MODELS=[timesfm,sgdfnet,timemixer,rt916]` | `realtime_chain` 读 `realtime_raw_model`（4 模型） |
| `DailyLedgerGEF` 动态权重 `eta=0.8, floor=0.03` | 新增 `weight_chain`（当前 skeleton 跳过加权，step 4/9 留空待接） |
| `apply_daily_ledger_weights`（重归一化+无 fillna） | `fusion_chain.run_fusion` 已含重归一化+全候选记录 |
| 分类器 `(final_pred==1)&(y_fused<=100)→-80` | `classifier_chain.run_classifier(REALTIME)` 迁移 `classifier_bridge` 逻辑 |
| `efm_task_finals` 分离 DA/RT | 已实现 `dayahead_task_final` / `realtime_task_final` |
| `historical_same_hour_median` 兜底 | 后续接入 `StepStatus.FAIL → fallback`（当前 skeleton 未实现兜底） |

---

## 6. CLI 与调用

```bash
# 旧链（保留，默认不替换）
python main.py --date 2026-02-14 --chain official --mode formal_sim --use-db --db-url "$EFM3_DB_URL"

# 新生产电路（新增）
python main.py --date 2026-02-14 --chain production_circuit --use-db --db-url "$EFM3_DB_URL"
```

`main.py` 在 `if use_db or mode != "dry_run":` 分支内读取 `args.chain`；`production_circuit` 走 `run_production_circuit`，返回 `COMPLETE`→exit 0 / `PARTIAL`→exit 1。`formal_sim` 下**不写 submission**。

---

## 7. 验收（Smoke 预期）

- `2026-02-14` smoke：
  - `efm_pipeline_steps` 出现 18+ 行，覆盖 DA 子链、`realtime_chain=NEEDS_MODEL_OUTPUT`、delivery=PARTIAL。
  - `efm_task_finals` 有 dayahead 24 行（`source_policy=benchmark_da_anchor`）。
  - `efm_delivery_finals` 有 24 行（`fallback_reason=realtime_final_missing`）。
  - `efm_metric_runs` 有 1 行 `metric_scope=benchmark`（明确标注 NOT model）。
  - 返回 `PRODUCTION_CIRCUIT_SMOKE_RESULT: PARTIAL` + `READY_TO_MIGRATE_2_5_MODEL_OUTPUTS`。
- 不出现：任何 realtime 伪造行、任何把 benchmark 当模型的指标。
