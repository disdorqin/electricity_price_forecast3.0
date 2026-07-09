# EFM3 DB Ledger V2 设计文档

> 迁移文件：`db/migrations/005_production_circuit_schema.sql`
> 分支：`agent/production-circuit-gap-audit-db-redesign`
> 日期：2026-07-09

## 1. 为什么需要 V2

现有 `efm_predictions`（PR #12/#14/#15/#16 建立）足够表达"单条预测 + selected 标志"，但**无法表达一条生产电路**：

- 无法表达**步骤 DAG**（哪一步在何时运行、状态、耗时、I/O 计数）→ 加 `efm_pipeline_steps`。
- 无法把"一组 24 小时预测"归因到某个**来源步骤/模型版本/批次** → 加 `efm_prediction_batches`。
- 无法追踪 **raw→repair→fuse→select→classifier→separator** 的血缘 → 加 `efm_prediction_lineage_edges`。
- 无法审计**每条修补决策**（含 no_op）→ 加 `efm_repair_decisions`。
- 无法审计**融合候选与权重/排序**（含单候选融合）→ 加 `efm_fusion_candidates`。
- 无法把**日前终值**与**实时终值**分离存储（防止静默混合）→ 加 `efm_task_finals`。
- 无法记录**交付终值 provenance**（哪个 DA/RT final 喂养、何种 policy）→ 加 `efm_delivery_finals`。
- 无法把**指标按 scope 持久化**且与 benchmark 隔离 → 加 `efm_metric_runs`。

---

## 2. 迁移安全性（Non-breaking）

| 原则 | 落实 |
|---|---|
| 不 DROP/ALTER 既有表 | 005 仅 `CREATE TABLE IF NOT EXISTS` 8 张新表；PR #12/#14/#15/#16 表完全不动 |
| 可重复执行 | 全部 `IF NOT EXISTS` |
| MySQL 8 兼容 | `utf8mb4` / `InnoDB`；`DATETIME(3)`；`JSON` 列 |
| 热路径索引 | 每张表对 `(run_id, target_date)`、`(run_id, task, stage)` 等建索引 |
| 不删数据 | 纯增量；FK 用 `ON DELETE CASCADE/SET NULL` |
| 引用既有 run | 所有 V2 表 FK → `efm_runs(run_id)`；血缘 FK → `efm_predictions(id)` |

> ⚠️ 既有 `efm_predictions.task` ENUM 仅含 `dayahead/realtime/fusion/final/shadow`，**没有 `final_selected`**——写 final/final_selected 行时必须用 `task='final'`、`stage='final_selected'`（已在前序审计中固化）。V2 的 `efm_task_finals`/`efm_delivery_finals` 才是"终值真相源"，与 `efm_predictions` 解耦。

---

## 3. 表清单与职责

### 3.1 `efm_pipeline_steps` — 步骤执行账本
- 一行 = 一个电路步骤。`status` ENUM：`PENDING/RUNNING/COMPLETE/PARTIAL/FAIL/SKIPPED`。
- 记录 `input_count`/`output_count`/`runtime_ms`/`message`/`config_json`/`metrics_json`。
- 索引：`(run_id, step_order)`、`(run_id, target_date)`、`(run_id, step_name)`。

### 3.2 `efm_prediction_batches` — 预测批次
- 一组 24h 预测 = 一个 batch。`batch_hash=sha256(metadata)` 保证幂等。
- `is_final_candidate` / `is_shadow` 区分候选性质；`source_step` 指向产生它的步骤名。
- 索引：`(run_id, target_date)`、`(run_id, task, stage)`。

### 3.3 `efm_prediction_lineage_edges` — 血缘边
- `relation_type`：`repair/weight/fuse/select/fallback/classifier_adjust/separator_adjust`。
- `parent_prediction_id` → `child_prediction_id`（均 FK → `efm_predictions.id`，`SET NULL`）。
- 索引：`(run_id, target_date)`、`(child_prediction_id)`。

### 3.4 `efm_repair_decisions` — 修补决策
- 每条决策一行，**含 no_op**（值未变也记录）。
- `repair_stage`：`module_repair/weighted_repair/separator_repair/no_op`。
- `before_value`/`after_value`：`DECIMAL(12,4)`；`severity`：`info/warning/critical`。
- 索引：`(run_id, target_date)`、`(run_id, target_date, hour_business)`。

### 3.5 `efm_fusion_candidates` — 融合候选
- 所有候选一行；`weight_value`/`rank_value`/`score_json`；`selected` + `rejected_reason`。
- 单候选融合也记录（`weight=1.0`，`selected=True`），保证融合可审计。
- 索引：`(run_id, target_date)`、`(run_id, target_date, hour_business)`、`(run_id, target_date, selected)`。

### 3.6 `efm_task_finals` — 任务终值（DA/RT 分离）
- **日前与实时分别存储**，永不混成一行。`UK (run_id, target_date, task, hour_business)`。
- `final_prediction_id` FK → `efm_predictions.id`；`source_policy`/`confidence_score`。
- 这是"真模型终值"的权威表，供 delivery 与指标读取。

### 3.7 `efm_delivery_finals` — 交付终值（provenance）
- 显式 `dayahead_final_id` / `realtime_final_id` FK → `efm_task_finals.id`。
- `delivery_policy`（如 `full_delivery` / `dayahead_only_fallback`）、`separator_rule`、`fallback_reason`。
- `UK (run_id, target_date, hour_business)`。这是"对外交付价"的唯一真相源。

### 3.8 `efm_metric_runs` — 指标运行（scope 隔离）
- `metric_scope` ENUM：`dayahead/realtime/delivery/benchmark`。
- `pred_stage`/`actual_source` 明确预测与实际的语义；`config_json` 记录 `result` 标签（`OK/UNCLEAR/NO_DELIVERY/NO_DATA`）。
- 与 benchmark（da_anchor vs rt_actual）**物理隔离**，杜绝伪装成模型口径。
- 索引：`(metric_scope)`、`(run_id)`、`(target_date_start, target_date_end)`。

---

## 4. 数据流（一条预测从生到死）

```
ledger/DB 源 ──▶ efm_predictions(stage=*_raw_model)         [既有表复用]
                       │ (batch)          │ (lineage edge: weight/fuse/...)
                       ▼                  ▼
              efm_prediction_batches   efm_prediction_lineage_edges
                       │
                       ▼
              efm_repair_decisions ──(repair edge)──▶ efm_predictions(stage=*_repaired)
              efm_fusion_candidates ─(fuse edge)──▶ efm_predictions(stage=*_fused)
                       │
                       ▼
              efm_task_finals (DA / RT 分离)
                       │ (select edges)
                       ▼
              efm_delivery_finals (provenance)
                       │
                       ▼
              efm_metric_runs (scope 隔离指标)
                       │
              efm_pipeline_steps (每步状态账本，贯穿全程)
```

---

## 5. 迁移执行

```bash
mysql -u root -p efm3 < db/migrations/005_production_circuit_schema.sql
# 或经 DbConnectionManager 在首次 production_circuit 运行时执行（建议显式先跑迁移）
```

验证（预期 8 张表新增，既有表不变）：
```sql
SELECT TABLE_NAME FROM information_schema.TABLES
WHERE TABLE_SCHEMA='efm3' AND TABLE_NAME LIKE 'efm_%';
-- 应含 efm_runs, efm_actual_prices, efm_predictions, ... 以及 8 张 V2 新表
```

---

## 6. 容量与索引提示
- 每目标日 ~18 个 pipeline_steps + 2 个 task_finals(24×2) + 1 delivery_finals(24) + N lineage/repair/fusion 行。
- 月度 ~30 天 → 各表千级行；索引足以支撑按 `(run_id, target_date)` 的逐日审计查询。
- 长期（>1 年）建议对 `efm_pipeline_steps`/`efm_repair_decisions` 按 `target_date` 分区（非本次范围）。
