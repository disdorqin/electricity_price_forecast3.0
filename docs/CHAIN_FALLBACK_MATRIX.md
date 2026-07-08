# EFM3 一键完整链路 — 兜底策略矩阵 (Chain Fallback Matrix)

本文件是 EFM3 3.0 一键完整链路 (`pipelines/full_chain_orchestrator.run_full_chain`) 的**显式兜底决策矩阵**。所有失败模式都不再由编排器即兴判断，而是统一走 `common/fallback_policy.py` 中的 `FallbackDecision` 与对应的 `evaluate_*` 求值器，保证行为可测试、可审计、可复现。

代码实现见 `common/fallback_policy.py`。本矩阵共 **10 行**，覆盖 MySQL 不可用、数据更新失败、数据集未就绪、DA 锚点缺失、官方基线缺失、Shadow 模块失败、Postflight 失败、导出失败、重复 run_id、已有 target_date 运行等全部边界。

字段含义：
- **action** — 编排器应执行的动作（`fail` / `continue_file_store` / `continue_degraded` / `fallback_official_baseline_warn` / `fail_selected_check` / `partial` / `mark_delivery_failed` / `continue_main_chain` / `continue_warn`）。
- **status** — 运行级状态（`PARTIAL` / `DEGRADED` / `FAIL` / `COMPLETE`）。
- **delivery_status** — 交付级状态（`PARTIAL` / `DEGRADED_DELIVERED` / `FAILED_NO_DELIVERY` / `NORMAL`）。
- **exit_code** — 进程退出码（`0` = 成功/降级可交付；`1` = 正式链路必须失败）。
- **db_enabled** — 该分支是否仍允许写 MySQL ledger。

---

## 矩阵总表 (10 行)

| # | 失败模式 (failure) | mode | action | status | delivery_status | exit | db_enabled | 说明 |
|---|--------------------|------|--------|--------|-----------------|------|-----------|------|
| 1 | `db_unavailable` | dry_run | `continue_file_store` | PARTIAL | PARTIAL | 0 | **false** | MySQL 不可用 → 退回 `FilePredictionStore`，结果落本地文件，db_enabled=false |
| 1 | `db_unavailable` | formal  | `fail` | FAIL | FAILED_NO_DELIVERY | 1 | false | 正式链路强制要求 ledger → 直接 FAIL（exit 1） |
| 2 | `dataset_not_ready` | dry_run (有 READY 数据集) | `continue_degraded` | DEGRADED | DEGRADED_DELIVERED | 0 | true | 无最新数据更新，但存在 READY `dataset_version` → 降级交付 |
| 2 | `dataset_not_ready` | dry_run (无 READY) | `continue_degraded` | PARTIAL | PARTIAL | 0 | true | 数据集未就绪且无可用回退 → PARTIAL |
| 2 | `dataset_not_ready` | formal  | `fail` | FAIL | FAILED_NO_DELIVERY | 1 | true | 正式交付必须有 READY 数据集 → FAIL（exit 1） |
| 3 | `da_anchor_missing` | 非冬季 dry_run | `fallback_official_baseline_warn` | DEGRADED | DEGRADED_DELIVERED | 0 | true | DA 锚点缺失 → 回退官方基线（仅告警） |
| 3 | `da_anchor_missing` | 冬季 dry_run | `fallback_official_baseline_warn` | DEGRADED | DEGRADED_DELIVERED | 0 | true | 冬季回退官方基线 + WARN |
| 3 | `da_anchor_missing` | 冬季 formal (无 `--allow-router-fallback`) | `fail` | FAIL | FAILED_NO_DELIVERY | 1 | true | 冬季正式且缺 DA 锚点、未允许路由回退 → FAIL（exit 1） |
| 3 | `da_anchor_missing` | 非冬季 formal | `fallback_official_baseline_warn` | DEGRADED | DEGRADED_DELIVERED | 0 | true | 非冬季正式可回退官方基线 |
| 4 | `official_baseline_missing` | dry_run | `fail_selected_check` | PARTIAL | PARTIAL | 0 | true | 官方基线缺失 → selected-prediction 校验失败（dry_run PARTIAL） |
| 4 | `official_baseline_missing` | formal  | `fail` | FAIL | FAILED_NO_DELIVERY | 1 | true | 官方基线缺失 → 正式 FAILED_NO_DELIVERY（exit 1） |
| 5 | `router_failure` | dry_run | `continue_warn` | DEGRADED | DEGRADED_DELIVERED | 0 | true | 路由降级 → 带告警继续 |
| 5 | `router_failure` | formal  | `continue_warn` | DEGRADED | DEGRADED_DELIVERED | 0 | true | 正式同理降级继续（由 `--allow-router-fallback` 控制是否允许） |
| 6 | `shadow_failed` | any | `continue_main_chain` | COMPLETE | NORMAL | 0 | true | Shadow 模块失败：主链路继续，Shadow 标记 DEGRADED 且**永不**被选入最终交付 |
| 7 | `postflight_failed` | dry_run | `partial` | PARTIAL | PARTIAL | 0 | true | Postflight 失败 → dry_run PARTIAL |
| 7 | `postflight_failed` | formal  | `fail` | FAIL | FAILED_NO_DELIVERY | 1 | true | Postflight 失败 → 正式 FAILED_NO_DELIVERY（exit 1） |
| 8 | `export_failed` | any | `mark_delivery_failed` | COMPLETE | FAILED_NO_DELIVERY | 1 | true | 导出失败：运行本身可能 PASS，但 delivery_status=FAILED_NO_DELIVERY（exit 1） |
| 9 | `duplicate_run_id` | any | `upsert_or_new_run_id` | (沿用) | (沿用) | 0 | true | 重复 run_id → upsert / 生成新 run_id，不产生重复预测行 |
| 10 | `existing_target_date_run` | dry_run | `new_run_id_allowed` | (沿用) | (沿用) | 0 | true | dry_run 允许新 run_id 覆盖已有 target_date |
| 10 | `existing_target_date_run` | formal  | `acquire_lock_or_fail` | (沿用) | (沿用) | (0/1) | true | 正式需先获取锁，失败则 FAIL |

---

## 关键不变量 (Invariants)

1. **正式链路失败一律 exit 1**：任何导致 `FAILED_NO_DELIVERY` 的正式分支，进程退出码必须为 `1`，CI / 调度器据此判定失败。
2. **dry_run 永不阻断退出**：dry_run 分支退出码恒为 `0`（PARTIAL / DEGRADED 仍算成功），仅用于演练与告警，不影响交付判定。
3. **MySQL 不可用时，dry_run 退文件、formal 退场**：`evaluate_db_failure("formal")` 直接 `FAIL`；`dry_run` 退回 `FilePredictionStore`（`db_enabled=false`），保证演练可离线进行。
4. **Shadow 永不污染正式交付**：`shadow_failed` 仅标记 Shadow DEGRADED，主链路照常继续，最终导出只读 `selected-final`，绝不混入 shadow 价格。
5. **危险动作需显式确认**：`export-submission` / `run-formal` 仅在 `confirm=true` 且 `reason` 非空时执行（见 `docs/OPS_CONSOLE_SAFETY.md` 与 `backend/app/security.py`）。

---

## 编排器接线 (Orchestrator wiring)

`pipelines/full_chain_orchestrator.py`：
- 顶部 `from common.fallback_policy import evaluate_db_failure`。
- 当 `mode == "formal"` 且 `db_mgr is None`（MySQL 不可用）时，提前返回 `evaluate_db_failure("formal")` 决策（`FAIL`, `FAILED_NO_DELIVERY`, exit 1），**绝不**静默降级到文件存储。
- dry_run 在 MySQL 不可用时调用 `FilePredictionStore(base_dir=outputs/prediction_store)`，保证演练产物可落盘、可被 `db_exporter` 后续读取。

测试覆盖见 `tests/test_fallback_policy.py`（覆盖上述全部求值器的 dry_run / formal 行为）。

---

## 退出码映射 (map_to_exit_code)

```python
from common.fallback_policy import map_to_exit_code, evaluate_db_failure
decision = evaluate_db_failure("formal")
raise SystemExit(map_to_exit_code(decision))  # -> exit 1
```

任何调用方应以 `map_to_exit_code(decision)` 作为编排器的进程退出码，保证矩阵语义在 CLI / 调度器中一致生效。
