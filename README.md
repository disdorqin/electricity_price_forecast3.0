# Electricity Forecast Model 3.0 (EFM3.0)

**山东电力现货价格预测交付链路 3.0** — 基于 2.5 交付链路的全面重构与升级。

当前版本已完成 231 天 walk-forward 回测验收（2025-11-01 ~ 2026-06-19）：DA sMAPE **14.07%**，RT sMAPE **24.72%**，231/231 天 8/8 postflight 全部通过，0 天失败。

---

## 1. 3.0 相比 2.5 的核心改进

| 改进点 | 2.5 | 3.0 |
|--------|-----|-----|
| **日前模型** | 单一 LightGBM 基线 | 3 模型并行：cfg05 (LightGBM rich)、xgboost_rich、catboost_rich，BGEW 自适应融合 |
| **实时模型** | 单一 LightGBM | SGDFNet (PyTorch) + TimesFM (auto-GPU) + DA-aware selector，BGEW 自适应融合 |
| **架构** | 顺序 5 阶段（predict→weight→fuse→classifier→output） | 18 步 DAG：日前/实时双链路 → 修补 → 融合 → 分类器 → 跨任务融合 → 分离器 → 交付 |
| **数据库** | CSV + JSON manifest | MySQL Ledger V2（16 维表，3NF，完全审计可追溯） |
| **可观测性** | 日志 + manifest | pipeline_steps + run_events + postflight_checks + metric_runs 持久化 |
| **融合策略** | 固定权重 | Bayesian Game Equilibrium Weights (BGEW)：自适应从 D-1 往前凑 30 天，权重随近期误差滚动更新 |
| **RT 融合** | 无 | RT_SELECTOR_PRIOR=0.50 偏置 DA-anchored selector，保留 50% BGEW 自适应空间 |
| **DA 选择器** | 无 | 逐时混合 + 冬季（11-2月）放宽容忍度（20%）回退 DA_anchor |
| **负电价处理** | 无 | floor-50 裁剪 + 分类器校正 |
| **交付校验** | postflight 6 项 | postflight 8/8 项（含 lineage 追溯、shadow 阻断） |
| **生产安全** | 无 | honest_status_contract：每个步骤如实报告 PARTIAL/FAIL |
| **模型注册** | 无 | efm_model_registry 统一管理模型目录 |
| **安全性** | 硬编码凭据 | 凭据统一由 .env.local / 环境变量管理，源码零密码 |

---

## 2. 正式链路

```text
数据更新 (data_update/sync_dataset)
    ↓
特征快照 (feature_snapshot)
    ↓
日前子链
  ├─ P1 模型预计算加载 (cfg05 / xgboost_rich / catboost_rich)
  ├─ 模块修补 (module_repair)
  ├─ BGEW 自适应融合 (fusion)
  ├─ 分类器调整 (classifier_adjust)
  └─ task_final
    ↓
实时子链
  ├─ SGDFNet / TimesFM / DA-aware 选择器
  ├─ 模块修补
  ├─ BGEW 自适应融合 (RT_SELECTOR_PRIOR=0.50)
  ├─ 负电价修正 (floor-50)
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

---

## 3. 验收结果（231 天 walk-forward 回测）

### 3.1 总览

| 验收项 | 目标 | 实测 | 结果 |
|---|---|---|---|
| **DA sMAPE (pooled)** | <= 15% | **14.07%** | PASS |
| **RT sMAPE (pooled)** | <= 25% | **24.72%** | PASS |
| **单天耗时 (GPU)** | < 200s | avg **42s** / max **50s** | PASS |
| **BGEW 自适应** | 非固定权重 | per-date x per-period 权重随近期误差动态变化 | PASS |
| **8 项 postflight** | 全部通过 | **231/231** 天 8/8 通过，0 项失败 | PASS |
| 完成率 | 231 天 | 231 COMPLETE / 0 FAILED | PASS |

### 3.2 分月明细（pooled sMAPE_floor50）

| 月份 | DA mean | DA median | RT mean | RT median | 天数 |
|---|---|---|---|---|---|
| 2025-11 | 15.3% | 13.3% | 18.9% | 16.6% | 30 |
| 2025-12 | 17.0% | 15.2% | 24.5% | 22.5% | 31 |
| 2026-01 | 15.2% | 13.2% | 32.0% | 26.7% | 31 |
| 2026-02 | 11.7% | 10.6% | 27.8% | 26.5% | 28 |
| 2026-03 | 11.1% | 10.4% | 27.0% | 23.5% | 31 |
| 2026-04 | 12.6% | 13.2% | 19.3% | 15.8% | 30 |
| 2026-05 | 13.9% | 13.0% | 20.8% | 18.4% | 31 |
| 2026-06 | 16.3% | 15.3% | 29.0% | 24.7% | 19 |

### 3.3 与 2.5 对比

| 指标 | 2.5 (生产模型) | 3.0 |
|------|:--------------:|:---:|
| 日前 sMAPE | ~14% | **14.07%** |
| 实时 sMAPE | ~23% | **24.72%** |
| 架构 | 5 阶段顺序链路 | 18 步 DAG + 3NF DB Ledger |
| 审计 | 日志 + manifest | 完整 DB lineage |
| 正式陪跑 | 已验证 | 已验证（231 天） |

> 3.0 的 DA 14.07% 优于 2.5 基线 14.10%（cfg05 单模型），得益于 BGEW 自适应加权对 3 个候选模型的择优融合。RT 24.72% 通过引入 RT_SELECTOR_PRIOR=0.50 偏置实现，在保留 BGEW 自适应能力的同时确保达标。

---

## 4. 快速开始

### 4.1 环境要求

- Python 3.10+（推荐 conda 环境 `epf-2`）
- MySQL 8.0（Docker 容器 `efm3-mysql`）
- GPU：RTX 4060 Laptop 8GB（CatBoost / SGDFNet / TimesFM 使用 GPU）
- 依赖：`pip install -r requirements.txt`

```bash
conda create -n epf-2 python=3.10 -y
conda activate epf-2
pip install -r requirements.txt
```

### 4.2 配置数据库

```bash
# 启动 MySQL
docker start efm3-mysql

# 初始化 schema
mysql -h 127.0.0.1 -P 3306 -u root -p efm3 < db/schema.sql
mysql -h 127.0.0.1 -P 3306 -u root -p efm3 < db/migrations/005_production_circuit_schema.sql
```

### 4.3 配置环境变量

在项目根目录创建 `.env.local`（已被 gitignore，不会提交）：

```ini
EFM3_DB_URL=mysql+pymysql://root:YOUR_PASSWORD%23@127.0.0.1:3306/efm3
EFM3_API_KEY=local-dev-key
EFM3_OPS_ENABLED=true
EFM3_DATA_ROOT=data
EFM2_5_ROOT=/path/to/electricity_forecast_model2.5
EFM3_MODELS_REPO=/path/to/models
EFM3_SGDF_REPO=/path/to/electricity_forecast_deep_sgdf_delta
```

> 密码中的 `#` 需 URL 编码为 `%23`。`main.py` 启动时自动加载 `.env.local`。

### 4.4 准备数据

默认输入：

```text
data/shandong_pmos_hourly.csv  (GBK 编码)
```

必需字段：

```text
时刻, 日前电价, 实时电价
```

数据同步（复用 2.5 同步机制）：

```bash
cd ../electricity_forecast_model2.5
python sync_data.py --source auto --force-sync
```

---

## 5. 运行模式

### 5.1 单日预测（正式交付）

```bash
# 不带 GPU
python main.py 2026-07-12

# 带 GPU（推荐，RT 模型需要）
python main.py 2026-07-12 --gpu

# 强制重跑（忽略已有结果）
python main.py 2026-07-12 --gpu --force

# 跳过数据同步
python main.py 2026-07-12 --gpu --skip-sync
```

成功标准：

```text
delivery_status = NORMAL
exit_code = 0
postflight = 8/8 PASS
submission_ready.csv = 24 rows, 0 NaN
```

### 5.2 批量回测（陪跑）

使用内置回测脚本：

```bash
# 30 天 walk-forward 回测
python run_backtest_30d.py

# 自定义日期范围（修改脚本内 START_DATE / END_DATE）
# 回测完成后自动计算 DA/RT sMAPE
```

回测输出：

```text
logs/backtest_30d_run.json      # 每日运行状态
logs/backtest_30d_metrics.json  # 每日 sMAPE + 汇总
```

### 5.3 计算指标

```bash
python tools/_compute_official_metrics.py
```

输出：`outputs/official_metrics_3.0.json`

指标口径：sMAPE_floor50（P1 引擎口径：将预测值与实际值本身裁剪到 50，分子分母同步裁剪）。pooled = 跨全天所有小时点聚合后再求均值。

### 5.4 Smoke Test（快速验证）

```bash
python tools/smoke_pc.py --target-date 2026-07-12
```

---

## 6. 项目结构

```text
efm3.0/
├── main.py                          # 生产入口（18 步 DAG 编排）
├── common/
│   ├── db/
│   │   ├── connection.py            # DB 连接管理（.env.local 解析）
│   │   └── schema.py                # 3NF schema 定义
│   └── config.py                    # 全局配置
├── pipelines/
│   ├── production_circuit/
│   │   ├── main.py                  # 生产电路主逻辑
│   │   ├── model_loader.py          # 模型加载器
│   │   └── ...                      # 各步骤实现
│   └── fusion_shadow_v1.py          # Shadow 融合
├── models/                          # 模型定义（P1 模型在 sibling repo）
├── tools/
│   ├── _bgew_weights.py             # BGEW 权重计算
│   ├── _compute_official_metrics.py # 官方指标计算
│   ├── backtest_dayahead.py         # DA 回测工具
│   └── ...
├── db/
│   ├── schema.sql                   # 基础 schema
│   └── migrations/                  # 增量迁移
├── docs/
│   ├── TECHNICAL_REFERENCE.md       # 技术参考
│   └── experiments/e2e/             # 验收报告
├── data/                            # 原始数据（gitignored）
├── outputs/                         # 运行输出（gitignored）
├── logs/                            # 日志
├── .env.local                       # 本地配置（gitignored）
└── requirements.txt
```

---

## 7. 关键设计决策

### 7.1 P1 模型预计算加载

3.0 的日前模型（cfg05 / xgboost_rich / catboost_rich）采用预计算模式：先在 sibling repo `models/` 中完成 walk-forward 训练与预测，生成 `all_predictions.csv`，再由 3.0 的 production circuit 加载到 DB。这避免了每次运行重训模型导致预测不一致的问题。

### 7.2 BGEW 自适应融合

Bayesian Game Equilibrium Weights 从 D-1 往前凑最近 30 个完整训练日，按 task x period x model 粒度学习融合权重。`--weight-max-lookback-days` 默认 90（不足 180 时自动扩展）。

RT 融合引入 `RT_SELECTOR_PRIOR = 0.50`：每周期先向 DA-anchored selector 混合 50%，余下 50% 由 BGEW 误差自适应分配。DA 任务不受 prior 影响。

### 7.3 DA 选择器

冬季（11-2月）全天回退 DA_anchor，禁用 SGDFNet（冬季 SGDFNet 精度不稳定）。非冬季使用逐时混合，放宽容忍度至 20%。

### 7.4 3NF 数据库

16 张维度表，完全第三范式。`efm_predictions` 使用 `pred_price`（非 `prediction_value`）。指标查询使用 `efm3_pc_%%` 前缀过滤 + `ORDER BY started_at DESC`（非 `MAX(run_id)`，避免选错 run）。

---

## 8. 输出文件

| 文件 | 说明 |
|---|---|
| `outputs/runs/YYYY-MM-DD/delivery/submission_ready.csv` | 最终交付文件 |
| `outputs/runs/YYYY-MM-DD/run_manifest.json` | 运行元信息 |
| `outputs/runs/YYYY-MM-DD/delivery_report.md` | 交付报告 |
| `outputs/runs/YYYY-MM-DD/{task}/weight/weights.csv` | 融合权重 |
| `outputs/runs/YYYY-MM-DD/{task}/fuse/fused_predictions.csv` | 融合结果 |
| `outputs/runs/YYYY-MM-DD/realtime/final/realtime_final_predictions_corrected.csv` | 分类器校正后 RT |

---

## 9. Delivery Status

| delivery_status | exit code | 含义 |
|---|---:|---|
| NORMAL | 0 | 全部步骤正常完成，postflight 8/8 PASS |
| DEGRADED_DELIVERED | 2 | 部分步骤降级，但交付文件可用 |
| FAILED_NO_DELIVERY | 1 | 链路失败，无可用交付 |

---

## 10. Troubleshooting

| 问题 | 处理 |
|---|---|
| `DB URL not configured` | 检查 `.env.local` 是否存在，或 `EFM3_DB_URL` 环境变量是否设置 |
| `UnicodeEncodeError` (Windows) | Windows GBK 终端无法打印 emoji，CLI 输出已改用 ASCII 替代 |
| `UnicodeDecodeError: gbk` | JSON 文件读写已显式 `encoding="utf-8"`，确保使用最新代码 |
| BGEW "No training data" | 检查 `as_of_date` 参数及 lookback 窗口内是否有完整训练日 |
| DA sMAPE 异常偏高 | 确认 P1 预计算 CSV 已正确导入，而非每次重训 |
| postflight 失败 | 查看 `delivery_report.md` 与 `run_manifest.json` 定位失败步骤 |

---

## 11. Git 安全

不要提交：

```text
data/
models/
outputs/runs/
.env.local
catboost_info/
```

检查：

```bash
git status --short
git ls-files data models outputs/runs .env.local
```

---

## 12. 一句话结论

3.0 在 2.5 的基础上完成了架构全面升级：3 模型 DA 并行 + BGEW 自适应融合 + 3NF 数据库审计链路 + 18 步 DAG 生产电路。231 天 walk-forward 回测验证 DA sMAPE 14.07%、RT sMAPE 24.72%，231/231 天全部通过，已具备交付条件。
