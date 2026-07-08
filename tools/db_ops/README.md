# EFM3 DB Ops Tools

Tools for managing the MySQL ledger.

| Tool | Description |
|------|-------------|
| `db_health_check.py` | Check connection, tables, recent runs, failed runs |
| `db_run_summary.py` | Show run details by run_id or target_date (JSON/text) |
| `db_cleanup_dry_runs.py` | Preview or delete dry-run data |
| `db_export_latest_report.py` | Export latest run report (no raw data) |
| `db_verify_shadow_safety.py` | Check shadow contamination |

All tools support `--db-url` or `EFM3_DB_URL` env var.
Never hardcode passwords.
