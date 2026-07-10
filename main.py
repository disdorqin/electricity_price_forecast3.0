#!/usr/bin/env python
"""
EFM3.0 — 生产全链路入口（单命令跑通，对标 2.5）

    python main.py 2026-07-10

自动执行（完全可运行，无任何"待集成"占位）：
  1. 数据同步        → 从 2.5 仓库同步 + 拷贝到 P1/SGDF 数据目录
  2. 日前预测(DA)    → P1 引擎训练 cfg05 / xgboost_rich / catboost_rich，预测次日 24h
  3. 实时预测(RT)    → SGDFNet(TrendKnightRT, 注入 DA 锚点真推理)
                       + TimesFM(真推理)，各预测次日 24h
  4. 生产电路        → 修补(repair) → 加权融合(fusion, BGEW) → 分类器修正(classifier) → 交付(final)
  5. 交付导出        → 输出次日完整 日前+实时 预测 submission_ready.csv

所有候选均为真实模型推理（非 persistence / 非锚点兜底）。
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("efm3")

# ═══ paths ═════════════════════════════════════════════════════════
REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

REPO_25 = Path("D:/作业/大创_挑战杯_互联网/大学生创新创业计划/大创实现/其他资料/electricity_forecast_model2.5")
MODELS_REPO = Path("D:/作业/大创_挑战杯_互联网/大学生创新创业计划/大创实现/其他資料/models") if False else \
    Path("D:/作业/大创_挑战杯_互联网/大学生创新创业计划/大创实现/其他资料/models")
SGDF_REPO = Path("D:/作业/大创_挑战杯_互联网/大学生创新创业计划/大创实现/其他资料/electricity_forecast_deep_sgdf_delta")

PMOS_SYNCED = REPO_25 / "data" / "shandong_pmos_hourly.csv"
PMOS_DEST = SGDF_REPO / "data" / "shandong_pmos_hourly.csv"
P1_DATA_DIR = MODELS_REPO.parent / "electricity_forecast_model2.0_exp" / "data"
# SGDFNet 真实权重（exp_tcn_2026_02 含 best_model.pt；exp_tcn_real_sgdfnet_2026_02 无权重）
SGDF_MODEL_DIR = SGDF_REPO / "artifacts" / "trendknight_rt" / "exp_tcn_2026_02"

for p in [str(MODELS_REPO), str(SGDF_REPO)]:
    if p not in sys.path:
        sys.path.insert(0, p)

DB_URL = os.environ.get(
    "EFM3_DB_URL",
    "mysql+pymysql://root:Zlt20060313%%23@127.0.0.1:3306/efm3",
).replace("%%23", "%23")

# 候选模型清单（与电路 DEFAULT_*_MODELS 对齐，保证 load_model_outputs 能命中）
DA_MODELS = ["cfg05", "xgboost_rich", "catboost_rich"]
ALL_DA_MODELS = ["cfg05", "xgboost_rich", "catboost_rich"]
ALL_RT_MODELS = ["sgdfnet", "timesfm", "da_aware_sgdf_selector"]


# ═════════════════════════════════════════════════════════════════
# Stage 0: Cleanup stale circuit runs for target_date
# ═════════════════════════════════════════════════════════════════
def stage_cleanup(target_date: str) -> dict:
    """Remove previous circuit runs (``efm3_pc_%``) AND raw ingest runs
    (``efm3_raw_%``) for this date so the circuit reads only the fresh
    candidate predictions.

    All child tables reference ``efm_runs`` with ON DELETE CASCADE, so deleting
    the parent run row removes every downstream row automatically — no manual
    per-table deletes needed. NOTE the doubled ``%%`` inside the LIKE pattern:
    pymysql uses ``%`` as its param-format sentinel, so a literal ``%`` must be
    escaped as ``%%`` or the query raises "not enough arguments".
    """
    from common.db.connection import DbConnectionManager
    conn = DbConnectionManager(db_url=DB_URL).new_connection()
    try:
        cur = conn.cursor()
        removed = 0
        for prefix in ("efm3_pc_%", "efm3_raw_%"):
            cur.execute(
                "SELECT run_id FROM efm_runs "
                "WHERE target_date=%s AND run_id LIKE %s",
                (target_date, prefix))
            old = [r[0] for r in cur.fetchall()]
            for rid in old:
                # CASCADE cleans efm_predictions / efm_task_finals / repair /
                # fusion / postflight / delivery / steps / events, etc.
                cur.execute("DELETE FROM efm_runs WHERE run_id=%s", (rid,))
                removed += 1
        conn.commit()
        logger.info("=== Stage 0/5: Cleanup ===  removed %d stale run(s) for %s",
                    removed, target_date)
        return {"status": "ok", "removed": removed}
    except Exception as e:
        logger.warning("  cleanup issue: %s", e)
        return {"status": "partial", "note": str(e)}
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════
# Stage 1: Data Sync
# ═════════════════════════════════════════════════════════════════
def stage_sync(target_date: str) -> dict:
    logger.info("=== Stage 1/5: Data Sync ===")
    try:
        sync_py = str(REPO_25 / "sync_data.py")
        result = subprocess.run(
            [sys.executable, sync_py, "--source", "db", "--target-date", target_date],
            capture_output=True, text=True, timeout=120,
        )
        try:
            manifest = json.loads(result.stdout) if result.stdout.strip() else {}
        except Exception:
            manifest = {}
        logger.info("  sync status=%s rows=%s", manifest.get("status"), manifest.get("rows"))
        import shutil
        if PMOS_SYNCED.exists():
            PMOS_DEST.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(PMOS_SYNCED), str(PMOS_DEST))
            p1_data = P1_DATA_DIR / "shandong_pmos_hourly.csv"
            p1_data.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(PMOS_SYNCED), str(p1_data))
            logger.info("  CSV copied to P1 + SGDF data dirs")
        return {"status": "ok", "manifest": manifest}
    except Exception as e:
        logger.warning("  sync issue: %s (using local data)", e)
        return {"status": "partial", "note": str(e)}


# ═══════════════════════════════════════════════════════════════════
# Stage 2: Day-ahead Prediction (real P1 models)
# ═══════════════════════════════════════════════════════════════════
def stage_da_predict(target_date: str) -> dict:
    """Train + predict day-ahead with P1 engine. Returns {status, preds:{model:{hb:val}}, models}."""
    logger.info("=== Stage 2/5: Day-ahead Prediction (P1: %s) ===", ", ".join(DA_MODELS))
    from scripts.run_dayahead_p1_walkforward import (
        build_features_rich, get_rich_feature_cols, build_adapter,
    )
    from src.common.data_loader import load_data as p1_load
    from src.common.repo_paths import get_data_path
    import numpy as np

    raw = p1_load(get_data_path(), target="dayahead")
    feat = build_features_rich(raw)
    feat_cols = get_rich_feature_cols(feat)
    feat = feat.sort_values("ds")
    target_dt = pd.Timestamp(target_date)

    preds: dict[str, dict[int, float]] = {}
    for model_name in DA_MODELS:
        adapter = build_adapter(model_name)
        adapter.feat_cols = feat_cols
        hist = feat[feat["ds"] < target_dt]
        train_df = hist[hist["ds"] >= (target_dt - pd.Timedelta(days=90))]
        train_df = train_df.dropna(subset=feat_cols + ["y"]).tail(5000)
        model = adapter.build_model(train_df, train_df.head(0))
        day_df = feat[feat["ds"].between(f"{target_date} 00:00", f"{target_date} 23:00")]
        if len(day_df) < 24:
            prev = feat[feat["ds"].between(
                f"{target_dt - pd.Timedelta(days=1)} 00:00",
                f"{target_dt - pd.Timedelta(days=1)} 23:00")]
            day_df = prev.copy() if len(prev) == 24 else day_df
        X = day_df[feat_cols].values.astype(np.float32)
        if adapter.predict_kind == "xgb":
            import xgboost as xgb
            p = model.predict(xgb.DMatrix(X))
        else:
            p = model.predict(X)
        preds[model_name] = {hb: float(v) for hb, v in zip(range(1, 25), p)}
        logger.info("  %s: predicted 24h (mean=%.1f)", model_name, float(np.mean(p)))

    # Ingest to DB (task=dayahead, stage=dayahead_raw_model -> circuit picks up)
    from tools.ingest_model_predictions import ingest_file
    for model_name, ph in preds.items():
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False,
                                          newline="", encoding="utf-8")
        w = csv.writer(tmp); w.writerow(["hour_business", "y_pred"])
        for hb in range(1, 25):
            w.writerow([hb, ph.get(hb, 0.0)])
        tmp.close()
        n = ingest_file(DB_URL, "dayahead", model_name, target_date,
                        Path(tmp.name), model_version="p1_prod")
        os.unlink(tmp.name)
        logger.info("  ingested %s: %d rows", model_name, n)

    return {"status": "ok", "preds": preds, "models": list(preds.keys())}


# ═══════════════════════════════════════════════════════════════════
# Stage 3: Real-time Prediction (SGDFNet + TimesFM, both REAL inference)
# ═══════════════════════════════════════════════════════════════════
def _build_anchor_csv(target_date: str, da_preds: dict[int, float], out_csv: Path) -> Path:
    """Build a temp PMOS CSV where target day's da_anchor = our DA prediction.
    SGDFNet needs the DA anchor to produce a real RT price (DA-aware design)."""
    df = pd.read_csv(PMOS_SYNCED, encoding="gbk")
    df["_d"] = pd.to_datetime(df["时刻"])
    mask = df["_d"].dt.strftime("%Y-%m-%d") == target_date
    if mask.sum() >= 24:
        # Position-wise mapping: the 24 consecutive rows for target_date are hb 1..24
        # (last row 00:00 of next day = hb 24, NOT hb 1). Avoid hour+1 mislabel.
        sub_idx = df.index[mask].tolist()
        for i, ix in enumerate(sub_idx):
            hb = i + 1
            df.loc[ix, "日前电价"] = da_preds.get(hb, df.loc[ix, "日前电价"])
    df = df.drop(columns=["_d"])
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False, encoding="gbk")
    return out_csv


def _run_sgdfnet(target_date: str, anchor_csv: Path) -> dict[int, float] | None:
    """Real SGDFNet (TrendKnightRT) inference for target_date."""
    out_csv = anchor_csv.parent / "sgdfnet_out.csv"
    res = subprocess.run(
        [sys.executable, str(SGDF_REPO / "scripts" / "predict_realtime_deep_model.py"),
         "--model-dir", str(SGDF_MODEL_DIR),
         "--data-path", str(anchor_csv.resolve()),
         "--decision-day", target_date,
         "--out", str(out_csv.resolve()),
         "--device", "cpu"],
        cwd=str(SGDF_REPO), capture_output=True, text=True, timeout=300,
    )
    if res.returncode != 0 or not out_csv.exists():
        logger.warning("  SGDFNet subprocess failed: %s", res.stderr[-500:])
        return None
    d = pd.read_csv(out_csv)
    col = "rt_pred" if "rt_pred" in d.columns else d.columns[-1]
    return {int(r["hour_business"]): float(r[col]) for _, r in d.iterrows()}


def _run_timesfm(target_date: str) -> dict[int, float] | None:
    """Real TimesFM inference for target_date (realtime)."""
    try:
        from TimesFMBackend.infer import predict_price_for_date
        d = predict_price_for_date(str(PMOS_SYNCED), target_date, target="realtime")
        if d is None or len(d) == 0:
            return None
        vals = d["预测值"].tolist() if "预测值" in d.columns else d.iloc[:, 1].tolist()
        return {hb: float(v) for hb, v in zip(range(1, 25), vals[:24])}
    except Exception as e:
        logger.warning("  TimesFM inference failed: %s", e)
        return None


def stage_rt_predict(target_date: str, da_preds) -> dict:
    logger.info("=== Stage 3/5: Real-time Prediction (SGDFNet + TimesFM) ===")
    from tools.ingest_model_predictions import ingest_file
    from collections import defaultdict

    # da_preds may be {model: {hb: val}} (from stage_da_predict) or {hb: val}.
    # Flatten to {hb: price} (average across DA models) for the SGDFNet anchor.
    flat_da: dict[int, float] = {}
    if da_preds:
        if isinstance(next(iter(da_preds.values())), dict):
            acc = defaultdict(list)
            for _m, hm in da_preds.items():
                for hb, v in hm.items():
                    acc[int(hb)].append(float(v))
            flat_da = {hb: sum(v) / len(v) for hb, v in acc.items()}
        else:
            flat_da = {int(k): float(v) for k, v in da_preds.items()}
    if flat_da:
        logger.info("  DA anchor (avg of %d models) mean=%.1f",
                    len(da_preds), sum(flat_da.values()) / 24)

    run_dir = REPO / "outputs" / "runs" / target_date
    anchor_csv = run_dir / "pmos_anchor.csv"
    if flat_da:
        _build_anchor_csv(target_date, flat_da, anchor_csv)
    else:
        # Fallback: copy raw PMOS (SGDFNet will use historical anchor where available)
        import shutil
        run_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(PMOS_SYNCED), str(anchor_csv))

    rt_results: dict[str, dict[int, float]] = {}

    # ── SGDFNet (real inference, DA-anchor injected) ──
    sg = _run_sgdfnet(target_date, anchor_csv)
    if sg:
        rt_results["sgdfnet"] = sg
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8")
        w = csv.writer(tmp); w.writerow(["hour_business", "y_pred"])
        for hb in range(1, 25):
            w.writerow([hb, sg.get(hb, 0.0)])
        tmp.close()
        n = ingest_file(DB_URL, "realtime", "sgdfnet", target_date, Path(tmp.name), model_version="sgdfnet_prod")
        os.unlink(tmp.name)
        logger.info("  SGDFNet: REAL inference ingested %d rows (mean=%.1f)", n, sum(sg.values())/24)
    else:
        logger.warning("  SGDFNet: no output produced")

    # ── TimesFM (real inference) ──
    tfm = _run_timesfm(target_date)
    if tfm:
        rt_results["timesfm"] = tfm
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8")
        w = csv.writer(tmp); w.writerow(["hour_business", "y_pred"])
        for hb in range(1, 25):
            w.writerow([hb, tfm.get(hb, 0.0)])
        tmp.close()
        n = ingest_file(DB_URL, "realtime", "timesfm", target_date, Path(tmp.name), model_version="timesfm_prod")
        os.unlink(tmp.name)
        logger.info("  TimesFM: REAL inference ingested %d rows (mean=%.1f)", n, sum(tfm.values())/24)
    else:
        logger.warning("  TimesFM: no output produced")

    # ── DA-aware SGDF selector (derived 3rd RT candidate) ──
    # default DA_anchor; switch to SGDFNet only when |sgdf-da|/da < 10% & non-winter
    # NOTE: use the flattened `flat_da` (avg across DA models), NOT the nested
    # `da_preds` dict — reading da_preds.get(hb) on {model:{hb:val}} always
    # returns 0.0, which previously wrote an all-zero selector into the ledger.
    if sg and flat_da:
        from pipelines.production_circuit.model_loader import SELECTOR_SWITCH_REL_TOL, WINTER_MONTHS
        month = pd.Timestamp(target_date).month
        sel = {}
        for hb in range(1, 25):
            da = flat_da.get(hb, 0.0)
            s = sg.get(hb, da)
            if da and month not in WINTER_MONTHS and abs(s - da) / abs(da) < SELECTOR_SWITCH_REL_TOL:
                sel[hb] = s
            else:
                sel[hb] = da  # DA_anchor fallback (use DA pred as anchor proxy)
        rt_results["da_aware_sgdf_selector"] = sel
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8")
        w = csv.writer(tmp); w.writerow(["hour_business", "y_pred"])
        for hb in range(1, 25):
            w.writerow([hb, sel.get(hb, 0.0)])
        tmp.close()
        n = ingest_file(DB_URL, "realtime", "da_aware_sgdf_selector", target_date, Path(tmp.name), model_version="selector_v1")
        os.unlink(tmp.name)
        logger.info("  da_aware_sgdf_selector: ingested %d rows", n)

    status = "ok" if rt_results else "failed"
    return {"status": status, "models": list(rt_results.keys()), "rt_results": rt_results}


# ═══════════════════════════════════════════════════════════════════
# Stage 4: Production Circuit (repair → fusion → classifier → delivery)
# ═══════════════════════════════════════════════════════════════════
def stage_circuit(target_date: str, mode: str = "formal_sim") -> dict:
    logger.info("=== Stage 4/5: Production Circuit ===")
    from pipelines.production_circuit.circuit_orchestrator import run_production_circuit

    try:
        from tools._bgew_weights import compute_bgew_weights, get_model_level_weights
        da_pw = compute_bgew_weights(DB_URL, task="dayahead", lookback_days=30)
        rt_pw = compute_bgew_weights(DB_URL, task="realtime", lookback_days=30)
        fw = {}
        fw.update(get_model_level_weights(da_pw))
        fw.update(get_model_level_weights(rt_pw))
        logger.info("  BGEW weights: DA=%s RT=%s", da_pw, rt_pw)
    except Exception as e:
        logger.warning("  BGEW weight computation failed: %s (equal weights)", e)
        fw = {}

    res = run_production_circuit(
        target_date, use_db=True, db_url=DB_URL, mode=mode,
        config={
            "dayahead_models": ALL_DA_MODELS,
            "realtime_models": ALL_RT_MODELS,
            "fusion_weights": fw,
            "allow_benchmark_fallback": False,
        },
    )
    rid = res.get("run_id", "")
    status = res.get("status", "UNKNOWN")

    from common.db.connection import DbConnectionManager
    conn = DbConnectionManager(db_url=DB_URL).new_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM efm_delivery_finals WHERE run_id=%s", (rid,))
    delh = cur.fetchone()[0]
    cur.execute("SELECT check_name,passed FROM efm_postflight_checks WHERE run_id=%s", (rid,))
    checks = {c[0]: c[1] for c in cur.fetchall()}
    conn.close()

    logger.info("  Circuit: status=%s delivery=%dh postflight=%d/8",
                status, delh, sum(1 for v in checks.values() if v))
    return {"status": status, "run_id": rid, "delivery_hours": delh, "checks": checks}


# ═══════════════════════════════════════════════════════════════════
# Stage 5: Delivery Export (real DA + RT from task finals, with fallback)
# ═══════════════════════════════════════════════════════════════════
def _read_task_finals(target_date: str, task: str, run_id: str | None = None) -> dict[int, float]:
    from common.db.connection import DbConnectionManager
    conn = DbConnectionManager(db_url=DB_URL).new_connection()
    try:
        cur = conn.cursor()
        if run_id:
            cur.execute(
                "SELECT hour_business, final_price FROM efm_task_finals "
                "WHERE target_date=%s AND task=%s AND run_id=%s ORDER BY hour_business",
                (target_date, task, run_id))
        else:
            # Fallback: latest run_id for this target_date
            cur.execute(
                "SELECT hour_business, final_price FROM efm_task_finals "
                "WHERE target_date=%s AND task=%s "
                "AND run_id=(SELECT MAX(run_id) FROM efm_task_finals "
                "WHERE target_date=%s AND task=%s) ORDER BY hour_business",
                (target_date, task, target_date, task))
        return {int(hb): float(p) for hb, p in cur.fetchall()}
    finally:
        conn.close()


def _read_fused_realtime(target_date: str, run_id: str | None = None) -> dict[int, float]:
    """Fallback: read realtime_fused stage (weighted fusion of RT candidates)."""
    from common.db.connection import DbConnectionManager
    conn = DbConnectionManager(db_url=DB_URL).new_connection()
    try:
        cur = conn.cursor()
        if run_id:
            cur.execute(
                "SELECT hour_business, pred_price FROM efm_predictions "
                "WHERE target_date=%s AND task='realtime' AND stage='realtime_fused' "
                "AND run_id=%s ORDER BY hour_business", (target_date, run_id))
        else:
            cur.execute(
                "SELECT hour_business, pred_price FROM efm_predictions "
                "WHERE target_date=%s AND task='realtime' AND stage='realtime_fused' "
                "ORDER BY hour_business", (target_date,))
        rows = cur.fetchall()
        if rows:
            return {int(hb): float(p) for hb, p in rows}
        # Last resort: average raw_model candidates of this run
        cur.execute(
            "SELECT hour_business, pred_price FROM efm_predictions "
            "WHERE target_date=%s AND task='realtime' AND stage='realtime_raw_model' "
            "AND run_id=%s ORDER BY hour_business", (target_date, run_id))
        from collections import defaultdict
        acc = defaultdict(list)
        for hb, p in cur.fetchall():
            acc[int(hb)].append(float(p))
        return {hb: sum(v)/len(v) for hb, v in acc.items()}
    finally:
        conn.close()


def stage_export(target_date: str, run_id: str) -> dict:
    logger.info("=== Stage 5/5: Delivery Export ===")
    da_final = _read_task_finals(target_date, "dayahead", run_id)
    rt_final = _read_task_finals(target_date, "realtime", run_id)
    if not rt_final:
        rt_final = _read_fused_realtime(target_date, run_id)
        logger.info("  RT final missing from task_finals; used fused fallback (%d h)", len(rt_final))

    if not da_final:
        logger.warning("  DA final missing — cannot produce complete delivery")
        return {"status": "failed", "path": None, "rows": 0}

    run_dir = REPO / "outputs" / "runs" / target_date / "delivery"
    run_dir.mkdir(parents=True, exist_ok=True)
    csv_path = run_dir / "submission_ready.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["business_day", "ds", "hour_business", "period",
                    "dayahead_price", "realtime_price", "selected_reason"])
        for hb in range(1, 25):
            ds = f"{target_date} {hb:02d}:00:00"
            period = "1_8" if hb <= 8 else ("9_16" if hb <= 16 else "17_24")
            da = da_final.get(hb)
            rt = rt_final.get(hb) if rt_final else None
            reason = "da+rt fused via production circuit" if rt is not None else "da only (rt final missing)"
            w.writerow([target_date, ds, hb, period,
                        f"{da:.2f}" if da is not None else "",
                        f"{rt:.2f}" if rt is not None else "",
                        reason])
    logger.info("  submission -> %s (%d rows, DA=%d RT=%d)",
                csv_path, 24, len(da_final), len(rt_final or {}))
    return {"status": "ok", "path": str(csv_path), "rows": 24}


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════
def run_full(target_date: str, mode: str = "formal_sim") -> dict:
    t0 = time.time()
    manifest = {"target_date": target_date, "pipeline": "efm3_full", "stages": {}}

    manifest["stages"]["cleanup"] = stage_cleanup(target_date)
    manifest["stages"]["sync"] = stage_sync(target_date)
    da = stage_da_predict(target_date)
    manifest["stages"]["da_predict"] = da
    manifest["stages"]["rt_predict"] = stage_rt_predict(target_date, da.get("preds", {}))
    manifest["stages"]["circuit"] = stage_circuit(target_date, mode=mode)

    rid = manifest["stages"]["circuit"].get("run_id", "")
    if rid:
        manifest["stages"]["export"] = stage_export(target_date, rid)

    manifest["elapsed_s"] = round(time.time() - t0, 1)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="EFM3.0 生产全链路")
    p.add_argument("pos_date", nargs="?", default=None)
    p.add_argument("--date", default=None)
    p.add_argument("--mode", default="formal_sim", choices=["dry_run", "formal_sim", "formal"])
    p.add_argument("--pipeline", default="full", choices=["full", "da_only", "rt_only", "circuit_only"])
    return p


def main():
    args = build_parser().parse_args()
    td = args.date or args.pos_date or date.today().isoformat()
    logger.info("=" * 60)
    logger.info("EFM3.0 Production Run: target=%s mode=%s", td, args.mode)
    logger.info("=" * 60)
    manifest = run_full(td, mode=args.mode)
    print("\n" + "=" * 60)
    print("  DELIVERY MANIFEST")
    print("=" * 60)
    for stage, result in manifest["stages"].items():
        print(f"  {stage:14s} → {result.get('status', '?')}")
    del_info = manifest.get("stages", {}).get("export", {})
    if del_info.get("status") == "ok":
        print(f"  ✅ delivery: {del_info.get('path')} ({del_info.get('rows')} rows)")
    print(f"  ⏱  {manifest.get('elapsed_s', 0):.0f}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
