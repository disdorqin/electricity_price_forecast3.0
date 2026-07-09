# EFM3 Backend / Forecast Ledger

EFM3 is the **backend delivery repository** for the Shandong electricity
spot-price forecasting system. It runs the forecast chain, stores every
prediction into a MySQL ledger, backfills source data, exposes a FastAPI
control-plane, and provides an OpenAPI contract for the frontend.

> This repository is the **backend / data / pipeline** layer. It is **not** a
> frontend, a dashboard, a model-training lab, or a raw-data warehouse.

---

## What this repo does

- **Run forecast chain** — `seasonal_da_router` / `full_chain_orchestrator`
  produce day-ahead (DA) anchor forecasts and fusion decisions per business day.
- **Store every prediction into MySQL ledger** — all candidates, the selected
  final, and the fusion decision are written through `common/prediction_store.py`
  into `efm_predictions` / `efm_fusion_decisions` (no silent CSV-only writes).
- **Backfill Shandong PMOS source data** — `tools/db_ops/backfill_shandong_pmos_csv.py`
  ingests `data/shandong_pmos_hourly.csv` (日前电价 → `da_price`, 实时电价 →
  `rt_price`) into `efm_market_data_hourly` and `efm_actual_prices`.
- **Provide FastAPI backend API** — `/api/health`, `/api/runs`,
  `/api/runs/{id}/predictions/selected`, `/api/lineage/...`, `/api/datasets`,
  `/api/reports/shadow-safety`, etc. (see `docs/api/openapi.json`).
- **Provide OpenAPI contract for frontend** — `docs/api/openapi.json` is the
  single source of truth for what the frontend may call.
- **Run `formal_sim` without producing official submission** — strict guards
  ensure `mode=formal_sim` never writes a `submission_ready.csv` to delivery.

## What this repo does NOT do

- **No frontend** — no React/Vue/HTML UI is shipped here.
- **No raw data committed** — source CSVs (`data/`) and large outputs
  (`outputs/`) are git-ignored.
- **No production submission by default** — `formal_sim` is evaluation-only;
  real submission export is gated behind explicit flags.
- **No champion replacement by default** — router/fusion weights are not
  auto-promoted; this repo is a stable delivery surface.
- **No password in the tree** — DB credentials live only in `.env.local`
  (git-ignored) or the `EFM3_DB_URL` env var (URL-encoded `#` → `%23`).

---

## Architecture

| Path | Role |
| --- | --- |
| `backend/` | FastAPI control-plane (app, routers, schemas, services, security) |
| `common/db/` | MySQL connection manager + schema init (`init_schema`) |
| `common/data_ingestion/` | Source importers (PMOS CSV → ledger tables) |
| `pipelines/` | `full_chain_orchestrator`, `seasonal_da_router`, fallback policy |
| `db/` | SQL schema + migrations (ledger tables, dashboard views) |
| `scripts/` | `run_monthly_db_dry_run.py` (dry_run / shadow / formal / formal_sim) |
| `tools/db_ops/` | `inspect` / `backfill` PMOS CSV, `db_yearly_metrics`, audit, summary |
| `docs/api/` | `openapi.json` + frontend API examples |
| `docs/experiments/e2e/` | end-to-end simulation & metrics reports |

### Data model (ledger)

- `efm_runs` — one row per executed run (mode, status, delivery_status, exit_code).
- `efm_predictions` — every candidate + `stage='final_selected'` rows
  (`task ENUM`, `hour_business 1..24`, `pred_price`, `is_selected`,
  `selected_reason`).
- `efm_fusion_decisions` — router/fusion choice per hour.
- `efm_market_data_hourly` — `data_type IN ('da_price','rt_price')`.
- `efm_actual_prices` — `da_anchor` (DA clearing) + `rt_actual` (real-time actual).
- `efm_postflight_checks`, `efm_delivery_outputs`, `efm_lineage_*`,
  `efm_data_sources`, `efm_source_files`, `efm_feature_snapshots`,
  `efm_dataset_versions`, `efm_shadow_*` — audit / lineage / shadow monitoring.

---

## Quick Start

> All commands use `EFM3_DB_URL` (URL-encode `#` as `%23`). PowerShell-style
> `$env:EFM3_DB_URL` is shown; in bash use `$EFM3_DB_URL`.

### 1. Create `.env.local`

```bash
# .env.local  (git-ignored — never commit)
EFM3_DB_URL="mysql+pymysql://root:YOUR_PASSWORD%23@127.0.0.1:3306/efm3"
```

### 2. Start Docker MySQL

```bash
docker compose -f docker-compose.mysql.yml up -d
```

### 3. Init DB

```bash
python main.py --init-db --db-url $env:EFM3_DB_URL
```

### 4. Backfill PMOS CSV

```bash
python tools/db_ops/backfill_shandong_pmos_csv.py \
  --csv-path data/shandong_pmos_hourly.csv \
  --start-date 2026-01-01 --end-date 2026-06-30 \
  --db-url $env:EFM3_DB_URL --encoding gbk --commit
```

### 5. Run `formal_sim`

```bash
python scripts/run_monthly_db_dry_run.py \
  --start-date 2026-01-01 --end-date 2026-06-30 \
  --db-url $env:EFM3_DB_URL \
  --chain seasonal_da_router --mode formal_sim \
  --continue-on-fail --report-dir outputs/db_jan_jun_formal_sim/2026
```

### 6. Compute metrics

```bash
python tools/db_ops/db_yearly_metrics.py \
  --start-date 2026-01-01 --end-date 2026-06-30 \
  --db-url $env:EFM3_DB_URL \
  --output-md  outputs/db_jan_jun_formal_sim/2026/metrics.md \
  --output-json outputs/db_jan_jun_formal_sim/2026/metrics.json
```

### 7. Start API

```bash
uvicorn backend.app.main:app --reload --port 8000
```

Then open the contract: [`docs/api/openapi.json`](docs/api/openapi.json), and
the frontend handoff guide: [`docs/FRONTEND_HANDOFF.md`](docs/FRONTEND_HANDOFF.md).

---

## Latest verified result (Jan–Jun 2026)

| Item | Result |
| --- | --- |
| `formal_sim` days | **181 / 181 PASS** |
| `final_selected` | 24 rows/day |
| `fusion_decisions` | 24 rows/day |
| `da_anchor` | 24 rows/day |
| postflight | PASS (8/8) |
| formal guard | PASS (no real submission) |
| delivery outputs | 0 |
| API smoke | 7/7 endpoints HTTP 200 |
| SMAPE | **49.70%** |
| MAE | **92.83** |
| RMSE | **143.90** |
| WMAPE | **30.92%** |
| Q2 vs Q1 | Q2 24.34% < Q1 37.58% |

Benchmark definition: day-ahead forecast (`final_selected`) vs real-time actual
(`rt_actual`). This is a baseline/DA-anchor benchmark, not a final champion
comparison. Full 14-section report:
[`docs/experiments/e2e/JAN_JUN_2026_BACKFILLED_FORMAL_SIM_METRICS_REPORT.md`](docs/experiments/e2e/JAN_JUN_2026_BACKFILLED_FORMAL_SIM_METRICS_REPORT.md).

Release seal: [`docs/RELEASE_SEAL_BACKEND_RC.md`](docs/RELEASE_SEAL_BACKEND_RC.md).

---

## Repository boundaries (what this repo is / is not)

**This repo IS:** backend API · MySQL ledger · data backfill/ingestion ·
forecast-chain executor · `formal_sim`/`dry_run` tooling · DB audit/metrics ·
OpenAPI contract.

**This repo is NOT:** frontend repo · dashboard/large-screen repo · model-training
experiment repo · raw-data warehouse · password/config repo.
