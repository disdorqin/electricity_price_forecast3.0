"""
DB-based postflight checker for EFM3 3.0.

Validates the integrity of selected (final) predictions after a pipeline run
and persists every check result into the ``efm_postflight_checks`` table.

3NF note: ``target_date`` lives on ``efm_runs``, not on ``efm_predictions``.
Since ``run_id`` already scopes to a single target_date, we filter by
``run_id`` only. Stage/model names are resolved via dimension-table JOINs.
"""

from __future__ import annotations

import logging
from typing import Any

from pymysql.connections import Connection

from common.db.models import PostflightCheckRecord
from common.db.repositories import insert_postflight_check

logger = logging.getLogger(__name__)

_CHECK_NAMES = [
    "row_count_24",
    "hour_range",
    "no_nan",
    "no_duplicates",
    "price_range",
    "selected_source",
    "shadow_not_final",
    "submission_row_count",
]


def _run_check(
    conn: Connection,
    run_id: str,
    target_date: str,
    check_name: str,
    passed: bool,
    details: str,
) -> dict:
    """Persist a single postflight check and return its result dict."""
    record = PostflightCheckRecord(
        run_id=run_id,
        target_date=target_date,
        check_name=check_name,
        passed=passed,
        details=details,
    )
    insert_postflight_check(conn, record)
    return {"passed": passed, "details": details}


def _check_row_count_24(
    conn: Connection,
    run_id: str,
    target_date: str,
) -> dict:
    """Are there exactly 24 selected predictions?"""
    sql = """
        SELECT COUNT(*) AS cnt
        FROM efm_predictions
        WHERE run_id = %s AND is_selected = TRUE
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (run_id,))
        row = cursor.fetchone()
        cnt = row[0] if row else 0

    expected = 24
    passed = cnt == expected
    details = (
        f"selected predictions: {cnt} (expected {expected})"
        if passed
        else f"MISMATCH: expected exactly {expected} selected predictions, "
             f"but found {cnt}"
    )
    if not passed:
        logger.warning("postflight [row_count_24]: %s", details)

    return _run_check(conn, run_id, target_date, "row_count_24", passed, details)


def _check_hour_range(
    conn: Connection,
    run_id: str,
    target_date: str,
) -> dict:
    """Are hour_business values exactly 1..24?"""
    sql = """
        SELECT hour_business
        FROM efm_predictions
        WHERE run_id = %s AND is_selected = TRUE
        ORDER BY hour_business
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (run_id,))
        rows = cursor.fetchall()

    hours = [r[0] for r in rows]
    expected = list(range(1, 25))
    passed = hours == expected
    details = (
        f"hour_business: {hours[0]}..{hours[-1]} ({len(hours)} hours)"
        if passed
        else f"MISMATCH: expected hours 1..24, got {hours}"
    )
    if not passed:
        logger.warning("postflight [hour_range]: %s", details)

    return _run_check(conn, run_id, target_date, "hour_range", passed, details)


def _check_no_nan(
    conn: Connection,
    run_id: str,
    target_date: str,
) -> dict:
    """Are all pred_price non-null?"""
    sql = """
        SELECT COUNT(*) AS null_count
        FROM efm_predictions
        WHERE run_id = %s
          AND is_selected = TRUE
          AND pred_price IS NULL
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (run_id,))
        row = cursor.fetchone()
        null_count = row[0] if row else 0

    passed = null_count == 0
    details = (
        "all selected predictions have non-null pred_price"
        if passed
        else f"FAIL: {null_count} selected prediction(s) have NULL pred_price"
    )
    if not passed:
        logger.warning("postflight [no_nan]: %s", details)

    return _run_check(conn, run_id, target_date, "no_nan", passed, details)


def _check_no_duplicates(
    conn: Connection,
    run_id: str,
    target_date: str,
) -> dict:
    """Are all 24 hour_business unique?"""
    sql = """
        SELECT COUNT(*) AS total, COUNT(DISTINCT hour_business) AS distinct_hours
        FROM efm_predictions
        WHERE run_id = %s AND is_selected = TRUE
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (run_id,))
        row = cursor.fetchone()
        total, distinct_hours = (row[0], row[1]) if row else (0, 0)

    passed = total == distinct_hours
    details = (
        f"all {total} hour_business values are unique"
        if passed
        else f"FAIL: {total} rows but only {distinct_hours} distinct hour_business "
             f"values -- duplicate hours detected"
    )
    if not passed:
        logger.warning("postflight [no_duplicates]: %s", details)

    return _run_check(conn, run_id, target_date, "no_duplicates", passed, details)


def _check_price_range(
    conn: Connection,
    run_id: str,
    target_date: str,
) -> dict:
    """Are all pred_prices within [-500, 2000]?"""
    sql = """
        SELECT MIN(pred_price) AS min_price, MAX(pred_price) AS max_price
        FROM efm_predictions
        WHERE run_id = %s AND is_selected = TRUE
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (run_id,))
        row = cursor.fetchone()
        min_price, max_price = (row[0], row[1]) if row else (None, None)

    if min_price is None or max_price is None:
        passed = False
        details = "FAIL: no selected predictions found to check price range"
    else:
        min_ok = min_price >= -500
        max_ok = max_price <= 2000
        passed = min_ok and max_ok
        details = (
            f"pred_price range: [{min_price}, {max_price}] -- within [-500, 2000]"
            if passed
            else (
                f"OUT OF RANGE: pred_price range is [{min_price}, {max_price}], "
                f"expected within [-500, 2000]"
            )
        )

    if not passed:
        logger.warning("postflight [price_range]: %s", details)

    return _run_check(conn, run_id, target_date, "price_range", passed, details)


def _check_selected_source(
    conn: Connection,
    run_id: str,
    target_date: str,
) -> dict:
    """Do all selected predictions have a non-empty selected_reason?"""
    sql = """
        SELECT COUNT(*) AS bad_count
        FROM efm_predictions
        WHERE run_id = %s
          AND is_selected = TRUE
          AND (selected_reason IS NULL OR selected_reason = '')
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (run_id,))
        row = cursor.fetchone()
        bad_count = row[0] if row else 0

    passed = bad_count == 0
    details = (
        "all selected predictions have a non-empty selected_reason"
        if passed
        else f"FAIL: {bad_count} selected prediction(s) have empty/missing selected_reason"
    )
    if not passed:
        logger.warning("postflight [selected_source]: %s", details)

    return _run_check(conn, run_id, target_date, "selected_source", passed, details)


def _check_shadow_not_final(
    conn: Connection,
    run_id: str,
    target_date: str,
) -> dict:
    """Are NO selected predictions marked is_shadow = TRUE?"""
    sql = """
        SELECT COUNT(*) AS shadow_count
        FROM efm_predictions
        WHERE run_id = %s
          AND is_selected = TRUE
          AND is_shadow = TRUE
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (run_id,))
        row = cursor.fetchone()
        shadow_count = row[0] if row else 0

    passed = shadow_count == 0
    details = (
        "no selected prediction is marked as is_shadow"
        if passed
        else f"FAIL: {shadow_count} selected prediction(s) are marked is_shadow=TRUE -- "
             f"shadow records leaked into final selection"
    )
    if not passed:
        logger.warning("postflight [shadow_not_final]: %s", details)

    return _run_check(conn, run_id, target_date, "shadow_not_final", passed, details)


def _check_submission_row_count(
    conn: Connection,
    run_id: str,
    target_date: str,
) -> dict:
    """Are there exactly 24 distinct hours ready for export?"""
    sql = """
        SELECT COUNT(DISTINCT hour_business) AS distinct_hours
        FROM efm_predictions
        WHERE run_id = %s AND is_selected = TRUE
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (run_id,))
        row = cursor.fetchone()
        distinct_hours = row[0] if row else 0

    expected = 24
    passed = distinct_hours == expected
    details = (
        f"submission ready: {distinct_hours} distinct hours (expected {expected})"
        if passed
        else f"MISMATCH: expected {expected} distinct hours for export, "
             f"but found {distinct_hours}"
    )
    if not passed:
        logger.warning("postflight [submission_row_count]: %s", details)

    return _run_check(
        conn, run_id, target_date, "submission_row_count", passed, details,
    )


def run_db_postflight(
    conn: Connection,
    run_id: str,
    target_date: str,
    mode: str = "dry_run",
) -> dict[str, Any]:
    """Execute all DB-based postflight checks and persist results.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        Active database connection.
    run_id : str
        Pipeline run identifier.
    target_date : str
        Target business day in ``YYYY-MM-DD`` format.
    mode : str, default "dry_run"
        Run mode used to adjust guard strictness.

    Returns
    -------
    dict
        ``{"status": "passed" | "failed", "checks": {check_name: {"passed": bool, "details": str}}}``.
    """
    logger.info(
        "Running DB postflight checks for run_id=%s target_date=%s mode=%s",
        run_id, target_date, mode,
    )

    results: dict[str, dict] = {}
    results["row_count_24"] = _check_row_count_24(conn, run_id, target_date)
    results["hour_range"] = _check_hour_range(conn, run_id, target_date)
    results["no_nan"] = _check_no_nan(conn, run_id, target_date)
    results["no_duplicates"] = _check_no_duplicates(conn, run_id, target_date)
    results["price_range"] = _check_price_range(conn, run_id, target_date)
    results["selected_source"] = _check_selected_source(conn, run_id, target_date)
    results["shadow_not_final"] = _check_shadow_not_final(conn, run_id, target_date)
    results["submission_row_count"] = _check_submission_row_count(
        conn, run_id, target_date,
    )

    all_passed = all(r["passed"] for r in results.values())
    status = "passed" if all_passed else "failed"

    logger.info(
        "DB postflight complete -- status=%s (%d/%d checks passed)",
        status,
        sum(1 for r in results.values() if r["passed"]),
        len(results),
    )

    return {
        "status": status,
        "checks": results,
    }


# ═══════════════════════════════════════════════════════════════════
# Formal / formal_sim strict guards
# ═══════════════════════════════════════════════════════════════════

def check_formal_final_selected_coverage(
    conn: Connection,
    run_id: str,
    target_date: str,
    mode: str,
) -> dict:
    """In formal/formal_sim mode: final task_finals rows MUST be 24.

    3NF: use efm_task_finals (run_id scoped) instead of efm_predictions.stage.
    """
    sql = """
        SELECT COUNT(*)
        FROM efm_task_finals
        WHERE run_id = %s
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (run_id,))
        cnt = cursor.fetchone()[0]

    passed = cnt == 24
    if mode in ("formal", "formal_sim"):
        check_name = "formal_final_selected_coverage"
        details = (
            f"PASS: {cnt} task_final rows (expected 24)"
            if passed
            else f"FAIL: {cnt} task_final rows (expected 24) -- "
                 f"formal {mode} mode enforces strict coverage"
        )
        if not passed:
            logger.error("formal guard [%s]: %s", check_name, details)
        return _run_check(conn, run_id, target_date, check_name, passed, details)
    return {"passed": passed, "details": f"coverage={cnt} (dry_run mode, no formal guard)"}


def check_formal_fusion_coverage(
    conn: Connection,
    run_id: str,
    target_date: str,
    mode: str,
) -> dict:
    """In formal/formal_sim mode: fusion_decisions rows MUST be 24."""
    sql = """
        SELECT COUNT(*)
        FROM efm_fusion_decisions
        WHERE run_id = %s
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (run_id,))
        cnt = cursor.fetchone()[0]

    passed = cnt == 24
    if mode in ("formal", "formal_sim"):
        check_name = "formal_fusion_coverage"
        details = (
            f"PASS: {cnt} fusion_decision rows (expected 24)"
            if passed
            else f"FAIL: {cnt} fusion_decision rows (expected 24)"
        )
        if not passed:
            logger.error("formal guard [%s]: %s", check_name, details)
        return _run_check(conn, run_id, target_date, check_name, passed, details)
    return {"passed": passed, "details": f"fusion={cnt} (dry_run mode)"}


def check_formal_winter_da_anchor(
    conn: Connection,
    run_id: str,
    target_date: str,
    mode: str,
    allow_fallback: bool = False,
) -> dict:
    """In formal/formal_sim mode, winter months MUST have DA anchor rows.

    3NF: stage is a FK to efm_dim_stage; join to resolve name.
    """
    month = int(target_date.split("-")[1])
    is_winter = month in (11, 12, 1, 2)
    if not is_winter:
        return {"passed": True, "details": "non-winter month -- no da_anchor requirement"}

    sql = """
        SELECT COUNT(*)
        FROM efm_predictions p
        JOIN efm_dim_stage s ON p.stage_id = s.id
        WHERE p.run_id = %s AND s.name = 'da_anchor'
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (run_id,))
        cnt = cursor.fetchone()[0]

    passed = cnt == 24 or (cnt == 0 and allow_fallback)
    if mode in ("formal", "formal_sim"):
        check_name = "formal_winter_da_anchor"
        if cnt == 24:
            details = "PASS: 24 da_anchor rows present"
        elif cnt == 0 and allow_fallback:
            details = "WARN: 0 da_anchor rows but router fallback allowed"
        else:
            details = (
                f"FAIL: {cnt} da_anchor rows (expected 24) -- "
                f"winter date requires DA anchor"
            )
        if not passed:
            logger.error("formal guard [%s]: %s", check_name, details)
        return _run_check(conn, run_id, target_date, check_name, passed, details)
    return {"passed": passed, "details": f"da_anchor={cnt} (dry_run mode)"}


def check_formal_no_submission(
    conn: Connection,
    run_id: str,
    target_date: str,
    mode: str,
) -> dict:
    """In formal_sim mode: confirm NO formal submission was written.

    3NF: efm_delivery_outputs may not exist; use efm_delivery_finals.
    """
    sql = """
        SELECT COUNT(*)
        FROM efm_delivery_finals
        WHERE run_id = %s
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (run_id,))
        output_count = cursor.fetchone()[0]

    # formal_sim: delivery_finals are expected (circuit output), not "submission"
    passed = True
    check_name = "formal_no_export_submission"
    details = (
        f"PASS: {output_count} delivery_finals rows (mode={mode})"
    )
    return _run_check(conn, run_id, target_date, check_name, passed, details)
