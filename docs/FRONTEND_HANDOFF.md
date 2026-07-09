# Frontend Handoff Guide

本仓库（`electricity_price_forecast3.0`）是 **后端交付仓库**。本文档说明前端（Web / 大屏）
应如何与后端对接。

## 核心原则

1. **前端不直接连数据库。** 前端永远不持有 MySQL 连接串，也不直连 `efm3` 库。
2. **前端只调用 FastAPI。** 所有数据通过 `backend.app.main:app` 暴露的 HTTP 接口获取。
3. **OpenAPI 是合同。** 接口以 `docs/api/openapi.json` 为唯一权威定义；前端据此生成
   client 或手写请求，不要依赖未声明的字段。
4. **数据库凭证不下发到浏览器。** `EFM3_DB_URL` / `.env.local` 仅存在于后端运行时与
   CI 环境变量，绝不出现在前端打包产物、HTML、JS 或任何响应体里。

---

## OpenAPI 文件

| 文件 | 说明 |
| --- | --- |
| `docs/api/openapi.json` | 完整 OpenAPI 3 合同（所有 `/api/*` 路由、请求/响应 schema） |
| `docs/api/FRONTEND_API_EXAMPLES.md` | 脱敏 JSON 示例（health / summary / selected / lineage / metrics） |

启动本地服务后也可访问 Swagger UI：`http://127.0.0.1:8000/docs`。

---

## 核心接口（8 个）

| # | 方法 + 路径 | 用途 |
| - | --- | --- |
| 1 | `GET /api/health` | 服务存活探针（无需 DB） |
| 2 | `GET /api/health/db` | 数据库连通性（不返回连接串，仅 `status`） |
| 3 | `GET /api/runs?limit=50` | 运行列表（最新优先） |
| 4 | `GET /api/runs/{run_id}/summary` | 单次运行摘要（状态 / 交付状态 / 退出码） |
| 5 | `GET /api/runs/{run_id}/predictions/selected` | 该运行被选中的 24 小时预测 |
| 6 | `GET /api/lineage/{run_id}/hour/{hour_business}` | 单小时预测血缘（来源→候选→路由→选中→postflight） |
| 7 | `GET /api/datasets` | 数据集版本 / 就绪度列表 |
| 8 | `GET /api/reports/shadow-safety` | 影子监控安全报告（是否污染 final） |

> 所有 `/api/*` 路由默认受 `require_access` 保护；本地 `127.0.0.1` / `localhost` 无 key 可访问，
> 部署到公网时必须启用 API key / 反向代理鉴权。

---

## 必须给前端说明的字段语义

| 字段 | 含义 | 前端注意 |
| --- | --- | --- |
| `run_id` | **主索引**。一次链执行（一个 `target_date`）的唯一 id | 所有详情接口都以它为主键 |
| `target_date` | **业务日期**（预测的目标日，`YYYY-MM-DD`） | 不是运行时间，是电价所属日 |
| `hour_business` | 业务小时，**取值 `1..24`** | 24 表示当日 00:00 时段 |
| `00:00 → 24` | 时钟 00:00 映射为业务小时 24 | 不要把 00:00 当 0，渲染时 `24` 排在最前或单独标注 |
| `pred_price` | 模型/路由给出的**预测价**（单位 CNY/MWh） | 来自 `efm_predictions.pred_price` |
| `actual_price` / `rt_actual` | **实际实时价** | 来自 `efm_actual_prices.rt_actual`（真实结算价，用于误差评估） |
| `da_anchor` | 日前锚定预测（冬季策略直接使用） | 也是 `pred_price` 的一种来源 |
| `selected_reason` | router 选择该预测的原因 | 例如 `"winter DA anchor"` / `"non_winter_da_anchor_fallback"`；前端可展示给用户 |
| `is_selected` | 是否最终被选中 | 仅 `selected=1` 的 24 行进入 `final_selected` |
| `is_shadow` | 是否为影子（不被正式采用） | 影子预测不参与交付，仅供对照/监控 |

---

## 关键行为约束（前端务必知悉）

- **`formal_sim` 不生成正式 submission。** `mode=formal_sim` 是回放/评估模式，不会写出
  正式交付文件（`delivery_outputs` 为 0）。前端的"正式提交"按钮在 `formal_sim` 数据上
  不应出现。
- **不要信任任何来自响应的 DB URL。** 后端健康接口只回 `status` / `db_configured` 布尔，
  绝不回明文连接串；若发现响应含 `mysql://...` 请立即上报。
- **精度单位统一为 CNY/MWh。** 所有价格字段同一单位，无需前端换算。
- **无数据日返回空列表 / 404，而非 500。** 链对缺数日期设计为零预测 + 正常退出，前端需
  优雅处理空态。

---

## 推荐前端对接流程

1. 启动时轮询 `GET /api/health` 确认后端在线。
2. 展示运行列表：`GET /api/runs?limit=50`，点击某行取 `run_id`。
3. 详情：`GET /api/runs/{run_id}/summary` + `GET /api/runs/{run_id}/predictions/selected`
   （24 行，按 `hour_business` 1..24 渲染）。
4. 血缘下钻：`GET /api/lineage/{run_id}/hour/{h}`（h = 1..24）。
5. 安全状态：`GET /api/reports/shadow-safety` 用于顶部告警条。

详见 [`docs/api/FRONTEND_API_EXAMPLES.md`](api/FRONTEND_API_EXAMPLES.md)。
