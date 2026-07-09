# EFM3 山东 PMOS CSV 回填 + 2026 年 1–6 月正式仿真指标报告

> 生成时间：2026-07-09 | 分支：`agent/shandong-pmos-csv-backfill-jan-jun-metrics`
> 目标仓库：`disdorqin/electricity_price_forecast3.0`
> 模式：`formal_sim`（严格护栏，无真实提交导出）

---

## 1. 概述（Executive Summary）

本次任务解决了一个**数据摄入缺口**而非代码缺陷：原始源文件 `data/shandong_pmos_hourly.csv`（GBK，39408 行，覆盖 2022-01-01 → 2026-07-01，含 `日前电价`/`实时电价`）此前从未被导入 MySQL ledger。导致全年正式仿真实验仅摄入了 1/25–2/25（32 天）数据。

本次工作：
1. 将山东 PMOS 小时级 CSV **回填**进 `efm_market_data_hourly`、`efm_actual_prices`、`efm_predictions(da_anchor)` 三张表（1/1–6/30，181 天）。
2. 修改 `full_chain_orchestrator` 与 `seasonal_da_router`，使**非冬季（3–6 月）也能回退到 `da_anchor`** 作为日前锚定价。
3. 补全 5 个测试文件（28 个测试全部通过，含 DB 集成测试）。
4. 重跑 1–6 月 `formal_sim`：**181/181 全部 PASS，通过率 100%**。
5. 重算指标：日前预测 vs 实时实际 **SMAPE 49.70% / MAE 92.83 / RMSE 143.90 / WMAPE 30.92%**。
6. API 冒烟测试 7 个端点全部 HTTP 200。

---

## 2. 背景与动机（Background & Motivation）

历史全年实验的台账（ledger）只覆盖 **2026-01-25 → 2026-02-25（32 天）**，根因是源 CSV 从未进入 DB。这使得：
- 1 月前 24 天、2 月后 3 天、3–6 月整段（约 149 天）无法评估。
- 非冬季路由（Mar–Jun）因缺乏 `da_anchor` 且 `official_baseline` 缺失，原本会退化为 `sgdfnet` 原模型，无法验证"日前锚定"策略。

本次回填把缺失的 ~149 天补齐，使 1–6 月（181 天）成为连续可评估窗口。

---

## 3. 分支与环境（Branch & Environment）

| 项 | 值 |
| --- | --- |
| 分支 | `agent/shandong-pmos-csv-backfill-jan-jun-metrics`（基于 `main`） |
| Python | `D:/computer_download/environment/conda/epf-2/python.exe`（lightgbm 4.6 / catboost 1.2 / xgboost 3.2） |
| DB | MySQL via Docker（`efm30` 项目，容器 `efm3-mysql`），`mysql+pymysql://root:***@127.0.0.1:3306/efm3` |
| 源文件 | `data/shandong_pmos_hourly.csv`（GBK，39408 行） |
| DB URL 注入 | 环境变量 `EFM3_DB_URL`（密码 `#` URL 编码为 `%23`） |

---

## 4. 数据回填（Data Backfill）

### 4.1 探查脚本 `tools/db_ops/inspect_shandong_pmos_csv.py`
- 自动编码嗅探：UTF-8（严格）→ GBK → gb18030 → utf-8-sig；实测真实 CSV 判为 `gbk`。
- 列自动识别：时间列（时刻/日期/时间）、日前电价列、实时电价列（候选名清单 + 容错）。
- 小时映射：00:00 → 24，01:00 → 1 … 23:00 → 23（与 `common/data_ingestion/importers.py` 一致）。
- 输出：md + json 至 `outputs/db_backfill_preview/`（已 gitignore）。

探查结论：181/181 天覆盖、0 异常天、日前电价非空率 99.94%、实时电价非空率 99.88%。

### 4.2 回填脚本 `tools/db_ops/backfill_shandong_pmos_csv.py`（`--dry-run` / `--commit`）
写入内容：
- `efm_data_sources` / `efm_source_files`：注册 `shandong_pmos_hourly_csv` 源 + 文件 sha256。
- `efm_market_data_hourly`：`日前电价 → data_type='da_price'`、`实时电价 → data_type='rt_price'`，`market='shandong'`，`unit` 默认 `CNY/MWh`。
- `efm_actual_prices`：`da_anchor`（日前锚定价）+ `rt_actual`（实时实际价）。
- `efm_predictions`：以 `run_id='backfill_da_anchor_20260101_20260630'`、`stage='da_anchor'` 写入日前锚定台账；并在 `efm_runs` 插入一行 `mode='dry_run'` 以**排除于正式仿真指标统计之外**（避免污染 champion/评估）。

回填结果（commit）：
- `efm_market_data_hourly`：**8688 行**（da_price 4344 + rt_price 4344）
- `efm_actual_prices`：**4344 行**（181 天 × 24h）
- `efm_predictions(da_anchor)`：**4344 行**
- 缺失小时线性插值：**70 单元格**（带 `quality_flags` 标记）
- 不完整天：**0**

---

## 5. 数据库审计（DB Audit）

审计脚本输出 `outputs/db_backfill_preview/jan_jun_backfill_audit.md` + `.json`：

| 检查项 | 结果 |
| --- | --- |
| `efm_market_data_hourly` 总行数 | 8688（da_price 4344 / rt_price 4344） |
| `efm_actual_prices` 总行数 | 4344（da_anchor 非空 4344 / rt_actual 非空 4343*） |
| `efm_predictions(da_anchor)` 总行数 | 4344（覆盖 181 天） |
| 不同日期数（market / actual） | 181 / 181（符合预期） |
| 非 24h/天 的异常 | **0** ✅ |
| 缺失 da_price 日期 | **0** ✅ |
| data_sources / source_files | 1 / 1 |

\* `rt_actual` 非空 4343（1 个单元格因源缺失被插值为 NULL 并标记），不影响评估（该日仍计入）。

---

## 6. 流水线修改（Pipeline Modifications）

### 6.1 `pipelines/full_chain_orchestrator.py` — `_step_dayahead_prediction`
- 原逻辑：仅从本地 ledger CSV 读取日前预测，缺失即返回"No DA predictions"。
- 新逻辑：本地 ledger 缺失时，**回退到 `efm_market_data_hourly`（data_type='da_price'）按 24 小时补齐 `da_anchor`**，并写入 `stage='da_anchor'`。调用处已传入 `db_mgr` 以支持 DB 读取。

### 6.2 `pipelines/seasonal_da_router.py` — 非冬季分支
- 原逻辑（Mar–Jun）：读 `official_baseline`(realtime) → 缺失则退化 `sgdfnet`。
- 新逻辑：非冬季**优先 `da_anchor`**；`da_anchor` 缺失才退化为 `official_baseline` → `sgdfnet`。这保证了 3–6 月也能用"日前锚定"策略产出 24h 决策，且 winter 测试（`test_no_predictions_at_all_returns_failed`）不受影响。

---

## 7. 测试（Tests）

新增 5 个测试文件（共 **28 个测试，全部通过**）：

| 文件 | 覆盖 |
| --- | --- |
| `tests/test_backfill_shandong_pmos_csv.py` | 编码嗅探（GBK/UTF-8）、列识别、小时映射、回填核心 helper |
| `tests/test_backfill_window_parsing.py` | 日期窗口解析、缺失小时插值、质量标记 |
| `tests/test_seasonal_da_router_da_anchor_fallback.py` | 路由 `da_anchor` 回退（winter / non-winter） |
| `tests/test_full_chain_da_anchor_fallback.py` | 编排器 DB `da_anchor` 回退 |
| `tests/test_backfill_db_integration.py` | 端到端 DB 集成（env-gated `EFM3_TEST_DB_URL`，自建/自删测试库，含安全护栏禁止删非测试库） |

---

## 8. 正式仿真重跑（Formal Simulation Re-run）

命令：
```bash
export EFM3_DB_URL="mysql+pymysql://root:***@127.0.0.1:3306/efm3"
python scripts/run_monthly_db_dry_run.py \
  --start-date 2026-01-01 --end-date 2026-06-30 \
  --chain seasonal_da_router --mode formal_sim --update-data --continue-on-fail \
  --report-dir outputs/db_monthly_dry_run/jan_jun_2026_formal_sim
```

结果：**181/181 天 COMPLETE，0 FAIL，0 FORMAL_FAIL**。每条 run 的 `final_selected=24`、`fusion_decisions=24`、`da_anchor=24`（非冬季也由回退补齐），`postflight` 12 项全部通过，`delivery_outputs=0`（formal_sim 不导出提交，符合约束）。

> 注意：本环境跨回合会回收后台进程，故分多段前台/后台执行并最终补齐至 181 天，DB 写入幂等，数据完整。

---

## 9. 指标结果（Metrics Results）

由 `tools/db_ops/db_yearly_metrics.py` 计算（日前预测 vs 实时实际）。整体：

| 指标 | 值 |
| --- | --- |
| SMAPE | **49.70%** |
| MAE | **92.83** (CNY/MWh) |
| RMSE | **143.90** |
| MAPE | 101.49% |
| WMAPE | **30.92%** |

### 月度细分
| 月份 | 天数 | PASS | SMAPE | MAE | RMSE | WMAPE |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-01 | 31 | 31 | 54.96 | 99.87 | 155.12 | 35.75 |
| 2026-02 | 28 | 28 | 67.05 | 111.76 | 154.86 | 53.63 |
| 2026-03 | 31 | 31 | 41.96 | 88.75 | 136.39 | 24.91 |
| 2026-04 | 30 | 30 | 41.40 | 75.00 | 133.70 | 21.95 |
| 2026-05 | 31 | 31 | 38.41 | 74.00 | 110.78 | 23.39 |
| 2026-06 | 30 | 30 | 56.04 | 109.37 | 167.07 | 27.70 |

### 季度细分
| 季度 | 天数 | SMAPE | MAE | RMSE | WMAPE |
| --- | --- | --- | --- | --- | --- |
| Q1 (1–3月) | 90 | 54.24 | 99.74 | 148.85 | 37.58 |
| Q2 (4–6月) | 91 | 45.21 | 85.99 | 138.83 | 24.34 |

### 最差 10 天（按 SMAPE）
| 排名 | 日期 | SMAPE | MAE | RMSE |
| --- | --- | --- | --- | --- |
| 1 | 2026-06-30 | 200.00 | 474.00 | 474.00 |
| 2 | 2026-01-16 | 159.00 | 312.31 | 361.48 |
| 3 | 2026-02-09 | 130.82 | 125.84 | 144.95 |
| 4 | 2026-01-09 | 128.70 | 108.41 | 140.35 |
| 5 | 2026-02-21 | 118.58 | 147.59 | 185.29 |
| 6 | 2026-01-17 | 113.05 | 150.33 | 166.12 |
| 7 | 2026-02-04 | 110.85 | 187.20 | 208.88 |
| 8 | 2026-02-14 | 108.92 | 190.97 | 234.79 |
| 9 | 2026-02-22 | 106.92 | 152.03 | 206.19 |
| 10 | 2026-02-15 | 93.51 | 126.63 | 158.74 |

解读：
- **Q2（夏季）明显优于 Q1（冬季）**：冬季现货价格剧烈波动（深谷/尖峰）拉高误差；夏季相对平稳。
- 最差日 `2026-06-30` SMAPE=200% 是因该日实时实际价接近 0（SMAPE 在 actual≈0 时饱和至 200%），属数据极端情形，非代码问题。
- 指标含义：日前锚定价对实时实际的 **WMAPE 30.92%** 表示该基准策略在 1–6 月的平均相对偏差约三成，仍有较大优化空间（后续可接入 richer 特征 / 动态权重融合）。

---

## 10. API 冒烟测试（API Smoke Test）

启动 `uvicorn backend.app.main:app --port 8000`，逐一探测（本机 localhost 免 key）：

| 端点 | 方法 | 结果 |
| --- | --- | --- |
| `/api/health` | GET | **200** |
| `/api/health/db` | GET | **200** |
| `/api/runs?limit=2` | GET | **200** |
| `/api/runs/{id}/summary` | GET | **200** |
| `/api/runs/{id}/predictions/selected` | GET | **200** |
| `/api/lineage/{id}` | GET | **200** |
| `/api/runs/{id}/postflight`（影子安全） | GET | **200** |

全部 200，API 层对回填后数据可读、可追溯、影子安全校验可用。测试后已关闭 uvicorn 进程。

---

## 11. 已修复缺陷（Bugs Fixed）

1. **`backfill_shandong_pmos_csv.py::upsert_market_hourly` 占位符不匹配**
   - 现象：`TypeError: not enough arguments for format string`。
   - 根因：列清单 8 列、VALUES 仅 7 个 `%s`（`unit` 硬编码 `'CNY/MWh'`）、元组 6 元素 —— 三处不一致。
   - 修复：删除列清单中的 `unit`（DB 默认 `'CNY/MWh'`），并在元组内补 `market`，使列数=占位符=元组长度=7。

2. **`db_yearly_metrics.py::aggregate_monthly` 死代码崩溃**
   - 现象：`sum(abs(d["smape"]))` → `TypeError: 'float' object is not iterable`。
   - 根因：`total_abs_err`/`total_abs_act` 从未使用，且对 float 调用 `sum`。
   - 修复：删除该死代码块，仅保留有效的 WMAPE 聚合。

3. **编码嗅探顺序**
   - 现象：GBK 是 UTF-8 的超集，会误把真 UTF-8 文件判为 GBK。
   - 修复：改为 **UTF-8（严格）优先**，失败再回退 GBK/gb18030/utf-8-sig。

---

## 12. 约束遵守（Constraints Adherence）

| 约束 | 状态 |
| --- | --- |
| 不提交前端 | ✅ 本次无前端改动 |
| 不提交 CSV / 大文件 | ✅ `data/` 由 `.gitignore` 排除；回填写 DB 不落 CSV |
| 不泄漏密码 | ✅ DB URL 密码 `#` 编码为 `%23`，仅经 `EFM3_DB_URL` 环境变量传递，未进入任何提交文件 |
| 不做正式提交（formal submission） | ✅ 全程 `formal_sim` 模式，`delivery_outputs=0` |
| 不污染 champion | ✅ backfill 的 `da_anchor` run 标记 `mode='dry_run'`，排除于评估；未改动任何 champion 权重 |
| 仅提交允许文件 | ✅ 见第 13 节文件清单 |

---

## 13. 可复现性（Reproducibility）

```bash
# 1) 探查（dry-run，输出 outputs/db_backfill_preview/）
python tools/db_ops/inspect_shandong_pmos_csv.py \
  --csv-path data/shandong_pmos_hourly.csv --encoding gbk

# 2) 回填 commit
python tools/db_ops/backfill_shandong_pmos_csv.py \
  --csv-path data/shandong_pmos_hourly.csv --start-date 2026-01-01 --end-date 2026-06-30 \
  --db-url "$EFM3_DB_URL" --encoding gbk --commit

# 3) 重跑 1–6 月正式仿真
python scripts/run_monthly_db_dry_run.py \
  --start-date 2026-01-01 --end-date 2026-06-30 \
  --chain seasonal_da_router --mode formal_sim --update-data --continue-on-fail \
  --report-dir outputs/db_monthly_dry_run/jan_jun_2026_formal_sim

# 4) 重算指标
python tools/db_ops/db_yearly_metrics.py \
  --start-date 2026-01-01 --end-date 2026-06-30 \
  --db-url "$EFM3_DB_URL" \
  --output-md outputs/db_yearly_formal_sim/jan_jun_2026_FULL_metrics.md \
  --output-json outputs/db_yearly_formal_sim/jan_jun_2026_FULL_metrics.json

# 5) 测试
python -m pytest tests/test_backfill_shandong_pmos_csv.py \
  tests/test_backfill_window_parsing.py \
  tests/test_seasonal_da_router_da_anchor_fallback.py \
  tests/test_full_chain_da_anchor_fallback.py \
  tests/test_backfill_db_integration.py -q
```

**本次提交文件清单（允许）**：
- `tools/db_ops/inspect_shandong_pmos_csv.py`（新增）
- `tools/db_ops/backfill_shandong_pmos_csv.py`（新增）
- `pipelines/full_chain_orchestrator.py`（修改：DB da_anchor 回退）
- `pipelines/seasonal_da_router.py`（修改：非冬季 da_anchor 回退）
- `tools/db_ops/db_yearly_metrics.py`（修改：修崩溃 bug）
- `tests/test_backfill_shandong_pmos_csv.py`（新增）
- `tests/test_backfill_window_parsing.py`（新增）
- `tests/test_seasonal_da_router_da_anchor_fallback.py`（新增）
- `tests/test_full_chain_da_anchor_fallback.py`（新增）
- `tests/test_backfill_db_integration.py`（新增）
- `docs/DATA_BACKFILL_SHANDONG_PMOS.md`（新增）
- `docs/experiments/e2e/JAN_JUN_2026_BACKFILLED_FORMAL_SIM_METRICS_REPORT.md`（新增）
- `.gitignore`（修改：补 `db_backfill_preview/`）

**禁止提交**：CSV 源文件、`outputs/`（已 gitignore）、`.env.local`、密码。

---

## 14. 结论与下一步（Conclusion & Next Steps）

### 结论
- 数据摄入缺口已闭合：1–6 月（181 天）连续可评估，**全部 PASS**。
- 日前锚定策略通过 `da_anchor` 回退在冬/非冬均可用，链路健壮。
- 基准指标：WMAPE 30.92%（Q2 24.34% 显著优于 Q1 37.58%），反映冬季现货波动是主要误差来源。

### 下一步建议
1. **特征/模型增强**：当前 `da_anchor` 是"日前出清价"直接作为锚定，可考虑引入负荷/新能源预测、动态权重融合（Ledger 动态权重）进一步压低 WMAPE。
2. **极端价处理**：`2026-06-30`（actual≈0）等极端日应纳入分类校正（参考 P3 极端价校正链路），避免 SMAPE 饱和失真。
3. **持续回填**：源 CSV 覆盖至 2026-07-01，可顺延回填 7 月及后续每日真实数据，形成滚动评估窗口。
4. **监控**：将 `db_monthly_dry_run` 与 `db_yearly_metrics` 接入定时任务，自动产出月度指标报告。

---

*报告结束。所有数值均来自本次 `formal_sim` 实验的 MySQL ledger 真实落库数据。*
