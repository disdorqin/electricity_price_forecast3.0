# EFM3 本地配置指南 (Local Config Guide)

本指南说明如何在本机用最小改动跑起 EFM3 3.0 的 MySQL ledger、后端控制面与一键链路。**所有密码均为占位符，真实密码只存在于本地 `.env.local`（已被 `.gitignore` 忽略），永不入库。**

---

## 1. 环境变量模板

| 文件 | 用途 | 是否入库 |
|------|------|----------|
| `.env.example` | 可提交模板，`EFM3_DB_URL` 用 `***` 占位 | ✅ |
| `.env.local.example` | 本地密钥模板，`YOUR_PASSWORD` 占位 | ✅ |
| `.env.local` | 复制自 `.env.local.example，填真实值` | ❌（gitignore） |

### 关键字段

```bash
# 主 ledger 连接串
EFM3_DB_URL=mysql+pymysql://root:YOUR_PASSWORD@127.0.0.1:3306/efm3
# 非 localhost 访问 API 所需的 key
EFM3_API_KEY=local-dev-key
# ops 端点默认关闭；仅在主动跑链路时打开
EFM3_OPS_ENABLED=true
# 本地数据根
EFM3_DATA_ROOT=data
EFM2_5_ROOT=D:/path/to/electricity_forecast_model2.5
```

### ⚠️ 密码含 `#` 的处理

URL 中密码若含 `#`，必须 URL 编码为 `%23`，否则 `#` 之后会被当作 URL fragment 截断：

```bash
# 错误：# 后的 23 被截断
EFM3_DB_URL=mysql+pymysql://root:SuperSecret123#@127.0.0.1:3306/efm3
# 正确
EFM3_DB_URL=mysql+pymysql://root:SuperSecret123%23@127.0.0.1:3306/efm3
```

> 注意：`common.db.connection.DbConnectionManager._parse_url` **不会**对 `%23` 做 URL 解码——它要求 URL 中就是原始的 `%23` 形式。Docker 启动时若 shell 传递含 `#` 的密码，请用单引号包裹：`MYSQL_ROOT_PASSWORD='SuperSecret123#'`。

---

## 2. 本地 MySQL（Docker）

`docker-compose.mysql.yml` 启动 MySQL 8.0，容器名 `efm3-mysql`，端口 `3306`，自动建库 `efm3`，带 healthcheck。

```bash
# 用单引号包裹含 # 的密码
MYSQL_ROOT_PASSWORD='SuperSecret123#' docker compose -f docker-compose.mysql.yml up -d
```

密码从 `MYSQL_ROOT_PASSWORD` 环境变量读取，**不写死在 compose 文件里**；缺失时 compose 直接报错退出（`${MYSQL_ROOT_PASSWORD:?...}`）。

---

## 3. 初始化 ledger

```bash
# 一键：先起 docker（可选），再等 MySQL 就绪，再 --init-db
python scripts/bootstrap_local_db.py --docker

# 仅初始化（MySQL 已自行启动）
python scripts/bootstrap_local_db.py
```

脚本逻辑（`scripts/bootstrap_local_db.py`）：
1. `--docker` 时执行 `docker compose -f docker-compose.mysql.yml up -d`；
2. 用 socket 探测 `127.0.0.1:3306` 直至可达；
3. 调用现有 `python main.py --init-db --db-url $EFM3_DB_URL` 应用迁移；
4. **全程不打印明文密码**（日志走 `redact_db_url` 脱敏）。

---

## 4. 一键 dry_run / shadow

```bash
# DB-backed dry_run（演练，不写 submission、不改交付状态）
python scripts/run_local_dry_run.py 2026-07-03
python scripts/run_local_dry_run.py 2026-07-03 --chain seasonal_da_router --no-update-data

# Shadow 监控（与主线并行，永不进入最终交付）
python scripts/run_local_shadow.py 2026-07-03 --with-selector-shadow
```

两个脚本都是对现有 CLI（`main.py <date> --use-db --mode ... --db-url ...`）的**薄封装**，不发明新命令；DB URL 同样不在 stdout 明文出现。

---

## 5. 启动后端控制面

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
cd ..
set EFM3_DB_URL=mysql+pymysql://root:YOUR_PASSWORD%23@127.0.0.1:3306/efm3
set EFM3_OPS_ENABLED=false      # 默认关闭，安全
uvicorn backend.app.main:app --reload --port 8000
```

后端只读 ledger / 触发白名单 ops，不依赖前端 / npm / node。生产环境如需打开 ops，请把 `EFM3_OPS_ENABLED=true` 并配置 `EFM3_API_KEY`，危险动作仍需 `confirm=true` + `reason`。

---

## 6. 配置清单（本仓库提供的免密模板）

| 文件 | 内容 |
|------|------|
| `.env.example` | 可提交 DB URL 模板（`***` 占位）+ 注释 |
| `.env.local.example` | 本地密钥模板（`YOUR_PASSWORD` 占位） |
| `configs/local.mysql.yaml` | `db.url_env: EFM3_DB_URL`、`formal_requires_db: true`、`connect_timeout: 10`、`pool_size: 5`、`redact_url_in_logs: true` |
| `configs/local.paths.yaml` | 数据根 / 2.5 根 / outputs / dry_run / shadow 路径（均从 env 解析） |
| `docker-compose.mysql.yml` | 本地 MySQL 8.0 容器（密码走 env） |
| `scripts/bootstrap_local_db.py` | 起 docker + 等就绪 + `--init-db` |
| `scripts/run_local_dry_run.py` | 本地 DB-backed dry_run 封装 |
| `scripts/run_local_shadow.py` | 本地 shadow 监控封装 |

---

## 7. 安全不变量

- `.env.local` 已在 `.gitignore`，真实密码永不入库。
- 文档 / 测试 / 模板中**不出现任何真实密码**（仅 `SuperSecret123#` 这类明显占位符）。
- 所有脚本日志对 DB URL 脱敏（`backend/app/utils/redaction.py`）。
- `EFM3_OPS_ENABLED` 默认 `false`，任何 `/api/ops/*` 默认返回 403。
