# EFM3.0 剩余任务详细执行提示词（续做手册）

> 本文件是给"继续执行本项目的下一段工作（人或 Agent）"的**自包含提示词**。
> 读完即可严格照做，无需再翻历史上下文。所有路径均相对于仓库根
> `D:/作业/大创_挑战杯_互联网/大学生创新创业计划/大创实现/其他资料/efm3.0`（下称 `REPO`）。

---

## 0. 背景与整体现状

项目：EFM3.0 山东电价预测（日前 DA + 实时 RT），对标 2.5 仓库。整条链路为
`数据同步 → 日前预测(P1引擎) → 实时预测(SGDFNet/TimesFM) → 生产电路(修补→融合BGEW→负电价分类器→交付) → 导出`。

**本轮会话之前已完成（不要重复做）：**
- **3NF 数据库重建**：`db/schema_3nf.sql`（16 张维度表 + 23 张核心表 + 1 视图 `v_efm_shadow_safety`）；`db/migrate_to_3nf.py` 已修复 SQL 注释内分号分割 bug，跑通后"全部清空重建"（这同时满足"清理被污染预测结果"的需求）。
- **数据访问层全面 3NF 化**：`common/db/dimensions.py`（name→id 解析）、`common/db/repositories.py`、`pipelines/production_circuit/step_recorder.py`、8 条生产电路链（`dayahead/rt/repair/fusion/classifier/separator/delivery/negative_price_fixer`）、4 个后端 service（`lineage/prediction/run/report`）、`common/db/schema.py` 已重指向 `schema_3nf.sql`。
- **关键不变量**：`efm_predictions` 及所有 run-children **不再存 `target_date`**（改 JOIN `efm_runs` 派生）；自由文本域（stage/model/policy/check/…）一律外键到 `efm_dim_*`；`task/status/severity/metric_scope/mode` 保留 ENUM。
- **smoke 测试通过**：`tests/test_3nf_ledger_smoke.py` 验证了维度自增、读写、电路子表、FK 完整性与 3NF 不变量。

**本会话刚完成（代码已写，待验证）：**
- 任务 #105 代码：`export_local.py`（仓库根，与 `main.py` 同级）＋ `main.py` 接入。已 `py_compile` 通过；文件夹逻辑用占位日期 `2099-12-31` 功能验证（建目录/拷贝 actual/skip 报已有/--force 覆盖均正确）。测试脚本本身有 `WindowsPath % str` 的小 bug（已不影响产品代码）。

---

## 1. 剩余任务清单（共 4 项）

| 编号 | 任务 | 状态 |
|------|------|------|
| #105 | 本地优先日期文件夹 — **端到端验证** | 代码已完成，未跑真实日 |
| #106 | 数据库连接加固 + 单一配置入口 + 启动健康检查门禁 | 未开始 |
| #107 | 清理废弃脚本（移到 `_archive/`，删 `*.log`/`catboost_info`），端到端跑通，本地提交（不 push） | 未开始 |
| #108 | 把 `main.py` 全链路接入现有 FastAPI 后端（触发预测 + 返回状态/路径） | 未开始 |
| #109 | **DA 感知选择器改造**：逐小时混用 → **整日二选一**（非冬季高置信才切 SGDFNet/TimesFM，否则整日用 DA） | 未开始（设计已定，见下文 + 附录 A.3） |

**建议顺序**：#105 验证 → #106 → #108 → #109（选择器纯逻辑改造，不影响表结构）→ #107（清理放最后，避免误删在用文件）。

---

## 2. 每条任务的详细执行提示

### 任务 #105：本地优先日期文件夹 — 端到端验证

**目标**：真实跑一次未来日（如 `2026-07-11`），确认 `outputs/<date>/` 下 9 个 CSV 全部正确生成、且二次无 `--force` 被 skip。

**现状（已实现）**：
- `export_local.py`：
  - `prepare_dated_folder(target_date, force=False)` → 建 `outputs/<date>/` + `actual/`、`predict/dayahead/`、`predict/realtime/`；拷贝 `data/shandong_pmos_hourly.csv` → `actual/`，并抽取 `<date>.csv`（该日 24 行，若数据含该日）；**唯一性**：已存在且非 `--force` → 返回 `skip=True`，`run_full` 直接中止并报告已有文件；`--force` → `rmtree` 重建。
  - `write_predict_csv(root, task, models_preds)` → `predict.csv`（每模型一列）。
  - `write_circuit_local_outputs(root, target_date, run_id, db_url, da_weights, rt_weights)` → 写 `weight.csv`（DA+RT）；从 3NF ledger 回读 `fuse.csv`（阶段 `dayahead_fused` / `realtime_fused`）、`final.csv`（`efm_task_finals`）、`module_repair.csv`（`efm_repair_decisions` 中 `repair_stage='module_repair'`，兜底读 `realtime_module_repaired` 阶段价格）。
- `main.py` 已接入：`import` 三个函数；`build_parser` 加 `--force`；`run_full` 开头调 `prepare_dated_folder`；`stage_da_predict`/`stage_rt_predict` 写 `predict.csv`；`stage_circuit` 末尾调 `write_circuit_local_outputs`。

**具体步骤**：
1. 确认 MySQL 容器在跑：`docker ps` 看 `efm3-mysql` 状态 `healthy`。
2. 用 conda epf-2 跑：
   ```
   D:/computer_download/environment/conda/epf-2/python.exe main.py 2026-07-11 --force
   ```
   （未来日；P1 引擎依赖 `models` 仓，SGDFNet/TimesFM 为子进程，可能较慢或部分失败，属正常。）
3. 检查 `outputs/2026-07-11/`：
   - `actual/shandong_pmos_hourly.csv` + `actual/2026-07-11.csv`
   - `predict/dayahead/{predict,weight,fuse,final}.csv`
   - `predict/realtime/{predict,weight,fuse,final,module_repair}.csv`
4. 校验每个 CSV：存在、24 行、列正确、非空（fuse/final/module_repair 依赖电路成功写库；若电路失败这些 CSV 可能只有表头——代码已做空保护）。
5. 再跑一次 `main.py 2026-07-11`（**不带** `--force`）→ 应 `skip` 并报已有文件，不覆盖。
6. 清理测试文件夹 `outputs/2026-07-11/`。

**已知坑**：
- 默认 `python` 缺 pymysql，必须用 conda epf-2。
- `outputs/runs/<date>/`（旧结构，存 `submission_ready.csv`）与新的 `outputs/<date>/` 不冲突；`stage_export` 仍写旧路径，可在 #107 视情况统一。
- 真实 run 依赖外部模型仓与子进程，失败要有容错（代码已对本地写做 try/except）。

**验证标准**：9 个 CSV 全部存在且 24 行；二次无 `--force` 被 skip；`main()` 打印 `⚠ outputs/<date> already exists`。

---

### 任务 #106：数据库连接加固 + 单一配置入口 + 健康检查门禁

**目标**：DB 配置只一处来源；管道启动先探活，连不上立即清晰报错，而非跑到一半崩。

**现状**：
- `main.py` 里 `DB_URL` 硬编码了带密码字面量：`mysql+pymysql://root:Zlt20060313%%23@127.0.0.1:3306/efm3`（`%23` 转义 `#`），可被 env `EFM3_DB_URL` 覆盖。
- 真正连接工厂：`common/db/connection.py` 的 `DbConnectionManager`（pymysql）。
- 后端 pydantic Settings 自动读 `.env.local`（已 gitignore，**不得提交**）。

**具体步骤**：
1. **单一配置**：在 `common/db/connection.py`（或新建 `common/config.py`）导出：
   ```python
   def get_db_url() -> str:
       # 1) 显式 env 2) .env.local (pydantic Settings) 3) 本地默认
       ...
   ```
   **删除 `main.py` 里的明文密码常量**，所有 `DbConnectionManager(db_url=DB_URL)` 改为 `DbConnectionManager(db_url=get_db_url())`；4 个后端 service 同样改用 `get_db_url()`。
2. **健康检查门禁** `db_health_check(db_url) -> bool`：连上执行 `SELECT 1` 返回 True。在 `run_full` 的**第一步**（建议 `prepare_dated_folder` 之后、`stage_cleanup` 之前）调用；失败则 `return {"db_error": True, "message": "DB unreachable: ..."}` 并打清晰 ERROR 日志，**不进后续阶段**。
3. 后端启动（`backend/app/main.py` 的 lifespan）也调一次 health check（失败记 warning，是否阻塞由 `ops_enabled` 决定）。
4. 验证：把 `EFM3_DB_URL` 指向错误端口 → 管道**首步即报 DB 不可达并退出**（非中途崩）。

**已知坑**：
- 密码含 `#` → URL 用 `%23`；`.env.local` 里同样 `%23`。
- 提交前 `git grep -n "Zlt20060313"` 自查，绝不能把明文密码或 `.env.local` 提交进去。
- 确认 `DbConnectionManager` 在其他模块的用法一致（重连/池化）。

**验证标准**：改坏 `EFM3_DB_URL` → 管道首步报错退出；恢复正常 URL → 照常跑通。

---

### 任务 #107：清理废弃脚本 + 端到端 + 本地提交

**目标**：删掉 2.5 风格旧 ledger 引用与临时日志，仓库干净，再端到端验证并提交（**不 push**）。

**现状**：
- 大量 `tools/*.py`、`tests/*.py` 仍引用旧列：`p.stage=`（应为 JOIN `efm_dim_stage`）、`efm_predictions.target_date`（3NF 已移除）、`check_name` 直接列（应为 JOIN `efm_dim_check`）、`task='final_selected'`（enum 无此值）、`efm_runs` 旧结构等。这些属废弃 2.5 风格 ledger。
- 根/子目录散落 `*.log`、`catboost_info/`、`__pycache__/`。

**具体步骤**：
1. **扫描**：用 Grep 搜关键字 `.stage=`、`final_selected`、`target_date`（出现在 predictions 上下文）、`check_name`（误用为列）、`catboost_info`、`.log"`。列出命中文件。
2. **归档**：确认废弃的脚本移到 `tools/_archive/`、`tests/_archive/`（保留历史不删内容）；确无用再彻底删。**切勿动**已 3NF 化的文件：`repositories.py`、`step_recorder.py`、8 条链、`lineage/prediction/run/report` 四个 service、`migrate_to_3nf.py`、`export_local.py`、`main.py`、`schema_3nf.sql`。
3. **清理**：删 `*.log`、`catboost_info/`、`__pycache__/`。
4. **端到端**：跑 #105 验证（未来日），确认清理后无 `ImportError` / 缺模块 / 旧列报错。
5. **本地提交**（**严禁 `git add -A`**，显式 add；**不 push**）：
   ```
   git add export_local.py main.py db/ common/ pipelines/ backend/ tests/<保留的> tools/<保留的> docs/REMAINING_TASKS_PROMPT.md
   git commit -m "feat: 本地优先日期输出文件夹 + 3NF 数据访问层 + 清理废弃脚本"
   git diff --cached --name-only   # 复核，确认无 .env.local / 日志
   ```
   远端是 `price_forecast3.0`；**本次不 push**。若日后要 push：走代理
   `git -c http.proxy=http://127.0.0.1:7890 -c https.proxy=http://127.0.0.1:7890 push origin <branch>`。
   `disdorqin/electricity_forecast_model2.5` 是**只读源，绝不 push/写回**。

**已知坑**：
- 别误删仍在用的脚本：`tools/ingest_model_predictions.py`、`tools/_bgew_weights.py` 是 3NF 后仍用到的。
- 工作区常驻无关未跟踪文件（`frontend/`、`daemon/`、`exports/`），必须显式 add。
- 长任务单回合前台跑完，或 `run_in_background` 全程不离开本回合（跨回合后台进程会被回收）。

**验证标准**：Grep 旧列关键字归零（仅剩 `_archive/` 内）；端到端跑通；`git status` 干净、无 `.env.local`/日志。

---

### 任务 #108：接入 FastAPI 后端（触发预测 + 返回状态/路径）

**目标**：前端/调用方通过 API 触发 `run_full` 并拿到状态与输出路径，完成"封装方便前端调用"。

**现状**：
- 后端 `backend/app/` 已有 routers：`runs/predictions/ops/postflight/datasets/lineage/reports/health`；services 已查 3NF ledger。
- 缺口：没有"触发一次预测"的端点；pipeline 在 conda epf-2 环境（含 lightgbm/catboost/pymysql），后端 venv 可能缺这些依赖。

**具体步骤**：
1. **封装服务** `backend/app/services/forecast_service.py`：
   - `trigger_forecast(target_date, mode="formal_sim", force=False) -> job_id`：用 **subprocess** 调
     `D:/computer_download/environment/conda/epf-2/python.exe main.py <date> [--mode x] [--force]`，
     stdout/stderr 重定向到 `outputs/<date>/run.log`，返回 `job_id`（如 `efm3_<date>_<ts>`）。**用 subprocess 隔离环境最稳**，不要 in-process 直接 import 跑。
   - `get_forecast_status(target_date)`：读 `efm_runs`（`status`/`delivery_status`）或 job 日志尾行。
   - `get_forecast_outputs(target_date)`：返回 `outputs/<date>/` 文件树 + delivery CSV 路径 + 各 CSV 相对路径（与 #105 结构一致）。
2. **端点**（加到 `backend/app/routers/runs.py` 或新建 `forecast.py`）：
   - `POST /api/forecast/trigger`  body `{target_date, mode?, force?}` → `{job_id, status}`
   - `GET  /api/forecast/<target_date>/status`  → `{status, delivery_status, checks_pass}`
   - `GET  /api/forecast/<target_date>/outputs` → `{tree, delivery_path, csv_paths}`
3. **注册** 到 `backend/app/main.py` 的 router 列表；`/health` 确认 DB。
4. **验证**：起 `uvicorn`；`curl` 触发一个未来日（或 dry_run）；轮询 status→`COMPLETE`；`outputs` 端点返回 9 个 CSV 路径 + delivery 路径。

**已知坑**：
- 不要 in-process import main 跑（依赖重、环境不同）；subprocess 隔离更稳。
- 长任务：端点应异步返回 `job_id`，状态靠轮询/DB，别阻塞 HTTP。
- 路径返回用 repo 相对或绝对，前端好展示；与 `outputs/<date>/` 结构对齐。
- 本环境跨回合后台 subprocess 会被回收 → 验证时若走本会话，用 `run_in_background` 且全程不离开；生产由真实服务常驻。

**验证标准**：curl 触发→拿到 job_id；轮询 status→`COMPLETE`；outputs 端点返回 9 个 CSV 路径 + delivery 路径。

---

### 任务 #109：DA 感知选择器改造（逐小时混用 → 整日二选一）

**目标**：把实时 DA 感知选择器（`da_aware_sgdf_selector`）从"每小时独立在 SGDFNet 与 DA 之间挑"改为"**整日二选一**"——要么整日用 DA 作为实时，要么整日用模型（SGDFNet / TimesFM）作为实时。

**用户原文（设计指令）**：
> 改为非冬季高置信才切 SGDFNet、timesfm，就是相当于要不是DA作为实时 要不是timesfm sdg作为实时

**现状（待改）**：
- 当前逻辑在 `pipelines/production_circuit/model_loader.py::derive_da_aware_selector`（**电路 step 8** 用，由 `realtime_chain.py::run_real_time_chain` 调用）。
- `main.py::stage_rt_predict`（lines ~333-349）里**另有一份内联实现**，同样逐小时判断，算完写入 `da_aware_sgdf_selector` 这个候选。两处必须**同时改**，否则不一致。
- 当前逐小时规则：`use_sg = (非冬季) and (da_anchor>0) and (sgdfnet存在) and (|sg-da|/da < SELECTOR_SWITCH_REL_TOL)`，每小时独立选 SGDFNet 或 DA。

**新逻辑（整日二选一）**：
1. **模型值** = 该小时 SGDFNet 与 TimesFM 的**平均**（两者都在则用均值；只有一个在则用那个）。
2. **整日门禁**（全部满足才切到模型）：
   - 非冬季（月份 ∉ `{11,12,1,2}`）；
   - 全天 24 小时 DA anchor 均存在且 > 0；
   - 全天 24 小时模型值均存在；
   - 至少 `>=80%` 的小时满足 `|model - da| / da < SELECTOR_SWITCH_REL_TOL`（10%）——即"高置信一致"。
3. **regime 决策**：
   - 门禁通过 → 整日 24 小时**全部用模型值**（出现在 `predict/realtime/predict.csv` 的 `da_aware_sgdf_selector` 列）。
   - 否则（冬季 / 低置信）→ 整日 24 小时**全部用 DA anchor**。
   - 安全兜底：切模型时某小时缺模型值 → 该小时回退 DA；不切时某小时缺 DA → 该小时回退模型值。

**关键文件**：
- `pipelines/production_circuit/model_loader.py`：`derive_da_aware_selector`（加 `timesfm_by_hour` 入参；抽出 `decide_da_aware_regime(da_map, model_by_hour, month, rel_tol, hour_fraction) -> (switch, per_hour)` 决策函数，两处共用）。常量：`SELECTOR_SWITCH_REL_TOL=0.10`、`SELECTOR_SWITCH_HOUR_FRACTION=0.80`、`WINTER_MONTHS={11,12,1,2}`。
- `pipelines/production_circuit/realtime_chain.py`：`run_real_time_chain` 内除提取 `sgdfnet_by_hour` 外，还要提取 `timesfm_by_hour` 并一起传给 `derive_da_aware_selector`。
- `main.py::stage_rt_predict`：内联块改为用同一个 `decide_da_aware_regime`（DA 用 `flat_da`，模型用 `sg`/`tfm` 平均），保证与电路逻辑一致。
- `configs/candidate_registry/realtime_da_sgdf_selector.yaml`：`selector_policy` 的 `auxiliary_model` 改为 `SGDFNet / TimesFM (per-hour average)`，`rule_summary` 写明"整日二选一、非冬季高置信才切"。

**注意**：选择器只是融合前的**第 3 个候选**（`realtime_raw_model`），最终实时价仍由 BGEW 融合 [sgdfnet, timesfm, da_aware_sgdf_selector] 得出。本任务只改"选择器候选"的产出逻辑（整日 DA 或整日模型），**不动融合本身**。若用户后续要求"最终实时价也必须是纯 DA 或纯模型"，那需要再改 `fusion_chain.py`，不在此任务范围。

**验证标准**：
- 单元验证 `decide_da_aware_regime`：非冬季且模型≈DA → 返回 switch=True、24 小时全为模型值；冬季 → 全 DA；非冬季但模型偏离大（<80% 小时在 10% 内）→ 全 DA；DA 含 0 → 不切。
- 真实跑一次，`predict/realtime/predict.csv` 的 `da_aware_sgdf_selector` 列应**整列同一来源**（全 DA 或全模型），不再出现部分小时 DA、部分小时模型。

---

## 附录 A：理想链路与代码/数据库的映射说明

本附录对应用户提供的两张流程图：

- `日前理想链路_3.0模型.png`：日前（Day-ahead）预测链路
- `实时理想链路_3.0模型.png`：实时（Real-time）预测链路

以下将图中每个节点映射到代码中的 `CircuitStage`/`RepairStage` 枚举、DB 表/字段，以及本地 `outputs/<date>/predict/` 下的 CSV 文件名。后续任务（尤其是 #105 本地 CSV 生成、#108 API 输出）需严格按此映射实现。

### A.1 通用约定

- 所有“自由文本阶段名”在 3NF 数据库中均外键到 `efm_dim_stage.name`；代码中通过 `CircuitStage` 枚举引用，写入前用 `common/db/dimensions.py::resolve_dim_id` 解析为 `stage_id`。
- 权重由 `DailyLedgerGEF`（BGEW）学习器拟合，存储在 `efm_fusion_decisions.candidate_weights`（JSON），`policy_id` 指向 `efm_dim_policy.name='BGEW'`。
- 终选价格写入 `efm_task_finals`（task='dayahead' 或 'realtime'），对应本地 `final.csv`。
- 交付文件仍由 `stage_export` 写入旧路径 `outputs/runs/<date>/delivery/submission_ready.csv`（#107 可决定是否统一）。

### A.2 日前链路（`日前理想链路_3.0模型.png`）

| 图中节点 | 代码枚举/说明 | 写入 DB 的位置 | 对应本地 CSV |
|---|---|---|---|
| `cfg05 · LightGBM` / `xgboost rich` / `catboost rich` | 三个原始候选模型 | `efm_predictions` stage=`dayahead_predicted` | `predict/dayahead/predict.csv`（每模型一列） |
| `修复模块 repair`（no_nan / range / spike 守护） | `CircuitStage.DAYAHEAD_REPAIRED` | `efm_predictions` stage=`dayahead_repaired` | 不单独出 CSV（中间态） |
| `学习器 DailyLedgerGEF (BGEW)` | 权重学习器；`policy='BGEW'` | `efm_fusion_decisions` 的 `candidate_weights` | `predict/dayahead/weight.csv` |
| `融合 fusion` | `CircuitStage.DAYAHEAD_FUSED` | `efm_predictions` stage=`dayahead_fused` | `predict/dayahead/fuse.csv` |
| `负电价分类器（待补）` | 图中标注“待补”；代码中由 `negative_price_fixer.py` 完成 | 输出为 `efm_task_finals` task='dayahead' | `predict/dayahead/final.csv` |
| `日前终选输出 task_final` | `CircuitStage.DAYAHEAD_TASK_FINAL` | `efm_task_finals` task='dayahead' | `predict/dayahead/final.csv`（与上一行同一文件） |

**注意**：日前链路没有单独的 `module_repair.csv`；负电价分类器的结果直接作为 `final.csv`。

### A.3 实时链路（`实时理想链路_3.0模型.png`）

| 图中节点 | 代码枚举/说明 | 写入 DB 的位置 | 对应本地 CSV |
|---|---|---|---|
| `SGDFNet`（在线 · CPU 40s） | 原始候选模型 | `efm_predictions` stage=`realtime_predicted` | `predict/realtime/predict.csv` |
| `TimesFM`（候选 · GPU 12s） | 原始候选模型 | `efm_predictions` stage=`realtime_predicted` | `predict/realtime/predict.csv` |
| `TimeMixer`（离线缓存 · 排除） | 当前未启用 | 不写入 | 不出现在 `predict.csv` |
| `RT916`（未就绪 · 排除） | 当前未启用 | 不写入 | 不出现在 `predict.csv` |
| `修复模块 repair → 学习器权重 → 融合` | `realtime_repaired` → BGEW → `realtime_fused` | `efm_predictions` + `efm_fusion_decisions` | `weight.csv` / `fuse.csv` |
| `学习器 DailyLedgerGEF (BGEW)` | 同日前 | `efm_fusion_decisions` | `predict/realtime/weight.csv` |
| `P3 spike_residual 负价/尖峰校正` | `RepairStage.MODULE_REPAIR` / `CircuitStage.REALTIME_MODULE_REPAIRED` | `efm_repair_decisions`（`repair_stage='module_repair'`）+ `efm_predictions` stage=`realtime_module_repaired` | `predict/realtime/module_repair.csv` |
| `DA 感知选择器`（`da_aware_sgdf_selector`） | 非独立 stage；在 step 8 `run_real_time_chain` 内由 `derive_da_aware_selector` 派生，作为**第 3 个融合候选**写入 `efm_predictions` stage=`realtime_raw_model`（model_name=`da_aware_sgdf_selector`） | 出现在 `predict/realtime/predict.csv` 的「da_aware_sgdf_selector」列（与 SGDFNet/TimesFM 同表多模型） |
| `实时终选输出 task_final` | `CircuitStage.REALTIME_TASK_FINAL` | `efm_task_finals` task='realtime' | `predict/realtime/final.csv` |

**注意**：图中 `P3 spike_residual` 是“shadow-only”，意味着它只在 shadow 模式下生效并写入；生产 formal 模式下 `separator_repaired` 可能直接来自 `realtime_fused`。代码中 `write_circuit_local_outputs` 对 `module_repair.csv` 做了兜底：若 `efm_repair_decisions` 无记录，则读 `efm_predictions` stage=`realtime_module_repaired` 的价格；仍无则写空表头。

**位置偏差（重要）**：两张流程图把「DA 感知选择器」画在「融合 fusion 之后、实时终选之前」，但代码里它其实是**融合之前**的候选生成——在 step 8 `run_real_time_chain` 中由 `derive_da_aware_selector` 派生，与 SGDFNet/TimesFM 一起作为 `realtime_raw_model` 进 `realtime_fusion`（step 10）。图中融合后那个独立节点在代码中并不存在；融合之后真正执行的是 `negative_price_fixer`（step 11 负价/尖峰修整）→ `realtime_classifier`（step 12）→ `realtime_task_final`（step 13）。另外 step 15 的 `separator_repair` 是跨任务融合后的**最终安全修补**（no_nan/range/4*std 守卫），它**不是** DA 感知选择器。DA 感知选择器逻辑：`efm_actual_prices.da_anchor`（未来日 NULL 时兜底读 `efm3_raw_%` 的 `dayahead_raw_model` 的 AVG）作默认；仅当 `非冬季` 且 `da_anchor>0` 且 `sgdfnet 存在` 且 `|sg-da|/da < SELECTOR_SWITCH_REL_TOL`（高置信一致）才切到 SGDFNet。⚠️ **此选择器将在任务 #109 中被改造为「整日二选一」**：非冬季且高置信（≥80% 小时 `|model-da|/da<10%`、全天 DA>0、模型值全天存在）时整日切到 SGDFNet/TimesFM 平均，否则整日用 DA；不再逐小时混合（详见任务 #109）。

### A.4 本地 `outputs/<date>/` 完整文件树

```
outputs/<date>/
├── actual/
│   ├── shandong_pmos_hourly.csv          # 从 data/ 完整拷贝
│   └── <date>.csv                         # 仅该日 24 行（方便当天预测）
└── predict/
    ├── dayahead/
    │   ├── predict.csv                    # 3 个输入模型：cfg05, xgboost_rich, catboost_rich
    │   ├── weight.csv                     # BGEW 权重
    │   ├── fuse.csv                       # dayahead_fused
    │   └── final.csv                      # dayahead_task_final（负电价分类器后）
    └── realtime/
        ├── predict.csv                    # SGDFNet, TimesFM（在线/候选模型）
        ├── weight.csv                     # BGEW 权重
        ├── fuse.csv                       # realtime_fused
        ├── final.csv                      # realtime_task_final（DA 感知选择器后）
        └── module_repair.csv              # P3 spike_residual 修补结果
```

### A.5 为什么“待补/排除”不影响 3NF 与本地输出

- `负电价分类器（待补）`：在 3NF 表中由 `efm_task_finals` 承载，前端/本地都把它视为 `dayahead_task_final`；后续若新增真正的负电价分类器模型，只需在 `negative_price_fixer.py` 中修改计算逻辑，不改变表结构。
- `TimeMixer/RT916（排除）`：未写入 DB，因此不生成 CSV；未来就绪后，只需在 `stage_rt_predict` 中把它们加入 `rt_results` 字典，本地 `predict.csv` 和 DB `realtime_predicted` 会自动多出一列/一行。

---

## 3. 跨任务硬约束（务必遵守）

- **环境**：所有 pipeline/引擎/迁移/测试一律用
  `D:/computer_download/environment/conda/epf-2/python.exe`（默认 python 无 pymysql）。
- **Git**：
  - 只推 `price_forecast3.0`；`disdorqin/electricity_forecast_model2.5` 是**只读源，绝不 push/写回**。
  - push 走代理：`git -c http.proxy=http://127.0.0.1:7890 -c https.proxy=http://127.0.0.1:7890 push origin <branch>`。
  - **严禁 `git add -A`**，必须显式 add；**不提交** `.env.local`、日志、`catboost_info`、`__pycache__`。
- **DB**：MySQL 8.0 in Docker `efm3-mysql`；`efm_predictions` **无 `target_date`**（3NF）；SQL 里 `LIKE` 字面 `%` 必须写 `%%`（pymysql 格式符）；密码 `#`→`%23`。
- **3NF 已定稿**：自由文本域 → id 在 `common/db/dimensions.py` 数据访问边界解析；不要回退到字符串列。
- **自主执行**：长任务连续跑、日志可见；仅遇硬错才停。

---

## 4. 立即可执行的下一步（#105 验证，复制即用）

```powershell
cd D:/作业/大创_挑战杯_互联网/大学生创新创业计划/大创实现/其他资料/efm3.0
docker ps | findstr efm3-mysql          # 确认容器 healthy
D:/computer_download/environment/conda/epf-2/python.exe main.py 2026-07-11 --force
# 检查 outputs/2026-07-11/ 下 9 个 CSV
D:/computer_download/environment/conda/epf-2/python.exe main.py 2026-07-11
# 期望：skip 并报已有文件，不覆盖
```

完成 #105 验证后，依次推进 #106 → #108 → #107，每完成一项更新 `docs/REMAINING_TASKS_PROMPT.md` 顶部状态表与 `.workbuddy/memory/` 日志。
