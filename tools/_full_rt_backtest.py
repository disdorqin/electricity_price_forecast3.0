#!/usr/bin/env python
"""Full real-time backtest: ingest all sgdfnet predictions → run circuit → metrics."""
import os, sys, csv, tempfile, json, time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_URL = os.environ.get("EFM3_DB_URL", "mysql+pymysql://root:Zlt20060313%23@127.0.0.1:3306/efm3")
RT_CSV = Path("D:/作业/大创_挑战杯_互联网/大学生创新创业计划/大创实现/其他资料/electricity_forecast_deep_sgdf_delta/exports/efm3_candidates/realtime_trend/p2_realtime_20260706/trend_predictions.csv")

from tools.ingest_model_predictions import ingest_file
from pipelines.production_circuit.circuit_orchestrator import run_production_circuit
from common.db.connection import DbConnectionManager
from common.db.repositories import create_run
from common.db.models import RunRecord

import pandas as pd

def main():
    t0 = time.time()
    df = pd.read_csv(RT_CSV, encoding="utf-8-sig")
    df["bd"] = df["business_day"].astype(str)
    
    # Filter to backtest range
    mask = (df["bd"] >= "2025-11-01") & (df["bd"] <= "2026-06-19")
    df = df[mask].copy()
    all_dates = sorted(df["bd"].unique())
    print(f"[RT-BACKTEST] {len(all_dates)} dates, {len(df)} rows")
    
    # Pre-clean: remove old ingest rows for the backtest range
    print("\n[Cleanup] Removing old efm3_raw predictions...")
    mgr = DbConnectionManager(db_url=DB_URL)
    conn = mgr.new_connection()
    cur = conn.cursor()
    # Direct cleanup of predictions to avoid FK cascade issues
    cur.execute("DELETE FROM efm_prediction_lineage_edges WHERE run_id LIKE 'efm3_raw_%'")
    cur.execute("DELETE FROM efm_prediction_batches WHERE run_id LIKE 'efm3_raw_%'")
    cur.execute("DELETE FROM efm_predictions WHERE run_id LIKE 'efm3_raw_%'")
    cur.execute("DELETE FROM efm_runs WHERE run_id LIKE 'efm3_raw_%'")
    cur.execute("DELETE FROM efm_prediction_lineage_edges WHERE run_id LIKE 'efm3_pc_%'")
    cur.execute("DELETE FROM efm_prediction_batches WHERE run_id LIKE 'efm3_pc_%'")
    cur.execute("DELETE FROM efm_predictions WHERE run_id LIKE 'efm3_pc_%'")
    cur.execute("DELETE FROM efm_metric_runs WHERE run_id LIKE 'efm3_pc_%'")
    cur.execute("DELETE FROM efm_runs WHERE run_id LIKE 'efm3_pc_%'")
    conn.commit()
    conn.close()
    print(f"  Full clean complete")

    # ── Phase 1: Ingest ALL DA predictions (from P1 backtest output) ─
    print("\n[Phase 1] Ingesting day-ahead predictions from P1 backtest...")
    DA_MODELS = ["cfg05", "xgboost_rich", "catboost_rich"]
    p1_csv = Path("D:/作业/大创_挑战杯_互联网/大学生创新创业计划/大创实现/其他资料/models/outputs/p1_dayahead/run_backtest_full/predictions/all_predictions.csv")
    p1 = pd.read_csv(p1_csv, encoding="utf-8-sig")
    p1_masked = p1[p1["business_day"].between("2025-11-01", "2026-06-19")]
    da_dates = sorted(p1_masked["business_day"].unique())
    da_ingested = 0
    for td in da_dates:
        for model_name in DA_MODELS:
            day = p1_masked[(p1_masked["business_day"] == td) & (p1_masked["model_name"] == model_name)]
            if day.empty:
                continue
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8")
            w = csv.writer(tmp)
            w.writerow(["hour_business", "y_pred"])
            for _, r in day.iterrows():
                w.writerow([int(r["hour_business"]), float(r["y_pred"])])
            tmp.close()
            n = ingest_file(DB_URL, "dayahead", model_name, td, Path(tmp.name), model_version="p1_walkforward")
            os.unlink(tmp.name)
            da_ingested += n
    print(f"  Ingested {da_ingested} DA rows across {len(da_dates)} dates")

    # ── Phase 2: Ingest all sgdfnet predictions ──────────────────
    print("\n[Phase 2] Ingesting sgdfnet predictions...")
    print("\n[Phase 1] Ingesting sgdfnet predictions...")
    ingested = 0
    for td in all_dates:
        day = df[df["bd"] == td]
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8")
        w = csv.writer(tmp)
        w.writerow(["hour_business", "y_pred"])
        for _, r in day.iterrows():
            w.writerow([int(r["hour_business"]), float(r["trend_pred"])])
        tmp.close()
        n = ingest_file(DB_URL, "realtime", "sgdfnet", td, Path(tmp.name), model_version="gru_day_d14")
        os.unlink(tmp.name)
        ingested += n
    print(f"  Ingested {ingested} rows across {len(all_dates)} dates")

    # ── Phase 3: Compute BGEW fusion weights ─────────────────────
    print("\n[Phase 3] Computing BGEW fusion weights...")
    from tools._bgew_weights import compute_bgew_weights, get_model_level_weights, PERIODS
    da_pw = compute_bgew_weights(DB_URL, task="dayahead", lookback_days=30)
    rt_pw = compute_bgew_weights(DB_URL, task="realtime", lookback_days=30)
    da_fw = get_model_level_weights(da_pw)
    rt_fw = get_model_level_weights(rt_pw)
    print(f"  DA fusion weights: {da_fw}")
    print(f"  RT fusion weights: {rt_fw}")
    
    # Merge into a single fusion_weights dict
    fusion_weights = {}
    fusion_weights.update(da_fw)
    fusion_weights.update(rt_fw)

    # ── Phase 4: Run circuit per date ────────────────────────────
    print("\n[Phase 4] Running production circuit with BGEW weights...")
    results = []
    for i, td in enumerate(all_dates, 1):
        if i % 20 == 0:
            print(f"  ({i}/{len(all_dates)}) {td}...")
        try:
            res = run_production_circuit(
                td, mode="dry_run", use_db=True, db_url=DB_URL,
                config={"realtime_models": ["sgdfnet", "da_aware_sgdf_selector"],
                        "dayahead_models": ["cfg05", "xgboost_rich", "catboost_rich"],
                        "fusion_weights": fusion_weights,
                        "allow_benchmark_fallback": False})
            rid = res.get("run_id", "")
            mgr3 = DbConnectionManager(db_url=DB_URL)
            conn3 = mgr3.new_connection()
            cur3 = conn3.cursor()
            metrics = {}
            cur3.execute("SELECT metric_scope, smape, mae, rmse FROM efm_metric_runs WHERE run_id=%s", (rid,))
            for r in cur3.fetchall():
                metrics[r[0]] = {"smape": float(r[1]), "mae": float(r[2]), "rmse": float(r[3])}
            conn3.close()
            results.append({"date": td, "status": res.get("status"), "metrics": metrics})
        except Exception as e:
            print(f"  ERROR {td}: {e}")
            # Fix RUNNING status if NEEDS_MODEL_OUTPUT truncation occurred
            try:
                mgr3 = DbConnectionManager(db_url=DB_URL)
                conn3 = mgr3.new_connection()
                cur3 = conn3.cursor()
                cur3.execute("UPDATE efm_runs SET status='PARTIAL' WHERE run_id LIKE CONCAT('%',%s,'%') AND status IN ('RUNNING','PENDING') ORDER BY finished_at DESC LIMIT 1", (td,))
                conn3.commit()
                conn3.close()
            except:
                pass
            results.append({"date": td, "status": "FAIL", "error": str(e)})

    # ── Phase 5: Aggregate (from DB) ──────────────────────────────
    print("\n[Phase 5] Aggregating results from DB...")
    
    # Collect all metric rows from DB
    mgr = DbConnectionManager(db_url=DB_URL)
    conn = mgr.new_connection()
    cur = conn.cursor()
    
    for scope in ["dayahead", "realtime", "benchmark"]:
        cur.execute("""
            SELECT mr.target_date_start, mr.smape, mr.mae, mr.rmse, mr.wmape
            FROM efm_metric_runs mr
            JOIN efm_runs r ON mr.run_id = r.run_id
            WHERE mr.metric_scope=%s AND r.target_date BETWEEN '2025-11-01' AND '2026-06-19'
              AND r.status IN ('COMPLETE','PARTIAL')
            ORDER BY mr.target_date_start
        """, (scope,))
        rows = cur.fetchall()
        if not rows:
            print(f"  {scope}: no data")
            continue
        smapes = [float(r[1]) for r in rows if r[1] is not None]
        maes = [float(r[2]) for r in rows if r[2] is not None]
        rmses = [float(r[3]) for r in rows if r[3] is not None]
        avg_smape = sum(smapes) / len(smapes) if smapes else 0
        avg_mae = sum(maes) / len(maes) if maes else 0
        avg_rmse = sum(rmses) / len(rmses) if rmses else 0
        print(f"  {scope:<12}: {len(rows):>4} runs  sMAPE={avg_smape:>6.2f}%  MAE={avg_mae:>6.2f}  RMSE={avg_rmse:>7.2f}")
    
    # Monthly breakdown for each scope
    print()
    for scope in ["dayahead", "realtime"]:
        cur.execute("""
            SELECT DATE_FORMAT(r.target_date, '%%Y-%%m'), COUNT(*),
                   AVG(mr.smape), AVG(mr.mae)
            FROM efm_metric_runs mr
            JOIN efm_runs r ON mr.run_id = r.run_id
            WHERE mr.metric_scope=%s AND r.target_date BETWEEN '2025-11-01' AND '2026-06-19'
              AND r.status IN ('COMPLETE','PARTIAL')
            GROUP BY 1 ORDER BY 1
        """, (scope,))
        print(f"\n  [{scope.upper()}] Monthly:")
        for r in cur.fetchall():
            print(f"    {r[0]}: runs={r[1]:>3}  sMAPE={float(r[2]):>6.2f}%  MAE={float(r[3]):>6.2f}")
    
    conn.close()
    elapsed = time.time() - t0
    print(f"\n  Total time: {elapsed/60:.1f} min")
    print("[RT-BACKTEST] DONE")

if __name__ == "__main__":
    main()
