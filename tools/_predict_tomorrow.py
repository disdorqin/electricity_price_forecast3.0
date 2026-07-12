#!/usr/bin/env python
"""
Production prediction for tomorrow (D+1).
Uses the P1 model adapters to train on historical data and predict a target date,
WITHOUT requiring the target variable to exist for the prediction date.

Usage:
  python tools/_predict_tomorrow.py --target-date 2026-07-10
"""
from __future__ import annotations

import csv
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path("D:/作业/大创_挑战杯_互联网/大学生创新创业计划/大创实现/其他资料/models").resolve()))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

import numpy as np
import pandas as pd

from src.common.data_loader import load_data
from src.common.repo_paths import get_data_path
from scripts.run_dayahead_p1_walkforward import (
    build_features_rich, get_rich_feature_cols, build_adapter,
)

# ── Config ────────────────────────────────────────────────────────
DA_MODELS = ["cfg05", "xgboost_rich"]
TRAIN_WINDOW_DAYS = 90
MAX_TRAIN_ROWS = 5000


def predict_date(target_date: str) -> dict[str, np.ndarray]:
    """
    Predict day-ahead prices for target_date using rich models.
    Returns {model_name: np.array(24,)}.
    """
    # ── 1. Load and prepare features ──────────────────────────────
    logger.info("Loading data and building features...")
    raw = load_data(get_data_path(), target="dayahead")
    feat = build_features_rich(raw)
    feat_cols = get_rich_feature_cols(feat)
    feat = feat.sort_values("ds")

    target_dt = pd.Timestamp(target_date)
    cutoff_str = target_date  # historical data must be strictly before target

    # ── 2. Per-model: train + predict ─────────────────────────────
    preds = {}
    for model_name in DA_MODELS:
        logger.info("Training %s...", model_name)
        adapter = build_adapter(model_name)
        adapter.feat_cols = feat_cols

        # Historical training window
        hist = feat[feat["ds"] < target_dt]
        train_df = hist[hist["ds"] >= (target_dt - pd.Timedelta(days=TRAIN_WINDOW_DAYS))]
        train_df = train_df.dropna(subset=feat_cols + ["y"]).tail(MAX_TRAIN_ROWS)
        logger.info("  training on %d rows (window=%dd)", len(train_df), TRAIN_WINDOW_DAYS)

        # Train model
        model = adapter.build_model(train_df, train_df.head(0))

        # Predict target date
        day_df = feat[feat["ds"].between(f"{target_date} 00:00", f"{target_date} 23:00")].copy()
        if len(day_df) < 24:
            logger.warning("  only %d feature rows for %s — using fallback", len(day_df), target_date)
            # Fill missing hours with previous day same hour
            prev = feat[feat["ds"].between(f"{pd.Timestamp(target_date) - pd.Timedelta(days=1)} 00:00",
                                            f"{pd.Timestamp(target_date) - pd.Timedelta(days=1)} 23:00")]
            if len(prev) == 24:
                day_df = prev.copy()
                day_df["ds"] = [pd.Timestamp(f"{target_date} {h:02d}:00:00") for h in range(24)]

        X = day_df[feat_cols].values.astype(np.float32)
        if adapter.predict_kind == "xgb":
            import xgboost as xgb
            pred = model.predict(xgb.DMatrix(X))
        else:
            pred = model.predict(X)

        if len(pred) == 24:
            preds[model_name] = pred
            logger.info("  %s: predicted 24 hours", model_name)
        else:
            logger.error("  %s: got %d predictions (expected 24)", model_name, len(pred))

    return preds


def ingest_to_db(preds: dict[str, np.ndarray], target_date: str, db_url: str):
    """Ingest predictions into EFM3 DB."""
    from tools.ingest_model_predictions import ingest_file

    for model_name, pred in preds.items():
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False,
                                          newline="", encoding="utf-8")
        w = csv.writer(tmp)
        w.writerow(["hour_business", "y_pred"])
        for hb in range(1, 25):
            w.writerow([hb, float(pred[hb - 1])])
        tmp.close()
        n = ingest_file(db_url, "dayahead", model_name, target_date,
                        Path(tmp.name), model_version="p1_prod")
        os.unlink(tmp.name)
        logger.info("  ingested %d rows for %s/%s", n, model_name, target_date)


def run_circuit(target_date: str, db_url: str) -> dict:
    """Run the production circuit for target_date."""
    from pipelines.production_circuit.circuit_orchestrator import run_production_circuit

    bgew = {"cfg05": 0.388, "xgboost_rich": 0.351}
    res = run_production_circuit(
        target_date, mode="dry_run", use_db=True, db_url=db_url,
        config={"dayahead_models": DA_MODELS,
                "realtime_models": ["sgdfnet", "da_aware_sgdf_selector"],
                "fusion_weights": bgew,
                "allow_benchmark_fallback": False,
                "rt_fallback_to_anchor": True},
    )
    return res


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Predict tomorrow and run circuit")
    ap.add_argument("--target-date", required=True)
    ap.add_argument("--no-circuit", action="store_true")
    ap.add_argument("--db-url", default=os.environ.get(
        "EFM3_DB_URL", "mysql+pymysql://root:Zlt20060313%%23@127.0.0.1:3306/efm3"))
    args = ap.parse_args()

    db_url = args.db_url.replace("%%23", "%23")

    # Predict
    logger.info("Predicting %s...", args.target_date)
    preds = predict_date(args.target_date)
    if not preds:
        logger.error("No predictions generated — exiting")
        sys.exit(1)

    # Ingest
    logger.info("Ingesting to DB...")
    ingest_to_db(preds, args.target_date, db_url)

    # Circuit
    if not args.no_circuit:
        logger.info("Running production circuit...")
        res = run_circuit(args.target_date, db_url)
        logger.info("Circuit: status=%s run=%s", res.get("status"), res.get("run_id"))

        # Quick check
        from common.db.connection import DbConnectionManager
        mgr = DbConnectionManager(db_url=db_url)
        conn = mgr.new_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM efm_delivery_finals WHERE run_id=%s", (res["run_id"],))
        delh = cur.fetchone()[0]
        cur.execute("SELECT check_name,passed FROM efm_postflight_checks WHERE run_id=%s", (res["run_id"],))
        checks = {c[0]: c[1] for c in cur.fetchall()}
        conn.close()

        logger.info("Delivery: %d hours", delh)
        logger.info("Postflight: %d/8 passed", sum(1 for v in checks.values() if v))

    logger.info("Done!")
