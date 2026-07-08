"""
DB-based exporter for EFM3 3.0.

Reads selected predictions from the database (via PredictionStore) and writes
``submission_ready.csv`` for delivery to the downstream settlement system.

Paths
-----
* Formal (production):  ``{output_dir}/final/submission_ready.csv``
* Dry-run (test):       ``{output_dir}/db_dry_run/{target_date}/submission_ready.csv``
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pandas as pd

from common.db.connection import DbConnectionManager
from common.db.models import DeliveryOutputRecord
from common.db.repositories import insert_delivery_output

logger = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────────────


def hour_to_time_string(h: int) -> str:
    """Map business hour (1-24) to ``HH:00`` string.

    Hour 24 is mapped to ``00:00`` (representing midnight of the next day),
    which matches ISO 8601 convention for end-of-day.
    """
    if h == 24:
        return "00:00"
    return f"{h:02d}:00"


def _period_label(hour_business: int) -> str:
    """Map hour to settlement period: 1_8, 9_16, or 17_24."""
    if 1 <= hour_business <= 8:
        return "1_8"
    if 9 <= hour_business <= 16:
        return "9_16"
    return "17_24"


def _is_selected(row: dict) -> bool:
    """Check whether a raw prediction dict is flagged as selected."""
    raw = row.get("is_selected")
    return raw in (True, 1, "1", "True", "true")


# ═══════════════════════════════════════════════════════════════════════
# Public export function
# ═══════════════════════════════════════════════════════════════════════


def export_submission_ready(
    run_id: str,
    target_date: str,
    prediction_store,
    output_dir: str,
    is_formal: bool = False,
) -> dict:
    """Read predictions from *prediction_store* and write ``submission_ready.csv``.

    Parameters
    ----------
    run_id : str
        Run identifier (e.g. ``"run_20260310_001"``).
    target_date : str
        Business date in ``YYYY-MM-DD`` format.
    prediction_store : PredictionStore
        An instance of ``MySQLPredictionStore`` or ``FilePredictionStore``.
    output_dir : str
        Root directory under which the output CSV is placed.
    is_formal : bool, default False
        If ``True`` the CSV is written to ``{output_dir}/final/submission_ready.csv``;
        otherwise written to ``{output_dir}/db_dry_run/{target_date}/submission_ready.csv``.

    Returns
    -------
    dict
        ``{"status": "ok"|"partial"|"failed",
           "output_path": str,
           "row_count": 24,
           "is_formal": bool}``
    """
    # ------------------------------------------------------------------
    # 1. Fetch selected predictions
    # ------------------------------------------------------------------

    # Primary: task='final' with is_selected=True
    selected_rows = prediction_store.read_predictions(
        run_id,
        target_date,
        task="final",
        is_selected=True,
    )

    # Fallback: if no final-selected rows exist, try any task with
    # is_selected=True (e.g. older runs that only have 'dayahead' selections).
    if not selected_rows:
        logger.info(
            "No final-selected predictions found for %s / %s; "
            "falling back to any selected prediction.",
            run_id,
            target_date,
        )
        selected_rows = prediction_store.read_predictions(
            run_id,
            target_date,
            is_selected=True,
        )

    # Build per-hour lookup for the selected (realtime) price
    selected_map: dict[int, float] = {}
    for row in selected_rows:
        try:
            hb = int(row["hour_business"])
            selected_map[hb] = float(row["pred_price"])
        except (ValueError, TypeError, KeyError):
            logger.warning("Skipping malformed selected row: %s", row)
            continue

    # ------------------------------------------------------------------
    # 2. Fetch da_anchor predictions (dayahead price)
    # ------------------------------------------------------------------

    da_rows = prediction_store.read_predictions(
        run_id,
        target_date,
        stage="da_anchor",
    )

    da_price_map: dict[int, float] = {}
    for row in da_rows:
        try:
            hb = int(row["hour_business"])
            da_price_map[hb] = float(row["pred_price"])
        except (ValueError, TypeError, KeyError):
            logger.warning("Skipping malformed da_anchor row: %s", row)
            continue

    # ------------------------------------------------------------------
    # 3. Build 24-hour DataFrame
    # ------------------------------------------------------------------

    records: list[dict] = []
    for hb in range(1, 25):
        ds = f"{target_date}T{hour_to_time_string(hb)}:00"
        period = _period_label(hb)
        da_price = da_price_map.get(hb)
        rt_price = selected_map.get(hb)

        records.append({
            "business_day": target_date,
            "ds": ds,
            "hour_business": hb,
            "period": period,
            "dayahead_price": f"{da_price:.4f}" if da_price is not None else "",
            "realtime_price": f"{rt_price:.4f}" if rt_price is not None else "",
        })

    df = pd.DataFrame(records)

    # ------------------------------------------------------------------
    # 4. Determine delivery status based on coverage
    # ------------------------------------------------------------------

    rt_filled = df["realtime_price"].astype(bool).sum()
    if rt_filled == 24:
        status = "ok"
    elif rt_filled > 0:
        status = "partial"
        logger.warning(
            "Partial realtime price coverage: %d / 24 hours filled.",
            rt_filled,
        )
    else:
        status = "failed"
        logger.error(
            "No realtime prices available for %s / %s.",
            run_id,
            target_date,
        )

    # ------------------------------------------------------------------
    # 5. Write CSV
    # ------------------------------------------------------------------

    if is_formal:
        output_path = Path(output_dir) / "final" / "submission_ready.csv"
    else:
        output_path = (
            Path(output_dir) / "db_dry_run" / target_date / "submission_ready.csv"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)

    logger.info(
        "Exported %d submission-ready rows for %s / %s -> %s (status=%s)",
        len(df),
        run_id,
        target_date,
        output_path,
        status,
    )

    # ------------------------------------------------------------------
    # 6. Record delivery output in DB
    # ------------------------------------------------------------------

    try:
        db_mgr = DbConnectionManager()
        if db_mgr.is_configured:
            conn = db_mgr.get_connection()
            file_hash = _compute_file_hash(output_path)
            record = DeliveryOutputRecord(
                run_id=run_id,
                target_date=target_date,
                output_type="submission_ready",
                output_path=str(output_path),
                file_hash=file_hash,
                row_count=len(df),
            )
            insert_delivery_output(conn, record)
            conn.close()
            logger.info(
                "Recorded delivery output in DB for %s / %s",
                run_id,
                target_date,
            )
        else:
            logger.warning(
                "DB not configured (EFM3_DB_URL unset); "
                "skipping delivery output recording."
            )
    except Exception:
        logger.exception(
            "Failed to record delivery output in DB for %s / %s",
            run_id,
            target_date,
        )

    return {
        "status": status,
        "output_path": str(output_path),
        "row_count": 24,
        "is_formal": is_formal,
    }


def _compute_file_hash(path: Path) -> str:
    """Compute a quick SHA-256 hex digest of the output file."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""
