"""
Migrate the EFM3 MySQL ledger to the 3NF schema.

Wipes ALL existing data (per project decision: full rebuild) and recreates the
normalized schema defined in ``db/schema_3nf.sql``. Dimension rows are created
lazily by the data-access layer (common/db/dimensions.py) at write time, so no
explicit seeding is required for the pipeline to run.

Usage:
    python db/migrate_to_3nf.py            # uses EFM3_DB_URL env var
    python db/migrate_to_3nf.py --db-url <url>
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("efm3.migrate")


def _split_sql(sql: str) -> list[str]:
    """Split a SQL script into statements, ignoring semicolons inside comments.

    A naive ``sql.split(';')`` breaks on semicolons that appear inside ``--``
    line comments or ``/* */`` block comments (the 3NF schema has both). We
    strip comments before splitting so DDL is parsed correctly.
    """
    # Remove block comments /* ... */
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    # Remove line comments -- ... (to end of line)
    sql = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in sql.split(";") if s.strip()]

_OLD_TABLES = [
    # run-children (must be dropped before efm_runs)
    "efm_pipeline_steps", "efm_prediction_lineage_edges", "efm_repair_decisions",
    "efm_fusion_candidates", "efm_fusion_decisions", "efm_postflight_checks",
    "efm_delivery_finals", "efm_delivery_outputs", "efm_task_finals",
    "efm_predictions", "efm_feature_snapshots", "efm_artifacts", "efm_run_events",
    "efm_prediction_batches",
    # registry / dimensions (old)
    "efm_model_registry",
    # ingestion
    "efm_dataset_versions", "efm_market_data_hourly", "efm_source_files",
    "efm_data_sources", "efm_data_update_runs",
    # actuals / metrics
    "efm_actual_prices", "efm_metric_runs",
    # run registry last
    "efm_runs",
]

_DIM_TABLES = [
    "efm_dim_stage", "efm_dim_model", "efm_dim_policy", "efm_dim_check",
    "efm_dim_output", "efm_dim_artifact", "efm_dim_event", "efm_dim_datatype",
    "efm_dim_relation", "efm_dim_rule", "efm_dim_repairstage", "efm_dim_market",
    "efm_dim_unit", "efm_dim_sourcetype", "efm_dim_importstatus", "efm_dim_step",
]


def _db_url(cli: str | None) -> str:
    return cli or os.environ.get("EFM3_DB_URL", "")


def migrate(db_url: str) -> None:
    from common.db.connection import DbConnectionManager

    if not db_url:
        raise SystemExit("EFM3_DB_URL not set (or pass --db-url)")

    mgr = DbConnectionManager(db_url=db_url)
    conn = mgr.new_connection()
    try:
        cur = conn.cursor()
        logger.info("Disabling FK checks and dropping old tables...")
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        for t in _OLD_TABLES + _DIM_TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {t}")
        conn.commit()
        logger.info("Dropped %d tables.", len(_OLD_TABLES) + len(_DIM_TABLES))

        # Recreate from the 3NF schema file.
        sql_path = Path(__file__).resolve().parent / "schema_3nf.sql"
        statements = _split_sql(sql_path.read_text(encoding="utf-8"))
        for stmt in statements:
            cur.execute(stmt)
        conn.commit()
        logger.info("Created 3NF schema (%d statements executed).", len(statements))

        cur.execute("SET FOREIGN_KEY_CHECKS=1")
        # sanity: count tables
        cur.execute("SHOW TABLES")
        tables = [r[0] for r in cur.fetchall()]
        logger.info("Live tables: %d", len(tables))
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", default=None)
    args = ap.parse_args()
    migrate(_db_url(args.db_url))


if __name__ == "__main__":
    main()
