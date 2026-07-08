# EFM3 API-only Control Plane — 实现计划与现状 (API-Only Backend Plan)

> 本文档描述从 PR #13（`agent/backend-frontend-control-plane`）中收束出的 **API-only 后端控制面** 的设计与实现状态。本次交付**不合并前端**，前端将稍后通过 OpenAPI 契约接入。

---

## 1. 目标 (Goals)

1. 后端 API 稳定、可独立启动，供前端后续调用。
2. 本次**不**合并 React 前端（`frontend/` 留在 PR #13 演示分支，不并入 main）。
3. 3.0 主链路（一键完整链路）像 2.5 一样一键可跑。
4. 全部预测结果自动写入 MySQL ledger。
5. 本地 Docker MySQL 配置被封装为开箱即用模板。
6. `dry_run` / `formal` / `shadow` 兜底规则显式、可测试。
7. 不破坏旧命令；绝不泄露数据库密码。

---

## 2. 分支策略 (Branch Strategy)

| 分支 | 角色 | 状态 |
|------|------|------|
| `main` | 主分支 | 已合并 PR #12（修复了 #12 中的真实密码泄露） |
| PR #12 `agent/final-archive-release-notes` | 旧命令 / DB 标志默认关 / 冠军不替换 | ✅ 已 merge 到 main |
| PR #13 `agent/backend-frontend-control-plane` | 完整平台演示（含前端） | ⏸ 保留为演示分支，**不合并、不关闭** |
| `agent/api-only-control-plane-config-hardening` | **本 PR 分支** | 基于 main，仅抽取后端 / 配置 / 测试 / 文档 |

**抽取边界（来自 PR #13）：**
- ✅ 抽取：`backend/`、`db/migrations/003_dashboard_views.sql`、后端测试、`docs/BACKEND_API_DESIGN.md` 等后端文档、脱敏 / ops 安全代码。
- ❌ 不抽取：`frontend/`、`node_modules/`、`dist/`、React / Vite / npm 相关文件。

---

## 3. 后端目录 (backend/)

```
backend/
├── README.md                  # 后端独立启动说明
├── requirements.txt           # fastapi / uvicorn / pydantic-settings / httpx / pytest（无 pandas）
├── app/
│   ├── main.py                # FastAPI 入口，挂载 9 个 router
│   ├── config.py              # pydantic-settings, env_prefix="EFM3_"
│   ├── db.py                  # DbConnectionManager 封装（只读 ledger，不写业务表）
│   ├── security.py            # require_access / require_ops / assert_confirm
│   ├── ops_dispatch.py        # 白名单动作 → main.py 子进程分发
│   ├── routers/               # 9 个 router（见 §4）
│   ├── schemas/               # pydantic 响应模型
│   ├── services/              # 6 个 service（dataset/lineage/ops/prediction/report/base）
│   └── utils/
│       ├── redaction.py       # 日志脱敏（DB URL / 密码）
│       └── subprocess_runner.py  # 严格命令白名单，shell=False
```

**后端不依赖前端 / npm / node**；对旧 CLI 行为零改动（所有 ops 动作最终仍调用 `main.py`）。

---

## 4. API 契约 (API Contract, §三)

9 个 router，前缀与标签如下（完整机器可读契约见 `docs/api/openapi.json` 与 `docs/api/API_CONTRACT.md`）：

| Router | 前缀 | 标签 |
|--------|------|------|
| health | `/api/health` | health |
| runs | `/api/runs` | runs |
| predictions | `/api/runs` | predictions |
| postflight | `/api` | postflight |
| datasets | `/api` | datasets |
| data_sources | `/api` | data_sources |
| ops | `/api/ops` | ops |
| reports | `/api/reports` | reports |
| lineage | `/api` | lineage |

- 只读端点（`health`/`runs`/`predictions`/`postflight`/`datasets`/`data_sources`/`reports`/`lineage`）依赖 `require_access`（配置了 `EFM3_API_KEY` 时，非 localhost 必须带 `X-API-Key`）。
- 写操作端点（`/api/ops/*`）依赖 `require_ops`：**`EFM3_OPS_ENABLED=false`（默认）时一律返回 403**，无 localhost 豁免。

生成命令：
```bash
backend/.venv/Scripts/python.exe scripts/export_openapi.py
```

---

## 5. 本地配置 (Local Config, §四)

免密码模板与一键脚本：
- `.env.example` / `.env.local.example` — 占位密码 `***`，说明 `#`→`%23` 编码。
- `configs/local.mysql.yaml` / `configs/local.paths.yaml` — 本地 DB / 数据根配置。
- `docker-compose.mysql.yml` — 本地 MySQL 8.0 容器（`MYSQL_ROOT_PASSWORD` 从环境变量读取，不写死）。
- `scripts/bootstrap_local_db.py` — `--docker` 等 MySQL 就绪后执行 `--init-db`。
- `scripts/run_local_dry_run.py` / `scripts/run_local_shadow.py` — 本地 dry_run / shadow 一键封装。
- `.env.local` 已加入 `.gitignore`，真实密码永不入库。

---

## 6. 兜底矩阵 (Fallback Matrix, §五)

见 `docs/CHAIN_FALLBACK_MATRIX.md` 与 `common/fallback_policy.py`。10 行矩阵覆盖全部失败模式；正式链路失败一律 exit 1，dry_run 永不阻断退出。

---

## 7. 自动入库 (Auto DB Storage, §六)

见 `docs/PREDICTION_STORAGE_CONTRACT.md`。所有预测经 `PredictionStore` 落库，导出器强制读 store，shadow 永不污染交付。

---

## 8. Ops 安全 (Ops Safety, §七)

- ops 默认禁用（403）。
- 危险动作（`export-submission` / `run-formal`）需 `confirm=true` **且** 非空 `reason`。
- 命令白名单（`ALLOWED_ACTIONS`）+ `shell=False` + 超时 + 脱敏日志，无任意命令执行。
- 任何端点绝不返回 DB 密码。

详见 `docs/OPS_CONSOLE_SAFETY.md`、`docs/FORECAST_LEDGER_LINEAGE.md`、`docs/BACKEND_API_DESIGN.md`。

---

## 9. 交付状态 (Delivery Status)

| 项 | 状态 |
|----|------|
| 后端 API-only 抽取 | ✅ |
| API 契约 + OpenAPI 导出 | ✅ |
| 本地配置模板 + 脚本 | ✅ |
| 兜底矩阵 + 代码 | ✅ |
| 自动入库契约 + 测试 | ✅ |
| Ops 安全 + 测试 | ✅ |
| 文档（本文件 + 5 篇） | ✅ |
| 测试全绿 | ⏳ 见最终报告 |
| PR #14 | ⏳ 见最终报告 |

**结论（推荐值）**：`API_ONLY_RECOMMENDATION = READY_FOR_FRONTEND_INTEGRATION`（详见 §十二最终报告）。
