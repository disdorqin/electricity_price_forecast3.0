# Frontend API Examples (Desensitized)

> 这些示例为**脱敏 / 代表性** JSON，字段名与类型与 `docs/api/openapi.json` 完全一致，
> 但数值为示意，且**不包含任何真实数据库 URL 或密码**。
> 真实连接串只存在于后端 `.env.local` 与 `EFM3_DB_URL` 环境变量。

---

## 1. `GET /api/health`

```json
{
  "status": "ok",
  "service": "efm3-control-plane",
  "app_env": "local",
  "db_configured": true,
  "ops_enabled": false,
  "timestamp": "2026-07-09T09:00:00.123456"
}
```

`GET /api/health/db`（仅返回连通状态，**不返回连接串**）：

```json
{
  "status": "ok",
  "db_url_prefix": "mysql+pymysql://root:***@127.0.0.1:3306/efm3",
  "latency_ms": 2.1
}
```

> 注意：`db_url_prefix` 里密码始终被掩码为 `***`；前端不应解析或展示它。

---

## 2. `GET /api/runs/{run_id}/summary`

```json
{
  "run_id": "efm3_20260615_1cb15b6_20260709T090000",
  "target_date": "2026-06-15",
  "chain_version": "3.0-db-ledger-v1",
  "mode": "formal_sim",
  "status": "COMPLETE",
  "delivery_status": "NORMAL",
  "exit_code": 0,
  "started_at": "2026-07-09T09:00:00",
  "finished_at": "2026-07-09T09:00:08",
  "duration_s": 8.1
}
```

---

## 3. `GET /api/runs/{run_id}/predictions/selected`

返回 **24 行**（`hour_business` 1..24）。下面展示首尾两行示意：

```json
[
  {
    "run_id": "efm3_20260615_1cb15b6_20260709T090000",
    "target_date": "2026-06-15",
    "hour_business": 1,
    "stage": "final_selected",
    "model_name": "seasonal_da_router",
    "pred_price": 312.45,
    "selected_reason": "non_winter_da_anchor_fallback"
  },
  {
    "run_id": "efm3_20260615_1cb15b6_20260709T090000",
    "target_date": "2026-06-15",
    "hour_business": 24,
    "stage": "final_selected",
    "model_name": "seasonal_da_router",
    "pred_price": 298.10,
    "selected_reason": "non_winter_da_anchor_fallback"
  }
]
```

> `hour_business = 24` 表示当日 **00:00** 时段；`pred_price` 单位为 **CNY/MWh**。

---

## 4. `GET /api/lineage/{run_id}/hour/{hour_business}`（hour = 24）

```json
{
  "run_id": "efm3_20260615_1cb15b6_20260709T090000",
  "hour_business": 24,
  "target_date": "2026-06-15",
  "nodes": [
    {"node_type": "source_file", "label": "shandong_pmos_hourly.csv", "detail": null},
    {"node_type": "dataset_version", "label": "ds_20260615", "detail": {"status": "READY"}},
    {"node_type": "feature_snapshot", "label": "feat_20260615_h24", "detail": null},
    {"node_type": "candidate", "label": "da_anchor", "detail": {"pred_price": 300.0}},
    {"node_type": "candidate", "label": "official_baseline", "detail": {"pred_price": 310.0}},
    {"node_type": "router", "label": "seasonal_da_router", "detail": {"decision": "non_winter_da_anchor_fallback"}},
    {"node_type": "selected", "label": "seasonal_da_router", "detail": {"pred_price": 298.10}},
    {"node_type": "postflight", "label": "row_count_24", "detail": {"passed": true}},
    {"node_type": "delivery", "label": "none (formal_sim)", "detail": {"delivered": false}}
  ],
  "edges": [
    {"from_node": "source_file", "to_node": "dataset_version"},
    {"from_node": "dataset_version", "to_node": "feature_snapshot"},
    {"from_node": "feature_snapshot", "to_node": "candidate"},
    {"from_node": "candidate", "to_node": "router"},
    {"from_node": "router", "to_node": "selected"},
    {"from_node": "selected", "to_node": "postflight"},
    {"from_node": "postflight", "to_node": "delivery"}
  ],
  "router_decision": {"policy": "seasonal_da_router", "reason": "non_winter_da_anchor_fallback"},
  "selected_reason": "non_winter_da_anchor_fallback",
  "is_shadow": false,
  "shadow_safe": true
}
```

> `shadow_safe: true` 表示该小时影子预测未污染 `final`；`delivered: false` 表示
> `formal_sim` 未产生正式交付（符合预期）。

---

## 5. 指标摘要（`tools/db_ops/db_yearly_metrics.py` 输出结构）

```json
{
  "period": {"start": "2026-01-01", "end": "2026-06-30"},
  "yearly": {
    "smape": 49.70,
    "mae": 92.83,
    "rmse": 143.90,
    "mape": 101.49,
    "wmape": 30.92,
    "evaluable_days": 181
  },
  "quarterly": [
    {"quarter": "Q1", "days": 90, "pass": 90, "smape": 54.24, "mae": 99.74, "rmse": 148.85, "wmape": 37.58},
    {"quarter": "Q2", "days": 91, "pass": 91, "smape": 45.21, "mae": 85.99, "rmse": 138.83, "wmape": 24.34}
  ]
}
```

> 指标定义：以**日前预测（`final_selected`）**对比**实时实际价（`rt_actual`）**。
> `smape`/`mae`/`rmse`/`wmape` 单位与价格一致（CNY/MWh 的派生量）；`wmape` 为加权
> 平均绝对百分比误差。这些是基线/DA-anchor 基准，非最终冠军模型对比。

---

## 6. `GET /api/datasets`（节选）

```json
[
  {
    "dataset_id": "ds_20260615",
    "target_date": "2026-06-15",
    "status": "READY",
    "row_counts": {"rows": 24},
    "leakage_cutoff": "2026-06-14 14:00:00",
    "canonical_hour_mapping": 1
  }
]
```

---

## 前端对接检查清单

- [ ] 所有请求走 `/api/*`，不直连数据库
- [ ] `hour_business` 按 `1..24` 渲染（24 = 00:00）
- [ ] 价格统一 CNY/MWh，前端不换算
- [ ] 不解析/不展示任何 `db_url`（密码已掩码）
- [ ] `formal_sim` 数据上隐藏"正式提交"操作
- [ ] 空数据日优雅降级（空列表 / 404）
