# EFM3.0 技术参考文档

> 本地文档，不推送至 GitHub。完整描述项目架构、模块、数据库、部署。

## 目录

1. [项目概述](#1-项目概述)
2. [系统架构](#2-系统架构)
3. [数据库设计](#3-数据库设计)
4. [生产电路详解](#4-生产电路详解)
5. [数据流](#5-数据流)
6. [模型管理](#6-模型管理)
7. [指标计算](#7-指标计算)
8. [部署指南](#8-部署指南)
9. [故障排查](#9-故障排查)

---

## 1. 项目概述

### 1.1 定位

EFM3.0 是山东省电力现货市场价格预测的交付链路。它**不包含模型训练逻辑**，而是将已训练的模型输出接入标准化链路，完成以下工作：

- **数据同步**：从 PMOS 数据源拉取最新电价与负荷数据
- **模型接入**：通过标准接口加载模型预测结果（CSV → DB）
- **生产电路**：18 步 DAG 驱动预测从原始输出到最终交付
- **审计追溯**：每一步写入 MySQL Ledger，全链路可审计
- **指标计算**：按官方公式计算 SMAPE / Accuracy / SCR / 套利

### 1.2 版本

| 组件 | 版本 |
|------|------|
| efm3.0 (本仓库) | v3.0-db-ledger-v2 |
| 模型引擎 | epf-sota-experiment (P1) |
| 数据库 | MySQL 8.0 (Docker) |
| Python | 3.11+ |

### 1.3 仓库拓扑

```
efm3.0/                    ← 本仓库（生产电路 + DB Ledger）
├── pipelines/
│   └── production_circuit/ ← 核心：18 步 DAG 生产电路
├── common/db/              ← DB 连接管理 + Repository 模式
├── tools/                  ← 工具脚本
├── db/                     ← Schema + 迁移
└── docs/                   ← 文档

其他资料/
├── models/                 ← P1 引擎（模型训练 + walk-forward）
├── electricity_forecast_model2.5/  ← 2.5 参考实现（只读）
└── electricity_forecast_model2.0_exp/ ← P3.x 实验仓
```

---

## 2. 系统架构

### 2.1 整体架构

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│  PMOS 数据源  │────▶│  sync_data   │────▶│  MySQL Ledger    │
│ (DB/HTTP)    │     │  (2.5 复用)   │     │  (efm3 DB)      │
└─────────────┘     └──────────────┘     └────────┬────────┘
                                                   │
┌─────────────┐     ┌──────────────┐               │
│  P1 模型引擎  │────▶│  ingest      │──────────────▶│
│ (walkforward)│     │  predictions │               │
└─────────────┘     └──────────────┘               │
                                                   ▼
                                          ┌─────────────────┐
                                          │ production_      │
                                          │ circuit (18步)   │
                                          └────────┬────────┘
                                                   │
                                                   ▼
                                          ┌─────────────────┐
                                          │  delivery_final  │
                                          │  (24h 交付输出)   │
                                          └─────────────────┘
```

### 2.2 生产电路 DAG

生产电路由 `pipelines/production_circuit/circuit_orchestrator.py` 编排，执行 18 步顺序 DAG：

```
Step 1:  data_update             → 更新市场数据
Step 2:  feature_snapshot        → 生成特征快照
Step 3:  dayahead_chain          → 日前模型并行预测
Step 4:  dayahead_repair         → 日前模块修补
Step 5:  dayahead_fusion         → 日前融合
Step 6:  dayahead_classifier     → 日前分类器调整
Step 7:  dayahead_task_final     → 日前任务最终
Step 8:  realtime_chain          → 实时模型预测
Step 9:  realtime_repair         → 实时模块修补
Step 10: realtime_fusion         → 实时融合
Step 11: realtime_neg_price_fix  → 实时负电价修正
Step 12: realtime_classifier     → 实时分类器调整
Step 13: realtime_task_final     → 实时任务最终
Step 14: cross_task_fusion       → 跨任务融合
Step 15: separator_repair        → 分离器修补
Step 16: delivery_final          → 交付最终
Step 17: postflight              → 8 项校验
Step 18: metrics                 → 指标计算
Step 19: finish_run              → 运行结束
```

每个步骤的状态契约（Honest Status Contract）：

| 状态 | 含义 |
|------|------|
| COMPLETE | 正常完成，输出完备 |
| PARTIAL | 部分完成（如实时模型缺失） |
| SKIPPED | 条件不满足跳过 |
| FAIL | 执行失败 |
| NEEDS_MODEL_OUTPUT | 缺少模型输入 |

---

## 3. 数据库设计

### 3.1 表清单（23 表）

| 组 | 表名 | 用途 |
|----|------|------|
| **核心** | efm_runs | 运行记录 |
| | efm_predictions | 预测值（所有阶段 × 模型） |
| | efm_actual_prices | 实际价格（da_anchor / rt_actual） |
| **审计** | efm_pipeline_steps | 每一步执行记录 |
| | efm_prediction_batches | 预测批次 |
| | efm_prediction_lineage_edges | 因果关系（raw→repaired→fused→final） |
| | efm_repair_decisions | 修补决策 |
| | efm_fusion_decisions | 融合决策 |
| | efm_run_events | 运行时事件 |
| **融合** | efm_fusion_candidates | 融合候选（含权重、rank、score） |
| | efm_task_finals | 日前/实时分离最终结果 |
| | efm_delivery_finals | 交付最终（含 provenance） |
| **校验** | efm_postflight_checks | 8 项校验结果 |
| **指标** | efm_metric_runs | 持久化指标 |
| **模型** | efm_model_registry | 模型注册目录 |
| **数据** | efm_data_sources | 数据源定义 |
| | efm_source_files | 源文件记录 |
| | efm_data_update_runs | 数据更新运行 |
| | efm_market_data_hourly | 小时级市场数据 |
| | efm_dataset_versions | 数据集版本 |
| | efm_feature_snapshots | 特征快照 |
| **交付** | efm_delivery_outputs | 交付文件记录 |
| | efm_artifacts | 构件记录 |

### 3.2 核心表详解

#### efm_predictions

存储所有模型在所有阶段的预测值。唯一键 `(run_id, target_date, hour_business, stage, model_name)` 确保多模型在同一阶段不会冲突。

```sql
CREATE TABLE efm_predictions (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id              VARCHAR(64)  NOT NULL,
    target_date         DATE         NOT NULL,
    hour_business       TINYINT      NOT NULL,  -- 1..24
    task                ENUM('dayahead','realtime','fusion','final','shadow','delivery') NOT NULL,
    stage               VARCHAR(32)  NOT NULL,  -- e.g. 'dayahead_raw_model'
    model_name          VARCHAR(64)  NOT NULL,  -- e.g. 'cfg05'
    model_version       VARCHAR(32)  DEFAULT 'unknown',
    pred_price          DECIMAL(12,4) NOT NULL,
    is_shadow           BOOLEAN      NOT NULL DEFAULT FALSE,
    is_selected         BOOLEAN      NOT NULL DEFAULT FALSE,
    ...
    UNIQUE KEY uk_run_date_hour_stage (run_id, target_date, hour_business, stage, model_name),
    FOREIGN KEY (run_id) REFERENCES efm_runs(run_id) ON DELETE CASCADE
);
```

#### efm_pipeline_steps

记录生产电路每一步的执行状态。

```sql
CREATE TABLE efm_pipeline_steps (
    run_id       VARCHAR(64)  NOT NULL,
    step_name    VARCHAR(64)  NOT NULL,
    step_order   SMALLINT     NOT NULL,
    status       ENUM('PENDING','RUNNING','COMPLETE','PARTIAL','FAIL','SKIPPED') NOT NULL,
    input_count  INT,
    output_count INT,
    runtime_ms   INT,         -- 耗时（毫秒）
    ...
);
```

### 3.3 3NF 规范

数据库设计遵循第三范式（3NF）：

- **1NF**：所有列原子化，无重复组
- **2NF**：所有非键列完全依赖于业务主键 `(run_id, target_date, hour_business, stage, model_name)`
- **3NF**：无传递依赖（`task` 虽可由 `stage` 推导，但为查询性能保留为冗余列）

---

## 4. 生产电路详解

### 4.1 日前子链

输入：3 个模型（cfg05 / xgboost_rich / catboost_rich）各 24h 预测
输出：日前 task_final（24h 融合后输出）

```
模型预测 (72行) → 修补 (72行) → 融合 (24行) → 分类器调整 (24行) → task_final (24行)
```

各阶段说明：

| 阶段 | 入 → 出 | 说明 |
|------|---------|------|
| dayahead_raw_model | 3×24 → 72 | 原始模型输出 |
| dayahead_module_repaired | 72 → 72 | 各模型独立修补（异常值/NaN） |
| dayahead_fused | 72 → 24 | 融合为单行/h（含权重学习） |
| dayahead_classifier_adjusted | 24 → 24 | 分类器调整 |
| dayahead_task_final | 24 → 24 | 最终确定（写入 efm_task_finals） |

### 4.2 实时子链

输入：SGDFNet / TimesFM / DA-aware 选择器
输出：实时 task_final（24h）

```
模型预测 (72行) → 修补 (72行) → 融合 (24行) → 负电价修正 (24h) → 分类器 → task_final (24h)
```

### 4.3 跨任务与交付

```
日前 final + 实时 final → 跨任务融合 (24h) → 分离器修补 (24h) → 交付 final (24h)
```

### 4.4 修补逻辑

每个模型独立修补，明确记录 before/after：

```python
# 修补规则示例
规则: outlier_clamp      — 钳制异常值到 [50, 2000]
规则: gap_fill           — 用前后均值填补空缺
规则: negative_to_anchor — 负电价 → DA_anchor 回退
```

### 4.5 融合策略

当有多个模型可用时：

1. 计算各模型历史表现权重
2. 按权重加权平均
3. 只有一个候选时权重=1.0

---

## 5. 数据流

### 5.1 数据摄入

```
PMOS CSV (GBK编码)
    → sync_data.py（2.5 仓库）
    → data/shandong_pmos_hourly.{csv,xlsx}
    → (复制到 2.0_exp/data/)
    → P1 引擎读取训练
```

### 5.2 预测导入

```
P1 all_predictions.csv
    → ingest_model_predictions.py
    → efm_predictions (raw_model stage)
    → production_circuit 读取
```

### 5.3 写入链路

```
ingest → write_stage_predictions(conn, run_id, date, task, stage, rows)
    → INSERT INTO efm_predictions
    → INSERT INTO efm_prediction_batches
    → conn.commit()

repair → insert_repair_decision(conn, RepairDecision)
fusion → insert_fusion_candidate(conn, FusionCandidate)
final  → insert_task_final(conn, TaskFinal)
delivery → insert_delivery_final(conn, DeliveryFinal)
metric → insert_metric_run(conn, dict)
```

---

## 6. 模型管理

### 6.1 注册模型

所有模型在 `efm_model_registry` 中注册：

```sql
INSERT INTO efm_model_registry (model_name, model_version, task, status, description)
VALUES ('cfg05', 'v_cfg05', 'dayahead', 'active', '3.0 day-ahead LightGBM cfg05');
```

### 6.2 可用模型

| 模型 | 任务 | 状态 | 说明 |
|------|------|------|------|
| cfg05 | 日前 | active | LightGBM rich features（冠军候选） |
| xgboost_rich | 日前 | active | XGBoost rich features |
| catboost_rich | 日前 | active | CatBoost rich features |
| sgdfnet | 实时 | active | SGDFNet 深度学习 |
| timesfm | 实时 | shadow | TimesFM 基座模型 |
| da_aware_sgdf_selector | 实时 | shadow | DA 感知选择器 |

### 6.3 模型输出格式

```csv
business_day,ds,hour_business,period,y_pred,model_name,model_version,source_repo,run_id,y_true
2026-03-01,2026-03-01 01:00:00,1,1_8,366.16,cfg05,v_cfg05,epf-sota-experiment,p1_202603,350.0
```

---

## 7. 指标计算

### 7.1 公式

详细公式见 `docs/metrics_calculation.md`。

### 7.2 官方指标脚本

```bash
python tools/_compute_official_metrics.py
```

### 7.3 指标字段

| 字段 | 含义 |
|------|------|
| smape_floor50 | floor(50) 裁剪后的 SMAPE |
| accuracy | = 1 - smape_floor50 |
| mae | 平均绝对误差 |
| rmse | 均方根误差 |
| wmape | 加权绝对百分比误差 |
| mape | 平均绝对百分比误差 |
| r2 | 决定系数 |
| scr | 价差方向准确率 |
| arbitrage_basic | 基础版度电套利 |
| arbitrage_improved | 改良版度电套利 |

---

## 8. 部署指南

### 8.1 首次部署

```bash
# 1. 启动 MySQL
docker run -d --name efm3-mysql -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=your_password \
  mysql:8.0

# 2. 初始化数据库
mysql -h 127.0.0.1 -u root -p < db/schema.sql
mysql -h 127.0.0.1 -u root -p < db/migrations/005_production_circuit_schema.sql

# 3. 配置环境
cp .env.example .env.local
# 编辑 .env.local 填入数据库连接

# 4. 安装依赖
pip install -r requirements.txt

# 5. 同步数据
cd ../electricity_forecast_model2.5
python sync_data.py --source auto --force-sync
cp data/shandong_pmos_hourly.csv ../electricity_forecast_model2.0_exp/data/
```

### 8.2 每日运行

```bash
# 1. 同步最新数据
cd ../electricity_forecast_model2.5
python sync_data.py --source auto

# 2. 训练 + 预测（P1 引擎）
cd ../models
python scripts/run_dayahead_p1_walkforward.py \
    --test-months $(date +%Y-%m) \
    --models cfg05,xgboost_rich,catboost_rich \
    --cpu-only

# 3. 导入 + 生产电路
cd ../efm3.0
python tools/backtest_dayahead.py \
    --p1-output ../models/outputs/p1_dayahead/latest
```

### 8.3 环境变量

| 变量 | 用途 |
|------|------|
| EFM3_DB_URL | MySQL 连接 URL（含密码 %23 编码） |
| DB_HOST / DB / DB_USER / DB_PWD | 2.5 sync 数据库配置 |

---

## 9. 故障排查

### 9.1 数据库连接失败

```bash
# 检查容器
docker ps | grep efm3-mysql
# 检查密码
docker exec efm3-mysql mysql -uroot -p'password' -e "SELECT 1"
# 检查用户权限
docker exec efm3-mysql mysql -uroot -p'password' -e "ALTER USER 'root'@'%' IDENTIFIED BY 'password'; FLUSH PRIVILEGES;"
```

### 9.2 唯一键冲突

`efm_predictions` 的 `uk_run_date_hour_stage` 唯一键包含 `(run_id, target_date, hour_business, stage, model_name)`。当相同组合多次写入时会使用 ON DUPLICATE KEY UPDATE。

### 9.3 外键约束失败

`efm_predictions` 引用 `efm_runs.run_id`。导入前必须先用 `create_run` 创建父运行记录。

```python
from common.db.repositories import create_run
from common.db.models import RunRecord

create_run(conn, RunRecord(
    run_id="efm3_raw_2026-03-15_dayahead",
    target_date="2026-03-15",
    chain_version="ingest_raw_model",
    mode="dry_run",
    status="COMPLETE",
    delivery_status="NOT_ATTEMPTED",
))
```

### 9.4 指标异常

- 如果 sMAPE 极高（>50%）：检查 `da_anchor` 和 `pred_price` 的数值范围
- 如果 `dayahead_task_final` 行数不对：检查 fusion 是否只产生了一个候选（权重=1.0）

### 9.5 运行状态为 RUNNING

如果回测或正式运行被中断（如脚本崩溃），`efm_runs.status` 可能停留在 RUNNING。
这不是数据问题（预测数据已写入），只是状态未更新。可以忽略或手动更新：

```sql
UPDATE efm_runs SET status='COMPLETE' WHERE run_id='xxx' AND status='RUNNING';
```

---

## 附录 A：文件结构

```
efm3.0/
├── README.md                     ← 项目说明
├── .env.local                    ← 本地配置（已 gitignore）
├── db/
│   ├── schema.sql                ← 完整 schema（23 表）
│   └── migrations/               ← 数据库迁移
│       ├── 001_init_efm3_mysql.sql
│       └── 005_production_circuit_schema.sql
├── common/
│   └── db/
│       ├── connection.py         ← DbConnectionManager
│       ├── models.py             ← 数据模型
│       └── repositories.py       ← 存储库（CRUD）
├── pipelines/
│   └── production_circuit/       ← 生产电路
│       ├── circuit_orchestrator.py  ← DAG 编排
│       ├── contracts.py          ← 数据契约
│       ├── step_recorder.py      ← 持久化层
│       ├── model_loader.py       ← 模型输出加载
│       ├── dayahead_chain.py     ← 日前子链
│       ├── realtime_chain.py     ← 实时子链
│       ├── repair_chain.py       ← 修补逻辑
│       ├── fusion_chain.py       ← 融合逻辑
│       ├── classifier_chain.py   ← 分类器
│       ├── negative_price_fixer.py ← 负电价修正
│       ├── separator_chain.py    ← 分离器
│       └── delivery_chain.py     ← 交付
├── tools/
│   ├── ingest_model_predictions.py ← 预测导入
│   ├── backtest_dayahead.py      ← 日前回测驱动
│   ├── smoke_pc.py               ← 冒烟测试
│   ├── _backtest_report.py       ← 回测报表
│   └── _compute_official_metrics.py ← 官方指标计算
├── tests/                        ← 测试
│   ├── test_production_circuit_new.py
│   ├── test_db_schema_contract.py
│   └── pc_fake_db.py
└── docs/                         ← 文档
    ├── metrics_calculation.md    ← 指标公式
    └── TECHNICAL_REFERENCE.md    ← 本文件（本地，不推送）
```
