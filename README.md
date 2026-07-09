# Electricity Forecast Model 3.0 (EFM3)

**山东电力现货价格预测交付链路 3.0** — 基于 2.5 交付链路的全面重构与升级。

---

## 1. 3.0 相比 2.5 的核心改进

| 改进点 | 2.5 | 3.0 |
|--------|-----|-----|
| **日前模型** | 单一 LightGBM 基线 | 3 模型并行：cfg05 (LightGBM rich)、xgboost_rich、catboost_rich，融合策略 |
| **架构** | 顺序 5 阶段（predict→weight→fuse→classifier→output） | 18 步 DAG：日前/实时双链路→修补→融合→分类器→跨任务融合→分离器→交付 |
| **数据库** | CSV + JSON manifest | MySQL Ledger V2（23 表，3NF，完全审计可追溯） |
| **可观测性** | 日志 + manifest | pipeline_steps + run_events + postflight_checks + metric_runs |
| **指标** | 运行后在终端输出 | 持久化到 efm_metric_runs，可跨时段聚合 |
| **数据同步** | sync_data.py | 复用 2.5 同步机制 + MySQL Ledger 回填 |
| **交付校验** | postflight 6 项 | postflight 8/8 项（含 lineage 追溯、shadow 阻断） |
| **生产安全** | 无 | honest_status_contract（每个步骤如实报告 PARTIAL/FAIL） |
| **模型注册** | 无 | efm_model_registry 统一管理模型目录 |
| **融合路径** | 单一融合 | 修补→权重→融合→分类器→task_final 完整链路 |

## 2. 正式链路

```text
数据更新 (data_update/sync_dataset)
    ↓
特征快照 (feature_snapshot)
    ↓
日前子链
  ├─ 模型并行预测 (cfg05 / xgboost_rich / catboost_rich)
  ├─ 模块修补 (module_repair)
  ├─ 融合 (fusion)
  ├─ 分类器调整 (classifier_adjust)
  └─ task_final
    ↓
实时子链
  ├─ SGDFNet / TimesFM / DA-aware 选择器
  ├─ 模块修补
  ├─ 融合
  ├─ 负电价修正
  ├─ 分类器调整
  └─ task_final
    ↓
跨任务融合 (cross_task_fusion)
    ↓
分离器修补 (separator_repair)
    ↓
交付最终 (delivery_final)
    ↓
Postflight 校验 (8/8 checks)
    ↓
指标计算 (metrics)
    ↓
运行结束 (finish_run)
```

### 交付文件

```text
outputs/runs/YYYY-MM-DD/delivery/submission_ready.csv
```

标准列：

```text
business_day, ds, hour_business, period, dayahead_price, realtime_price
```

## 3. 复现指南

### 环境要求

- Python 3.11+（推荐 conda 环境）
- MySQL 8.0（Docker 容器 `efm3-mysql`）
- 依赖：`pip install -r requirements.txt`

### 数据准备

```bash
# 方式一：从 2.5 仓库同步最新 PMOS 数据
cd ../electricity_forecast_model2.5
python sync_data.py --source auto --force-sync

# 方式二：复制已有 CSV
cp ../electricity_forecast_model2.5/data/shandong_pmos_hourly.csv \
   ../electricity_forecast_model2.0_exp/data/
```

### 数据库初始化

```bash
# 启动 MySQL
docker start efm3-mysql

# 初始化 schema
mysql -h 127.0.0.1 -P 3306 -u root -p efm3 < db/schema.sql
mysql -h 127.0.0.1 -P 3306 -u root -p efm3 < db/migrations/005_production_circuit_schema.sql
```

### 模型训练与预测

```bash
# P1 walk-forward（日前模型）
cd ../models
python scripts/run_dayahead_p1_walkforward.py \
    --test-months YYYY-MM \
    --models cfg05,xgboost_rich,catboost_rich \
    --train-window-months 18 \
    --output-root outputs/p1_dayahead/run_name \
    --cpu-only
```

### 导入预测到数据库

```bash
cd ../efm3.0
# 按日期逐个导入
python tools/ingest_model_predictions.py \
    --db-url mysql+pymysql://root:PASS@host:3306/efm3 \
    --task dayahead \
    --model cfg05 \
    --target-date YYYY-MM-DD \
    --csv path/to/predictions.csv
```

### 运行生产电路

```bash
# 单日运行（dry_run 模式，不生成正式交付）
python tools/smoke_pc.py --target-date YYYY-MM-DD

# 批量回测
python tools/backtest_dayahead.py \
    --p1-output ../models/outputs/p1_dayahead/run_name \
    --start YYYY-MM-DD --end YYYY-MM-DD

# 正式陪跑
python pipelines/production_circuit/main.py \
    --target-date YYYY-MM-DD \
    --mode formal_sim
```

### 计算指标

```bash
python tools/_compute_official_metrics.py
```

输出：`outputs/official_metrics_3.0.json`

## 4. 当前效果

### 回测结果（2025-11-01 ~ 2026-06-19，231 天）

| 指标 | 值 |
|------|:----:|
| **sMAPE (floor 50)** | **14.45%** |
| **Accuracy (1-SMAPE)** | **85.55%** |
| MAE | 47.70 CNY/MWh |
| RMSE | 67.28 CNY/MWh |
| WMAPE | 15.71% |
| R² | 0.8821 |
| SCR (价差方向准确率) | 47.86% |
| 度电套利（基础版） | 5.20 元/MWh |

### 分时段

| 时段 | sMAPE | MAE | Accuracy |
|------|:-----:|:---:|:--------:|
| 1_8 (谷) | 13.73% | 44.34 | 86.27% |
| 9_16 (平) | 15.12% | 47.42 | 84.88% |
| 17_24 (峰) | 14.49% | 51.35 | 85.51% |

### 月度分解

| 月份 | 天数 | sMAPE | MaE | Accuracy |
|------|:----:|:-----:|:---:|:--------:|
| 2025-11 | 30 | 15.65% | 54.66 | 84.35% |
| 2025-12 | 31 | 18.11% | 46.74 | 81.89% |
| 2026-01 | 31 | 16.39% | 46.40 | 83.61% |
| 2026-02 | 28 | **11.85%** | 34.11 | 88.15% |
| 2026-03 | 31 | **11.50%** | 40.65 | 88.50% |
| 2026-04 | 30 | 12.67% | 50.18 | 87.33% |
| 2026-05 | 31 | 13.85% | 54.93 | 86.15% |
| 2026-06 | 19 | 16.24% | 58.38 | 83.76% |

### 2026-07-09 正式陪跑结果

| 阶段 | 状态 |
|------|:----:|
| 数据导入 | ✅ 3 模型 × 24h（72 行） |
| 日前链路 | ✅ COMPLETE |
| 实时链路 | ✅ PARTIAL（预期，无 RT 模型输出） |
| 交付 | ✅ 24h delivery_finals |
| Postflight | ✅ 8/8 通过 |

## 5. 与 2.5 对比

| 指标 | 2.5 (生产模型) | 3.0 (我们的模型) |
|------|:--------------:|:----------------:|
| 日前 sMAPE | ~14% | **14.45%** |
| 实时 sMAPE | ~23% | 23.76% (DA_anchor 回退) |
| 架构 | 5 阶段顺序链路 | 18 步 DAG + DB Ledger |
| 审计 | 日志 + manifest | Complete DB lineage |
| 正式陪跑 | 已验证 | ✅ 已验证 |

> 注：3.0 的 14.45% 使用了 **我们自己的 3 个日前模型**（cfg05 / xgboost_rich / catboost_rich），与 2.5 的 LightGBM 基线精度相当，但架构更健壮、可观测性更强。
