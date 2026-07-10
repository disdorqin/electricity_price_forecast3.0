"""
export_local.py — Local-first dated output folders for the EFM3 production run.

Every forecasting run for a given ``target_date`` produces an ordered, self-
contained set of artifacts under ``outputs/<target_date>/``:

    outputs/<date>/
      actual/                              ← copy of that day's data from data/
      predict/
        dayahead/  predict.csv weight.csv fuse.csv final.csv
        realtime/  predict.csv weight.csv fuse.csv final.csv module_repair.csv

Folder uniqueness rules (per user spec):
  * The folder MUST be unique per date.
  * Re-running the same date WITHOUT --force  -> report existing files, abort.
  * Re-running WITH --force                    -> overwrite (rmtree + recreate).

The MySQL 3NF ledger remains the system of record; these local CSVs are the
ordered, inspectable per-stage artifacts that mirror the ledger for that date.
Fused / final / module-repair values are read back from the ledger by run_id so
the local files always reflect what was actually committed.

This module is import-safe: importing it touches no DB / model code at module
load time. DB access only happens inside the read helpers, which take an
explicit ``db_url`` argument supplied by the caller (main.py).
"""

from __future__ import annotations

import csv
import logging
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger("efm3.local")

# Project root = parent of this file (export_local.py lives at repo root).
ROOT = Path(__file__).resolve().parent

DA_STAGE_FUSED = "dayahead_fused"
RT_STAGE_FUSED = "realtime_fused"
DA_STAGE_FINAL = "dayahead_task_final"
RT_STAGE_FINAL = "realtime_task_final"
RT_STAGE_MODULE_REPAIRED = "realtime_module_repaired"


# ══════════════════════════════════════════════════════════════════════════
# Folder preparation
# ══════════════════════════════════════════════════════════════════════════

def prepare_dated_folder(target_date: str, force: bool = False) -> dict:
    """Create ``outputs/<date>/`` with the required subfolder structure.

    Returns a dict with keys:
      root          Path to the dated folder
      created       bool  (True if a fresh folder was made)
      existed       bool  (True if the folder already existed beforehand)
      skip          bool  (True if it existed and --force was NOT given)
      existing_files list[str]  relative paths when skip=True
    """
    root = ROOT / "outputs" / target_date
    existed = root.exists()

    if existed and not force:
        existing_files: list[str] = []
        for p in sorted(root.rglob("*")):
            if p.is_file():
                existing_files.append(str(p.relative_to(root)))
        logger.warning(
            "outputs/%s already exists (%d files); use --force to overwrite",
            target_date, len(existing_files),
        )
        return {
            "root": root, "created": False, "existed": True,
            "skip": True, "existing_files": existing_files,
        }

    if existed and force:
        shutil.rmtree(root, ignore_errors=True)
        logger.info("outputs/%s removed (--force) and will be recreated", target_date)

    for d in (root / "actual", root / "predict" / "dayahead", root / "predict" / "realtime"):
        d.mkdir(parents=True, exist_ok=True)

    _copy_actual_data(target_date, root / "actual")

    return {"root": root, "created": True, "existed": existed, "skip": False}


def _copy_actual_data(target_date: str, actual_dir: Path) -> None:
    """Copy that day's source data into ``actual/`` for same-day convenience.

    * Full source CSV is copied verbatim (shandong_pmos_hourly.csv).
    * A per-day extract ``<date>.csv`` (the 24 hourly rows for the target day)
      is written when the day is present in the source.
    """
    src = ROOT / "data" / "shandong_pmos_hourly.csv"
    if not src.exists():
        logger.warning("  actual data source not found: %s (skipping actual/ copy)", src)
        return
    try:
        dst_full = actual_dir / "shandong_pmos_hourly.csv"
        shutil.copy2(str(src), str(dst_full))

        try:
            import pandas as pd
            df = pd.read_csv(src, encoding="gbk")
            day_n = 0
            if "时刻" in df.columns:
                d = pd.to_datetime(df["时刻"], errors="coerce")
                mask = d.dt.strftime("%Y-%m-%d") == target_date
                day = df[mask]
                day_n = len(day)
                day.to_csv(actual_dir / f"{target_date}.csv", index=False, encoding="utf-8")
            logger.info("  actual/ ready: full source + %d hourly rows for %s", day_n, target_date)
        except Exception as e:  # non-fatal; full copy already done
            logger.warning("  actual per-day extract skipped: %s", e)
    except Exception as e:
        logger.warning("  actual data copy failed: %s", e)


# ══════════════════════════════════════════════════════════════════════════
# CSV writers (pure local; no DB)
# ══════════════════════════════════════════════════════════════════════════

def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in rows:
            w.writerow(row)


def write_predict_csv(root: Path, task: str, models_preds: dict[str, dict[int, float]]) -> None:
    """predict.csv — initial per-model predictions (one column per model)."""
    models = list(models_preds.keys())
    path = root / "predict" / task / "predict.csv"
    rows = []
    for hb in range(1, 25):
        row = [hb]
        for m in models:
            row.append(round(float(models_preds[m].get(hb, 0.0)), 4))
        rows.append(row)
    _write_csv(path, ["hour_business"] + models, rows)
    logger.info("  local %s/predict.csv written (%d models)", task, len(models))


def write_weight_csv(root: Path, task: str, weights: dict[str, float]) -> None:
    """weight.csv — per-model BGEW fusion weights."""
    path = root / "predict" / task / "weight.csv"
    rows = [[m, round(float(w), 6)] for m, w in weights.items()]
    _write_csv(path, ["model", "weight"], rows)
    logger.info("  local %s/weight.csv written (%d weights)", task, len(rows))


def write_fuse_csv(root: Path, task: str, hb_prices: dict[int, float]) -> None:
    """fuse.csv — weighted fusion result for the task."""
    path = root / "predict" / task / "fuse.csv"
    rows = [[hb, round(float(hb_prices.get(hb, 0.0)), 4)] for hb in sorted(hb_prices)]
    _write_csv(path, ["hour_business", "fused_price"], rows)
    logger.info("  local %s/fuse.csv written (%d h)", task, len(rows))


def write_final_csv(root: Path, task: str, hb_prices: dict[int, float]) -> None:
    """final.csv — negative-price classifier result (task final)."""
    path = root / "predict" / task / "final.csv"
    rows = [[hb, round(float(hb_prices.get(hb, 0.0)), 4)] for hb in sorted(hb_prices)]
    _write_csv(path, ["hour_business", "final_price"], rows)
    logger.info("  local %s/final.csv written (%d h)", task, len(rows))


def write_module_repair_csv(root: Path, rows: list[list]) -> None:
    """module_repair.csv — realtime-only: before/after module-repair decisions."""
    path = root / "predict" / "realtime" / "module_repair.csv"
    header = ["hour_business", "before_value", "after_value", "rule", "reason", "severity"]
    _write_csv(path, header, rows)
    logger.info("  local realtime/module_repair.csv written (%d h)", len(rows))


# ══════════════════════════════════════════════════════════════════════════
# DB readers (3NF ledger -> local CSV), called after the circuit commits
# ══════════════════════════════════════════════════════════════════════════

def _conn(db_url: str):
    from common.db.connection import DbConnectionManager
    return DbConnectionManager(db_url=db_url).new_connection()


def read_stage_prices(db_url: str, target_date: str, run_id: str,
                      stage_name: str, task: str) -> dict[int, float]:
    """Read a fused/adjusted stage's 24h prices from efm_predictions."""
    conn = _conn(db_url)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT p.hour_business, p.pred_price FROM efm_predictions p "
            "JOIN efm_runs r ON p.run_id=r.run_id "
            "JOIN efm_dim_stage s ON p.stage_id=s.id "
            "WHERE r.target_date=%s AND p.run_id=%s AND p.task=%s AND s.name=%s "
            "ORDER BY p.hour_business",
            (target_date, run_id, task, stage_name))
        return {int(hb): float(p) for hb, p in cur.fetchall()}
    finally:
        conn.close()


def read_task_final(db_url: str, target_date: str, run_id: str, task: str) -> dict[int, float]:
    """Read the task-final price from efm_task_finals (negative-price classifier output)."""
    conn = _conn(db_url)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT tf.hour_business, tf.final_price FROM efm_task_finals tf "
            "JOIN efm_runs r ON tf.run_id=r.run_id "
            "WHERE r.target_date=%s AND tf.run_id=%s AND tf.task=%s "
            "ORDER BY tf.hour_business",
            (target_date, run_id, task))
        return {int(hb): float(p) for hb, p in cur.fetchall()}
    finally:
        conn.close()


def read_module_repair(db_url: str, target_date: str, run_id: str) -> list[list]:
    """Read realtime module-repair decisions (before/after) from efm_repair_decisions."""
    conn = _conn(db_url)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT rd.hour_business, rd.before_value, rd.after_value, rl.name, "
            "       rd.reason, rd.severity "
            "FROM efm_repair_decisions rd "
            "JOIN efm_runs r ON rd.run_id=r.run_id "
            "JOIN efm_dim_repairstage rs ON rd.repair_stage_id=rs.id "
            "JOIN efm_dim_rule rl ON rd.rule_id=rl.id "
            "WHERE r.target_date=%s AND rd.run_id=%s AND rd.task='realtime' "
            "  AND rs.name='module_repair' "
            "ORDER BY rd.hour_business",
            (target_date, run_id))
        rows = []
        for hb, bv, av, rule, reason, sev in cur.fetchall():
            rows.append([
                int(hb),
                round(float(bv), 4) if bv is not None else "",
                round(float(av), 4) if av is not None else "",
                rule, reason or "", sev,
            ])
        # Fallback: if no repair decisions recorded, use the repaired stage prices.
        if not rows:
            prices = read_stage_prices(db_url, target_date, run_id,
                                       RT_STAGE_MODULE_REPAIRED, "realtime")
            rows = [[hb, "", round(v, 4), "module_repair", "", "info"]
                    for hb, v in sorted(prices.items())]
        return rows
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# Orchestration helper: write every per-stage CSV from in-memory + DB
# ══════════════════════════════════════════════════════════════════════════

def write_circuit_local_outputs(
    root: Path,
    target_date: str,
    run_id: str,
    db_url: str,
    da_weights: Optional[dict[str, float]] = None,
    rt_weights: Optional[dict[str, float]] = None,
) -> None:
    """After the production circuit commits, fill weight/fuse/final/module_repair CSVs.

    predict.csv (per task) is written earlier by the DA/RT prediction stages,
    so this only handles the circuit-produced artifacts.
    """
    if da_weights:
        write_weight_csv(root, "dayahead", da_weights)
    if rt_weights:
        write_weight_csv(root, "realtime", rt_weights)

    # Day-ahead fused + final
    da_fused = read_stage_prices(db_url, target_date, run_id, DA_STAGE_FUSED, "dayahead")
    if da_fused:
        write_fuse_csv(root, "dayahead", da_fused)
    da_final = read_task_final(db_url, target_date, run_id, "dayahead")
    if da_final:
        write_final_csv(root, "dayahead", da_final)

    # Real-time fused + final + module repair
    rt_fused = read_stage_prices(db_url, target_date, run_id, RT_STAGE_FUSED, "realtime")
    if rt_fused:
        write_fuse_csv(root, "realtime", rt_fused)
    rt_final = read_task_final(db_url, target_date, run_id, "realtime")
    if rt_final:
        write_final_csv(root, "realtime", rt_final)
    rt_repair = read_module_repair(db_url, target_date, run_id)
    write_module_repair_csv(root, rt_repair)
