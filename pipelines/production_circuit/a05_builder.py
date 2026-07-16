"""
a05_builder.py — Build the A05 composite candidate for real-time (V3.1).

A05 = 0.5 * DD + 0.5 * IHMAE

Key design rule:
  * IHMAE (Intra-hour MAE model) is a PRECOMPUTED_BASELINE_COMPONENT_NOT_RECONSTRUCTED
    -- its training/inference code does not exist in any repository.
    Only the canonical panel parquet column exists.
  * When IHMAE IS available: A05[h] = 0.5 * DD[h] + 0.5 * IHMAE[h]
  * When IHMAE is NOT available (fail-closed): A05[h] = DD[h]
  * DD = da_anchor read from efm_actual_prices

Data source:
  IHMAE is loaded from a config-specified source path
  (config["ihmae_source"], e.g. a CSV/parquet file path or DB table name).
  The canonical panel at artifacts/canonical_panel/FAILMODE_V5_CANONICAL_PANEL.parquet
  is the authoritative reference; the config must point to it or another valid source.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

A05_MODEL_NAME = "a05_composite"
A05_MODEL_VERSION = "v3.1"


def build_a05_candidate(
    conn: Any,
    target_date: str,
    config: dict,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the A05 composite candidate for ``target_date``.

    Args:
        conn: DB connection (for reading da_anchor from efm_actual_prices).
        target_date: Target date string (YYYY-MM-DD).
        config: Circuit config dict (must contain ``ihmae_source`` for IHMAE data).

    Returns:
        (rows, metadata) where:
            rows: list of 24 dicts (standard prediction-row format),
                  model_name="a05_composite".
            metadata: dict with ihmae_status and other diagnostic info.
    """
    # Step 1: Load DD (da_anchor) from efm_actual_prices
    dd_map: dict[int, float] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT hour_business, da_anchor FROM efm_actual_prices "
            "WHERE target_date=%s AND da_anchor IS NOT NULL ORDER BY hour_business",
            (target_date,),
        )
        for hb, v in cur.fetchall():
            dd_map[int(hb)] = float(v)

    if not dd_map:
        logger.warning("[a05_builder] no da_anchor rows for %s, returning empty", target_date)
        return [], {"ihmae_status": "NO_DD_DATA", "hours": 0}

    # Step 2: Try loading IHMAE from the configured source
    ihmae_map: dict[int, float] = {}
    ihmae_status = "NOT_ATTEMPTED"
    ihmae_source = config.get("ihmae_source")
    if ihmae_source:
        try:
            ihmae_map = _load_ihmae(ihmae_source, target_date)
            if ihmae_map:
                ihmae_status = "RECONSTRUCTED"
                logger.info("[a05_builder] IHMAE loaded from %s for %s (%d hours)",
                            ihmae_source, target_date, len(ihmae_map))
            else:
                ihmae_status = "NO_IHMAE_DATA"
                logger.warning("[a05_builder] IHMAE source %s returned no data for %s",
                               ihmae_source, target_date)
        except Exception as exc:
            ihmae_status = f"LOAD_FAILED: {exc}"
            logger.warning("[a05_builder] IHMAE load failed from %s: %s", ihmae_source, exc)
    else:
        ihmae_status = "NO_SOURCE_CONFIGURED"
        logger.info("[a05_builder] no ihmae_source configured, using DD-only")

    # Step 3: Build A05 rows
    rows: list[dict[str, Any]] = []
    for hb in range(1, 25):
        if hb not in dd_map:
            continue
        dd_val = dd_map[hb]
        ihmae_val = ihmae_map.get(hb)

        if ihmae_val is not None:
            # Full A05: 0.5 * DD + 0.5 * IHMAE
            a05_val = 0.5 * dd_val + 0.5 * ihmae_val
            reason = f"A05=0.5*DD+0.5*IHMAE (DD={dd_val:.2f}, IHMAE={ihmae_val:.2f})"
            qflags = ["a05_composite", "full"]
        else:
            # Fail-closed: A05 = DD
            a05_val = dd_val
            reason = f"A05=DD (fail-closed, IHMAE unavailable: {ihmae_status})"
            qflags = ["a05_composite", "fail_closed_to_dd"]

        rows.append({
            "hour_business": hb,
            "pred_price": a05_val,
            "model_name": A05_MODEL_NAME,
            "model_version": A05_MODEL_VERSION,
            "is_shadow": False,
            "is_selected": False,
            "selected_reason": reason,
            "quality_flags": qflags,
        })

    metadata = {
        "ihmae_status": ihmae_status,
        "hours": len(rows),
        "model_name": A05_MODEL_NAME,
        "model_version": A05_MODEL_VERSION,
    }
    return rows, metadata


def _load_ihmae(source: str, target_date: str) -> dict[int, float]:
    """Load IHMAE values for ``target_date`` from the configured source.

    Supported source types (auto-detected by extension):
      - ``.parquet``: Parquet file with columns ``business_day``, ``hour_business``, ``IHMAE``
      - ``.csv``: CSV file with same columns
      - ``.pkl``: Pickle DataFrame with same columns
      - Otherwise: treated as DB table name (columns ``target_date``, ``hour_business``, ``ihmae_pred``)
    """
    path = Path(source)

    if path.suffix == ".parquet":
        import pandas as pd  # type: ignore[import-untyped]
        df = pd.read_parquet(source)
    elif path.suffix == ".csv":
        import pandas as pd
        df = pd.read_csv(source, encoding="utf-8")
    elif path.suffix == ".pkl":
        import pandas as pd
        df = pd.read_pickle(source)
    else:
        # Assume it's a DB table reference: read from DB
        import pandas as pd
        df = pd.read_sql_query(
            "SELECT target_date, hour_business, ihmae_pred AS IHMAE "
            f"FROM {source} WHERE target_date=%s",
            params=(target_date,),
        )

    # Standardize column names
    col_map = {}
    for c in df.columns:
        cl = c.lower().strip()
        if cl in ("business_day", "target_date", "date"):
            col_map[c] = "date_col"
        elif cl in ("hour_business", "hour", "hb"):
            col_map[c] = "hour_business"
        elif cl in ("ihmae", "ihmae_pred"):
            col_map[c] = "IHMAE"
    df = df.rename(columns=col_map)

    # Filter to target date
    if "date_col" in df.columns:
        # Ensure date_col is string for comparison
        df["date_col"] = df["date_col"].astype(str)
        df = df[df["date_col"] == target_date]

    if "hour_business" not in df.columns or "IHMAE" not in df.columns:
        logger.warning("[a05_builder] IHMAE source %s missing required columns", source)
        return {}

    out = {}
    for _, row in df.iterrows():
        hb = int(row["hour_business"])
        ihmae = float(row["IHMAE"])
        out[hb] = ihmae
    return out
