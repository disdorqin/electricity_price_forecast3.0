"""
ingest_model_predictions.py — Ingest OUR model predictions into the DB ledger.

Reads prediction CSVs (standard layout: columns ``hour_business`` and
``y_pred`` / ``pred_price``) and writes them into ``efm_predictions`` with
``stage=dayahead_raw_model`` (day-ahead) or ``realtime_raw_model`` (real-time)
for each configured model. This is the DB-import step that makes the
production circuit's model loader find real predictions.

Usage
-----
  # single model, single date
  python tools/ingest_model_predictions.py --db-url $EFM3_DB_URL \
      --task dayahead --model cfg05 --date 2026-02-24 --csv path/to/cfg05.csv

  # batch: <batch-dir>/<model>/<date>.csv for many models/dates
  python tools/ingest_model_predictions.py --db-url $EFM3_DB_URL \
      --batch-dir predictions_store --date 2026-02-24

The ``run_id`` used for raw predictions is ``efm3_raw_<date>_<task>`` so that
re-ingestion is idempotent (ON DUPLICATE KEY on the batch hash).
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ingest_model_predictions")


def _read_pred_csv(path: Path) -> list[tuple[int, float]]:
    """Return [(hour_business, pred_price), ...] from a prediction CSV."""
    out: list[tuple[int, float]] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"empty CSV: {path}")
        # Detect value + hour columns.
        lower = {c.lower(): c for c in reader.fieldnames}
        hour_col = lower.get("hour_business") or lower.get("hour") or lower.get("hb")
        val_col = lower.get("y_pred") or lower.get("pred_price") or lower.get("pred")
        if not hour_col or not val_col:
            raise ValueError(
                f"CSV {path} must contain hour_business/hour and y_pred/pred_price; "
                f"got columns {reader.fieldnames}"
            )
        for i, row in enumerate(reader, start=1):
            try:
                hb = int(float(row[hour_col]))
                val = float(row[val_col])
            except (TypeError, ValueError):
                logger.warning("  skip bad row %d in %s: %s", i, path.name, row)
                continue
            out.append((hb, val))
    return out


def ingest_file(db_url: str, task: str, model: str, target_date: str,
                csv_path: Path, model_version: str = "v1") -> int:
    from common.db.connection import DbConnectionManager
    from common.db.repositories import create_run
    from common.db.models import RunRecord
    from pipelines.production_circuit.contracts import (
        CircuitStage, CircuitTask,
    )
    from pipelines.production_circuit.step_recorder import write_stage_predictions

    ctask = CircuitTask.DAYAHEAD if task == "dayahead" else CircuitTask.REALTIME
    stage = (
        CircuitStage.DAYAHEAD_RAW_MODEL
        if task == "dayahead" else CircuitStage.REALTIME_RAW_MODEL
    )
    pairs = _read_pred_csv(csv_path)
    if not pairs:
        logger.warning("  no valid rows in %s — skipped", csv_path)
        return 0
    # Sort by hour for stable ordering.
    pairs.sort(key=lambda x: x[0])
    rows = [{
        "hour_business": hb,
        "pred_price": val,
        "model_name": model,
        "model_version": model_version,
        "is_shadow": False,
        "is_selected": False,
        "selected_reason": "ingested model raw output",
        "quality_flags": ["model_raw", "ingested"],
    } for hb, val in pairs]

    run_id = f"efm3_raw_{target_date}_{task}"
    db_mgr = DbConnectionManager(db_url=db_url)

    # The ledger enforces a FK: efm_predictions.run_id -> efm_runs.run_id.
    # Create (idempotent) the parent run row so the raw-model ingestion can
    # land. ON DUPLICATE KEY makes re-ingestion safe.
    try:
        create_run(db_mgr.new_connection(), RunRecord(
            run_id=run_id, target_date=target_date,
            chain_version="ingest_raw_model", mode="dry_run",
            status="COMPLETE", delivery_status="NOT_ATTEMPTED",
        ))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("  could not create parent efm_runs row for %s: %s",
                       run_id, exc)
    conn = db_mgr.new_connection()
    try:
        ids = write_stage_predictions(
            conn, run_id, target_date, ctask, stage, rows,
            source_step="ingest_model_predictions", is_final_candidate=False,
        )
        logger.info("  ingested %d rows for %s/%s (%s)", len(ids), task, model, target_date)
        return len(ids)
    finally:
        conn.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-url", required=True)
    p.add_argument("--task", choices=["dayahead", "realtime"], required=True)
    p.add_argument("--model", help="model name (single-file mode)")
    p.add_argument("--date", help="target date YYYY-MM-DD")
    p.add_argument("--csv", type=Path, help="single prediction CSV")
    p.add_argument("--batch-dir", type=Path,
                   help="layout: <batch-dir>/<model>/<date>.csv")
    p.add_argument("--model-version", default="v1")
    args = p.parse_args(argv)

    total = 0
    if args.batch_dir:
        if not args.date:
            logger.error("--date is required in batch mode")
            return 2
        if not args.batch_dir.exists():
            logger.error("batch-dir not found: %s", args.batch_dir)
            return 2
        for model_dir in sorted(args.batch_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            csv_path = model_dir / f"{args.date}.csv"
            if not csv_path.exists():
                logger.warning("  missing %s for model %s — skipped",
                               csv_path, model_dir.name)
                continue
            logger.info("batch: %s / %s", args.task, model_dir.name)
            total += ingest_file(args.db_url, args.task, model_dir.name,
                                 args.date, csv_path, args.model_version)
    else:
        if not (args.model and args.date and args.csv):
            logger.error("single mode requires --model --date --csv")
            return 2
        total += ingest_file(args.db_url, args.task, args.model,
                             args.date, args.csv, args.model_version)

    logger.info("DONE: ingested %d rows total", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
