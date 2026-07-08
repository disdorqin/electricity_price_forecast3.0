# External Database Integration Guide

This document explains how to connect EFM3 to an external MySQL database, how
the connection is configured, and how the frontend accesses the ledger without
direct database credentials.

---

## 1. Connection Model

```
Frontend ───HTTP/WS──→ Backend API (FastAPI) ───pymysql──→ MySQL
```

The frontend **never** receives the database URL or credentials. It talks only
to the backend API (port 8000, via OpenAPI contract). The backend connects to
MySQL using the `EFM3_DB_URL` environment variable or `--db-url` CLI argument.

---

## 2. EFM3_DB_URL Format

```
mysql+pymysql://USER:PASSWORD@HOST:PORT/DATABASE
```

Examples:

| Scenario | URL |
| -------- | --- |
| Local Docker MySQL | `mysql+pymysql://root:password@127.0.0.1:3306/efm3` |
| External MySQL | `mysql+pymysql://deploy:p%40ss@db.example.com:3306/efm3` |
| Password with `#` | `mysql+pymysql://root:pass%23word@127.0.0.1:3306/efm3` |

Requirements:
- The URL is read **only** from `EFM3_DB_URL` env variable or `--db-url` CLI flag.
- No host/user/password is ever hardcoded in the codebase.
- Passwords containing `#` must be URL-encoded as `%23`.

### URL Encoding for Special Characters

When a password or username contains special characters, they must be
URL-encoded in the connection string:

| Character | Encoded |
| --------- | ------- |
| `#`       | `%23`   |
| `@`       | `%40`   |
| `:`       | `%3A`   |
| `/`       | `%2F`   |
| `?`       | `%3F`   |
| `&`       | `%26`   |

Example: a password of `p@ss#1:2/3?4&5` would be written as
`mysql+pymysql://user:p%40ss%231%3A2%2F3%3F4%265@host:3306/db`.

The backend's `_parse_url` method on `common/db/connection.py` fully URL-decodes
the host, user, password, and database components before passing them to pymysql,
so encoded passwords work correctly.

---

## 3. Setting the DB URL

### 3.1 Environment Variable (recommended for deployment)

```bash
export EFM3_DB_URL="mysql+pymysql://deploy:password@db.example.com:3306/efm3"
```

### 3.2 CLI Flag

```bash
python main.py 2026-02-01 --use-db --db-url "mysql+pymysql://..." --mode dry_run
```

### 3.3 Local Config File (development only)

```bash
# .env.local (gitignored)
EFM3_DB_URL="mysql+pymysql://root:pass@127.0.0.1:3306/efm3"
```

The backend API auto-loads `.env.local` via pydantic-settings (see `backend/app/config.py`).

---

## 4. Required Tables

The schema is defined in `db/schema.sql` and `db/migrations/`. After running
the migration, the following 15 tables must exist:

| # | Table | Purpose |
|---|-------|---------|
| 1 | `efm_runs` | Run metadata (status, delivery, exit code) |
| 2 | `efm_predictions` | All prediction rows (da_anchor, final_selected, shadow, …) |
| 3 | `efm_fusion_decisions` | Winter/non-winter router decisions |
| 4 | `efm_postflight_checks` | Per-run postflight check results |
| 5 | `efm_delivery_outputs` | Output file paths and hashes |
| 6 | `efm_run_events` | Step-level audit trail |
| 7 | `efm_data_update_runs` | Data sync run records |
| 8 | `efm_dataset_versions` | Dataset version tracking |
| 9 | `efm_data_sources` | Configured data sources |
| 10 | `efm_source_files` | Data source file manifests |
| 11 | `efm_feature_snapshots` | Feature snapshot diagnostics |
| 12 | `efm_actual_prices` | Actual (verified) prices |
| 13 | `efm_market_data_hourly` | Hourly market data |
| 14 | `efm_model_registry` | Registered forecast models |
| 15 | `efm_artifacts` | Pipeline artifacts |

Plus 8 dashboard views (`db/migrations/003_dashboard_views.sql`).

---

## 5. Running Migration

If the target database is empty or needs an upgrade:

```bash
python main.py --init-db --db-url "mysql+pymysql://..."
```

This runs `db/schema.sql` and all scripts in `db/migrations/` (idempotent —
uses `CREATE TABLE IF NOT EXISTS`).

---

## 6. Verifying Schema

### Quick health check

```bash
python tools/db_ops/db_health_check.py --db-url "mysql+pymysql://..."
```

### List tables

```sql
SHOW TABLES;
```

### Expected output for a healthy database

```
Tables (15): efm_actual_prices, efm_artifacts, efm_data_sources,
efm_data_update_runs, efm_dataset_versions, efm_delivery_outputs,
efm_feature_snapshots, efm_fusion_decisions, efm_market_data_hourly,
efm_model_registry, efm_postflight_checks, efm_predictions,
efm_run_events, efm_runs, efm_source_files
```

---

## 7. Switching Databases

Change the `EFM3_DB_URL` environment variable to point at a different MySQL
instance. The DB-ledger chain, backend API, and all CLI tools read the URL from
this single source — no code changes are needed.

Example workflow for switching between local dev and an external staging DB:

```bash
# Local development
export EFM3_DB_URL="mysql+pymysql://root:localpass@127.0.0.1:3306/efm3"

# External staging
export EFM3_DB_URL="mysql+pymysql://deploy:stagingpass@staging.example.com:3306/efm3"
```

---

## 8. Security & Redaction

- The backend API logs the DB URL as `mysql+pymysql://root:****@host:port/db`.
- Health endpoints return `db_url_prefix` as `host:port` **only**, never the
  full URL or credentials.
- The frontend calls the backend API and **never** receives the database URL.
  API endpoints return prediction data, lineage, and run summaries without
  exposing connection details.
- `.env.local` is gitignored and never committed.

---

## 9. Frontend Integration

The frontend should use `docs/api/openapi.json` (generated by
`scripts/export_openapi.py`) to discover available endpoints:

- `GET /api/health` — service status
- `GET /api/health/db` — DB connectivity
- `GET /api/runs` — run list
- `GET /api/runs/{run_id}/summary` — run summary
- `GET /api/runs/{run_id}/predictions` — prediction rows
- `GET /api/runs/{run_id}/predictions/selected` — final selected predictions
- `GET /api/lineage/{run_id}` — full lineage tree
- `GET /api/lineage/{run_id}/hour/{hour}` — per-hour lineage
- `GET /api/datasets` — dataset manifests
- `GET /api/data_sources` — configured sources
- `GET /api/postflight/{run_id}` — postflight results
- `GET /api/reports/shadow-safety` — shadow safety summary
- `GET /api/reports/delivery/{run_id}` — delivery report

The frontend does NOT need the DB URL, credentials, or any direct database
access.
