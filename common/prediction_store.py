"""
Unified PredictionStore for EFM3 — dual backend: MySQL (production) and File (dry_run / local).

Usage:
    from common.prediction_store import MySQLPredictionStore, FilePredictionStore

    # File-backed (dry_run)
    store = FilePredictionStore()
    store.write_predictions("run_001", "2026-03-10", predictions)

    # MySQL-backed (production)
    store = MySQLPredictionStore("mysql+pymysql://user:pass@host:3306/efm3")
    store.write_predictions("run_001", "2026-03-10", predictions)
"""

from __future__ import annotations

import abc
import csv
import json
import logging
from pathlib import Path
from typing import Any, Optional

from common.db.connection import DbConnectionManager
from common.db.errors import ConnectionError as DbConnectionError
from common.db.models import PredictionRecord, FusionDecisionRecord
from common.db.repositories import (
    fetch_predictions,
    insert_fusion_decision,
    insert_prediction,
    mark_selected_prediction,
)

logger = logging.getLogger(__name__)

# ── Period helpers ─────────────────────────────────────────────────

_HOUR_PERIOD_MAP: dict[tuple[int, int], str] = {
    (1, 8): "1_8",
    (9, 16): "9_16",
    (17, 24): "17_24",
}


def _compute_period(hour_business: int) -> str:
    """Map hour_business (1-24) to a period label: 1_8, 9_16, 17_24."""
    for (lo, hi), period in _HOUR_PERIOD_MAP.items():
        if lo <= hour_business <= hi:
            return period
    raise ValueError(f"hour_business={hour_business} is out of range [1, 24]")


def _row_to_prediction_record(
    run_id: str,
    target_date: str,
    row: dict[str, Any],
) -> PredictionRecord:
    """Convert a raw prediction dict to a PredictionRecord dataclass."""
    return PredictionRecord(
        run_id=run_id,
        target_date=target_date,
        hour_business=int(row["hour_business"]),
        task=str(row.get("task", "dayahead")),
        stage=str(row.get("stage", "raw_model")),
        model_name=str(row.get("model_name", "")),
        model_version=str(row.get("model_version", "unknown")),
        pred_price=float(row["pred_price"]),
        is_shadow=bool(row.get("is_shadow", False)),
        is_selected=bool(row.get("is_selected", False)),
        selected_reason=row.get("selected_reason"),
        quality_flags=row.get("quality_flags"),
    )


_CSV_HEADERS = [
    "hour_business", "task", "stage", "model_name", "model_version",
    "pred_price", "is_shadow", "is_selected", "selected_reason",
    "quality_flags",
]

_SUBMISSION_CSV_HEADERS = [
    "business_day", "ds", "hour_business", "period",
    "dayahead_price", "realtime_price",
]


# ═══════════════════════════════════════════════════════════════════
# Abstract base
# ═══════════════════════════════════════════════════════════════════

class PredictionStore(abc.ABC):
    """Abstract base for prediction storage backends."""

    @abc.abstractmethod
    def write_predictions(
        self,
        run_id: str,
        target_date: str,
        predictions: list[dict[str, Any]],
    ) -> int:
        """Write one or more prediction rows. Returns count written."""

    @abc.abstractmethod
    def write_shadow_predictions(
        self,
        run_id: str,
        target_date: str,
        shadow_type: str,
        predictions: list[dict[str, Any]],
    ) -> int:
        """Write shadow predictions for a given shadow type (stage). Returns count."""

    @abc.abstractmethod
    def write_selected_final(
        self,
        run_id: str,
        target_date: str,
        decisions: list[dict[str, Any]],
    ) -> int:
        """Write final-selected decisions (fusion output). Returns count."""

    @abc.abstractmethod
    def read_predictions(
        self,
        run_id: str,
        target_date: str,
        task: Optional[str] = None,
        stage: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Read predictions for a run/date, optionally filtered by task/stage."""

    @abc.abstractmethod
    def export_submission_ready(
        self,
        run_id: str,
        target_date: str,
        output_path: str,
    ) -> str:
        """Export a 24-row submission-ready CSV. Returns the output_path."""

    @abc.abstractmethod
    def get_db_url_info(self) -> str:
        """Return human-readable info about the backend being used."""


# ═══════════════════════════════════════════════════════════════════
# File-backed
# ═══════════════════════════════════════════════════════════════════

class FilePredictionStore(PredictionStore):
    """File-based prediction store — writes CSV under outputs/prediction_store/.

    Directory structure:
        outputs/prediction_store/{run_id}/{target_date}/
            {stage}_predictions.csv
            final_selected.csv
            submission_ready.csv
    """

    _BASE_DIR = "outputs/prediction_store"

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = Path(base_dir or self._BASE_DIR).resolve()

    # ── helpers ────────────────────────────────────────────────────

    def _store_dir(self, run_id: str, target_date: str) -> Path:
        d = self.base_dir / run_id / target_date
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _write_csv(self, path: Path, headers: list[str], rows: list[dict]) -> int:
        """Write rows to a CSV file. Returns row count."""
        if not rows:
            # Write header-only file so it exists
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
            return 0
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                # Serialise quality_flags as JSON string
                row_out = dict(row)
                if "quality_flags" in row_out and isinstance(row_out["quality_flags"], dict):
                    row_out["quality_flags"] = json.dumps(row_out["quality_flags"], ensure_ascii=False)
                writer.writerow(row_out)
        return len(rows)

    def _read_csv(self, path: Path) -> list[dict]:
        """Read a CSV file into a list of dicts."""
        if not path.exists():
            return []
        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = []
            for row in reader:
                # Deserialise quality_flags if present
                if "quality_flags" in row and row["quality_flags"]:
                    try:
                        row["quality_flags"] = json.loads(row["quality_flags"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                rows.append(row)
        return rows

    def _find_stage_file(self, store_dir: Path, stage: str) -> Path:
        return store_dir / f"{stage}_predictions.csv"

    # ── public API ─────────────────────────────────────────────────

    def write_predictions(
        self,
        run_id: str,
        target_date: str,
        predictions: list[dict[str, Any]],
    ) -> int:
        store_dir = self._store_dir(run_id, target_date)

        # Group by stage so each stage gets its own CSV
        by_stage: dict[str, list[dict]] = {}
        for p in predictions:
            stage = p.get("stage", "raw_model")
            by_stage.setdefault(stage, []).append(p)

        count = 0
        # Also write an "all" file
        for stage, stage_rows in by_stage.items():
            path = self._find_stage_file(store_dir, stage)
            count += self._write_csv(path, _CSV_HEADERS, stage_rows)

        # Write combined _all_predictions.csv as a convenience
        all_path = store_dir / "_all_predictions.csv"
        self._write_csv(all_path, _CSV_HEADERS, predictions)

        logger.info(
            f"[FilePredictionStore] Wrote {count} predictions "
            f"({len(by_stage)} stage(s)) to {store_dir}"
        )
        return count

    def write_shadow_predictions(
        self,
        run_id: str,
        target_date: str,
        shadow_type: str,
        predictions: list[dict[str, Any]],
    ) -> int:
        # Augment each row with task='shadow' and stage=shadow_type
        augmented: list[dict] = []
        for p in predictions:
            row = dict(p)
            row["task"] = "shadow"
            row["stage"] = shadow_type
            row["is_shadow"] = True
            augmented.append(row)
        return self.write_predictions(run_id, target_date, augmented)

    def write_selected_final(
        self,
        run_id: str,
        target_date: str,
        decisions: list[dict[str, Any]],
    ) -> int:
        store_dir = self._store_dir(run_id, target_date)
        path = store_dir / "final_selected.csv"

        headers = [
            "hour_business", "pred_price", "policy_name",
            "selected_model", "decision_reason",
        ]
        count = self._write_csv(path, headers, decisions)

        logger.info(
            f"[FilePredictionStore] Wrote {count} final-selected "
            f"decisions to {path}"
        )
        return count

    def read_predictions(
        self,
        run_id: str,
        target_date: str,
        task: Optional[str] = None,
        stage: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        store_dir = self._store_dir(run_id, target_date)

        if stage:
            path = self._find_stage_file(store_dir, stage)
            rows = self._read_csv(path)
        else:
            # Aggregate across all individual stage CSV files for robustness.
            # This avoids file-consistency issues with _all_predictions.csv
            # (e.g. when predictions are written across multiple calls).
            rows = []
            for fpath in sorted(store_dir.glob("*_predictions.csv")):
                if fpath.name in ("_all_predictions.csv",):
                    continue
                rows.extend(self._read_csv(fpath))

        # Filter by task if provided
        if task:
            rows = [r for r in rows if r.get("task") == task]

        return rows

    def export_submission_ready(
        self,
        run_id: str,
        target_date: str,
        output_path: str,
    ) -> str:
        store_dir = self._store_dir(run_id, target_date)

        # Gather all predictions from stage files
        all_preds = self.read_predictions(run_id, target_date)

        # Build lookup: (hour_business, stage) -> pred_price
        price_map: dict[tuple[int, str], float] = {}
        for p in all_preds:
            hb = int(p["hour_business"])
            stg = p.get("stage", "")
            try:
                price_map[(hb, stg)] = float(p["pred_price"])
            except (ValueError, TypeError):
                continue

        # Build a per-hour lookup for selected/final prices.
        # Source 1: predictions with is_selected=True in stage CSVs.
        selected_map: dict[int, float] = {}
        for p in all_preds:
            if p.get("is_selected") in ("1", 1, True, "True", "true"):
                try:
                    hb = int(p["hour_business"])
                    selected_map[hb] = float(p["pred_price"])
                except (ValueError, TypeError):
                    continue

        # Source 2: final_selected.csv (written by write_selected_final).
        # Overrides any is_selected markers from stage files.
        final_csv = store_dir / "final_selected.csv"
        if final_csv.exists():
            for dec in self._read_csv(final_csv):
                try:
                    hb = int(dec["hour_business"])
                    selected_map[hb] = float(dec["pred_price"])
                except (ValueError, TypeError, KeyError):
                    continue

        # Build 24-hour submission rows
        out_rows: list[dict[str, Any]] = []
        for hb in range(1, 25):
            ds = f"{target_date}T{hb:02d}:00:00"
            period = _compute_period(hb)
            da_price = price_map.get((hb, "da_anchor"))
            rt_price = selected_map.get(hb)

            out_rows.append({
                "business_day": target_date,
                "ds": ds,
                "hour_business": hb,
                "period": period,
                "dayahead_price": f"{da_price:.4f}" if da_price is not None else "",
                "realtime_price": f"{rt_price:.4f}" if rt_price is not None else "",
            })

        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_csv(out_path, _SUBMISSION_CSV_HEADERS, out_rows)

        # Also write a copy into the store directory
        local_copy = store_dir / "submission_ready.csv"
        self._write_csv(local_copy, _SUBMISSION_CSV_HEADERS, out_rows)

        logger.info(
            f"[FilePredictionStore] Exported {len(out_rows)} submission rows to {out_path}"
        )
        return str(out_path)

    def get_db_url_info(self) -> str:
        return f"file://{self.base_dir}"


# ═══════════════════════════════════════════════════════════════════
# MySQL-backed
# ═══════════════════════════════════════════════════════════════════

class MySQLPredictionStore(PredictionStore):
    """MySQL-backed prediction store using DbConnectionManager.

    All writes delegate to common.db.repositories.
    """

    def __init__(self, db_url: str):
        self._mgr = DbConnectionManager(db_url=db_url)
        self._db_url = db_url

    # ── connection helper ──────────────────────────────────────────

    def _conn(self):
        """Get a DB connection, raising DbConnectionError on failure."""
        try:
            return self._mgr.get_connection()
        except Exception as exc:
            raise DbConnectionError(
                f"Cannot connect to MySQL: {exc}"
            ) from exc

    # ── public API ─────────────────────────────────────────────────

    def write_predictions(
        self,
        run_id: str,
        target_date: str,
        predictions: list[dict[str, Any]],
    ) -> int:
        if not run_id:
            raise ValueError("run_id is required")
        if not target_date:
            raise ValueError("target_date is required (YYYY-MM-DD)")

        try:
            conn = self._conn()
        except DbConnectionError:
            logger.exception("[MySQLPredictionStore] DB unavailable — cannot write predictions")
            return 0

        try:
            records = [
                _row_to_prediction_record(run_id, target_date, p)
                for p in predictions
            ]
            count = 0
            for rec in records:
                insert_prediction(conn, rec)
                count += 1
            conn.commit()
            logger.info(
                f"[MySQLPredictionStore] Wrote {count} predictions "
                f"for run={run_id} date={target_date}"
            )
            return count
        except Exception:
            logger.exception(
                f"[MySQLPredictionStore] Failed to write predictions "
                f"for run={run_id} date={target_date}"
            )
            return 0
        finally:
            conn.close()

    def write_shadow_predictions(
        self,
        run_id: str,
        target_date: str,
        shadow_type: str,
        predictions: list[dict[str, Any]],
    ) -> int:
        if not run_id:
            raise ValueError("run_id is required")
        if not target_date:
            raise ValueError("target_date is required (YYYY-MM-DD)")

        try:
            conn = self._conn()
        except DbConnectionError:
            logger.exception("[MySQLPredictionStore] DB unavailable — cannot write shadow predictions")
            return 0

        try:
            records: list[PredictionRecord] = []
            for p in predictions:
                row = dict(p)
                row["task"] = "shadow"
                row["stage"] = shadow_type
                row["is_shadow"] = True
                records.append(
                    _row_to_prediction_record(run_id, target_date, row)
                )

            count = 0
            for rec in records:
                insert_prediction(conn, rec)
                count += 1
            conn.commit()
            logger.info(
                f"[MySQLPredictionStore] Wrote {count} shadow predictions "
                f"(type={shadow_type}) for run={run_id} date={target_date}"
            )
            return count
        except Exception:
            logger.exception(
                f"[MySQLPredictionStore] Failed to write shadow predictions "
                f"for run={run_id} date={target_date}"
            )
            return 0
        finally:
            conn.close()

    def write_selected_final(
        self,
        run_id: str,
        target_date: str,
        decisions: list[dict[str, Any]],
    ) -> int:
        if not run_id:
            raise ValueError("run_id is required")
        if not target_date:
            raise ValueError("target_date is required (YYYY-MM-DD)")

        try:
            conn = self._conn()
        except DbConnectionError:
            logger.exception("[MySQLPredictionStore] DB unavailable — cannot write final selections")
            return 0

        try:
            count = 0
            for dec in decisions:
                hb = int(dec["hour_business"])
                pred_price = float(dec["pred_price"])
                policy_name = str(dec.get("policy_name", ""))
                selected_model = str(dec.get("selected_model", ""))
                decision_reason = str(dec.get("decision_reason", ""))

                # 1. Write this as a fusion decision record
                fdr = FusionDecisionRecord(
                    run_id=run_id,
                    target_date=target_date,
                    hour_business=hb,
                    policy_name=policy_name,
                    selected_model=selected_model,
                    decision_reason=decision_reason,
                )
                insert_fusion_decision(conn, fdr)

                # 2. Mark the corresponding prediction as selected (final)
                mark_selected_prediction(
                    conn,
                    run_id,
                    target_date,
                    hb,
                    stage="final_selected",
                    reason=decision_reason or policy_name,
                )

                count += 1

            conn.commit()
            logger.info(
                f"[MySQLPredictionStore] Wrote {count} final-selected decisions "
                f"for run={run_id} date={target_date}"
            )
            return count
        except Exception:
            logger.exception(
                f"[MySQLPredictionStore] Failed to write final-selected decisions "
                f"for run={run_id} date={target_date}"
            )
            return 0
        finally:
            conn.close()

    def read_predictions(
        self,
        run_id: str,
        target_date: str,
        task: Optional[str] = None,
        stage: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        if not run_id:
            raise ValueError("run_id is required")
        if not target_date:
            raise ValueError("target_date is required (YYYY-MM-DD)")

        try:
            conn = self._conn()
        except DbConnectionError:
            logger.exception("[MySQLPredictionStore] DB unavailable — cannot read predictions")
            return []

        try:
            rows = fetch_predictions(conn, run_id, task=task, stage=stage)
            # Filter by target_date client-side (repo already filters by run_id)
            rows = [r for r in rows if str(r.get("target_date", "")) == target_date]
            # Decode quality_flags from string if needed
            for r in rows:
                if isinstance(r.get("quality_flags"), str):
                    try:
                        r["quality_flags"] = json.loads(r["quality_flags"])
                    except (json.JSONDecodeError, TypeError):
                        pass
            return rows
        except Exception:
            logger.exception(
                f"[MySQLPredictionStore] Failed to read predictions "
                f"for run={run_id} date={target_date}"
            )
            return []
        finally:
            conn.close()

    def export_submission_ready(
        self,
        run_id: str,
        target_date: str,
        output_path: str,
    ) -> str:
        if not run_id:
            raise ValueError("run_id is required")
        if not target_date:
            raise ValueError("target_date is required (YYYY-MM-DD)")

        # Read all predictions for this run/date
        all_preds = self.read_predictions(run_id, target_date)

        # Build per-hour price lookups
        price_map: dict[tuple[int, str], float] = {}
        selected_map: dict[int, float] = {}
        for p in all_preds:
            hb = int(p["hour_business"])
            stg = str(p.get("stage", ""))
            try:
                price_map[(hb, stg)] = float(p["pred_price"])
            except (ValueError, TypeError):
                continue

            # Check if selected
            raw_sel = p.get("is_selected")
            is_sel = raw_sel in (1, True, "1", "True", "true")
            if is_sel:
                try:
                    selected_map[hb] = float(p["pred_price"])
                except (ValueError, TypeError):
                    continue

        out_rows: list[dict[str, Any]] = []
        for hb in range(1, 25):
            ds = f"{target_date}T{hb:02d}:00:00"
            period = _compute_period(hb)
            da_price = price_map.get((hb, "da_anchor"))
            rt_price = selected_map.get(hb)

            out_rows.append({
                "business_day": target_date,
                "ds": ds,
                "hour_business": hb,
                "period": period,
                "dayahead_price": f"{da_price:.4f}" if da_price is not None else "",
                "realtime_price": f"{rt_price:.4f}" if rt_price is not None else "",
            })

        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_SUBMISSION_CSV_HEADERS)
            writer.writeheader()
            for row in out_rows:
                writer.writerow(row)

        logger.info(
            f"[MySQLPredictionStore] Exported {len(out_rows)} submission rows to {out_path}"
        )
        return str(out_path)

    def get_db_url_info(self) -> str:
        """Return sanitised DB URL (password masked)."""
        safe = self._db_url
        if "@" in safe:
            userinfo = safe.split("@")[0]
            if ":" in userinfo:
                masked = userinfo.split(":")[0] + ":****"
                safe = safe.replace(userinfo, masked)
        return safe


# ═══════════════════════════════════════════════════════════════════
# Factory helper
# ═══════════════════════════════════════════════════════════════════

def create_prediction_store(
    db_url: Optional[str] = None,
    base_dir: Optional[str] = None,
    prefer_db: bool = True,
) -> PredictionStore:
    """Factory: create the appropriate PredictionStore.

    If *prefer_db* is True and *db_url* is provided, returns MySQLPredictionStore.
    Otherwise returns FilePredictionStore.
    """
    if prefer_db and db_url:
        logger.info(f"Creating MySQLPredictionStore (db_url provided)")
        return MySQLPredictionStore(db_url)
    logger.info(f"Creating FilePredictionStore (base_dir={base_dir or FilePredictionStore._BASE_DIR})")
    return FilePredictionStore(base_dir=base_dir)
