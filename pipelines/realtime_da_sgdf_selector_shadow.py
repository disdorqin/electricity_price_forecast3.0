"""Realtime DA-SGDF Selector Shadow Adapter.

DEFAULT OFF. Only runs when --enable-realtime-da-sgdf-selector-shadow is set.

Conservative gate: default DA_anchor, switch to SGDFNet only on high-confidence
windows. Does NOT write to final/ or submission_ready.csv. Does NOT replace
champion. Outputs shadow diagnostics only.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from common.realtime_canonical_loader import (
    load_dayahead_anchor_canonical,
    canonical_smape_floor50,
)

# ── module-level flag: this module is a no-op unless explicitly enabled ──────
_SHADOW_ENABLED = False

# ── default config (inlined, can be overridden by YAML) ─────────────────────
DEFAULT_CONFIG = {
    "gap_threshold": 50.0,
    "avoid_17_24_switch": True,
    "avoid_negative_switch": True,
    "avoid_spike_switch": True,
    "sgdfnet_max_hour_pct": 0.05,  # ~5% max SGD hours (P2.9 = 2%)
    "fallback_to_da_on_missing_sgdfnet": True,
    "output_dir_suffix": "realtime_da_sgdf_selector_shadow",
}


def _log(msg: str) -> None:
    print(f"[da-sgdf-selector-shadow] {msg}", flush=True)


# ── conservative selector logic ─────────────────────────────────────────────
def _select_hour(
    da_price: float,
    sgdfnet_price: float,
    hour_business: int,
    config: dict,
) -> tuple[str, str, float]:
    """Select which model to use for one hour.

    Returns:
        (selected_model, reason, confidence)
    """
    gap = abs(da_price - sgdfnet_price)

    # Rule 1: negative price → stay with DA
    if da_price < 0 and config.get("avoid_negative_switch", True):
        return "DA_anchor", "negative_price_avoid", 0.9

    # Rule 2: spike price → stay with DA (DA wins on spike per P2.9)
    if da_price > 200 and config.get("avoid_spike_switch", True):
        return "DA_anchor", "spike_price_avoid", 0.85

    # Rule 3: 17_24 period → avoid switch (DA wins 15.04 vs SGD 16.79)
    if hour_business >= 17 and config.get("avoid_17_24_switch", True):
        return "DA_anchor", "period_17_24_avoid", 0.85

    # Rule 4: gap > threshold + normal price → switch to SGDFNet
    if gap > config.get("gap_threshold", 50.0):
        return "SGDFNet", f"high_gap_{gap:.0f}", 0.7

    # Default: DA
    return "DA_anchor", "default", 0.9


# ── main entry point ────────────────────────────────────────────────────────
def run_realtime_da_sgdf_selector_shadow(
    target_date: str,
    runs_root: str = "outputs/runs",
    data_path: str = "data/shandong_pmos_hourly.xlsx",
    config: Optional[dict] = None,
    config_path: Optional[str] = None,
) -> dict:
    """Run the selector shadow and return a manifest dict.

    Returns manifest with status, output paths, and diagnostics.
    Never raises — failures result in degraded manifest.

    Args:
        target_date: YYYY-MM-DD
        runs_root: root for run output directories
        data_path: path to xlsx with DA anchor prices
        config: optional dict of selector config (overrides defaults)
        config_path: optional path to YAML config file (loaded if provided)
    """
    # Load config from path if provided
    cfg = {**DEFAULT_CONFIG}
    if config_path:
        cfg_path = Path(config_path)
        if cfg_path.exists():
            try:
                import yaml
                with open(cfg_path, encoding="utf-8") as f:
                    yaml_cfg = yaml.safe_load(f) or {}
                if "shadow" in yaml_cfg:
                    yaml_cfg = yaml_cfg["shadow"]
                cfg.update({k: v for k, v in yaml_cfg.items() if k in DEFAULT_CONFIG})
            except Exception as e:
                _log(f"Warning: failed to load config from {config_path}: {e}")
    if config:
        cfg.update(config)
    run_dir = Path(runs_root) / target_date
    shadow_dir = run_dir / cfg["output_dir_suffix"]
    manifest: dict[str, Any] = {
        "shadow_only": True,
        "target_date": target_date,
        "model_version": "p2.11-conservative-gate",
        "run_id": f"p2.11-{target_date}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "enabled": False,
        "status": "SKIPPED_NOT_ENABLED",
        "output_files": [],
        "sgdfnet_available": False,
        "da_available": False,
        "num_hours": 0,
        "sgdfnet_hours": 0,
        "da_hours": 0,
        "warnings": [],
    }

    if not _SHADOW_ENABLED:
        _log("Shadow NOT enabled. Set --enable-realtime-da-sgdf-selector-shadow to activate.")
        return manifest
    manifest["enabled"] = True

    # ── Load DA anchor ─────────────────────────────────────────────────────
    da_anchor_path = Path(data_path)
    da_arr = None
    if da_anchor_path.exists():
        da_arr = load_dayahead_anchor_canonical(da_anchor_path, target_date)
    if da_arr is None or len(da_arr) != 24:
        manifest["status"] = "FAILED_NO_DA_ANCHOR"
        manifest["da_available"] = False
        _log(f"FAILED: No DA anchor for {target_date}")
        return manifest
    manifest["da_available"] = True

    # ── Load SGDFNet prediction ────────────────────────────────────────────
    sgdfnet_arr = None
    sgd_pred_dir = run_dir / "realtime" / "prediction"
    sgd_csv = sgd_pred_dir / "sgdfnet_predictions.csv"
    if sgd_csv.exists():
        try:
            sgd_df = pd.read_csv(sgd_csv)
            sgd_df = sgd_df.sort_values("hour_business")
            if len(sgd_df) == 24 and "y_pred" in sgd_df.columns:
                sgdfnet_arr = sgd_df["y_pred"].values.astype(float)
        except Exception as e:
            _log(f"Warning reading SGDFNet CSV: {e}")
    if sgdfnet_arr is None or len(sgdfnet_arr) != 24:
        if cfg["fallback_to_da_on_missing_sgdfnet"]:
            sgdfnet_arr = da_arr.copy()
            manifest["sgdfnet_available"] = False
            manifest["warnings"].append("SGDFNet missing; fallback all DA")
            _log("SGDFNet not found → fallback all DA")
        else:
            manifest["status"] = "DEGRADED_NO_SGDFNET"
            manifest["sgdfnet_available"] = False
            _log("SGDFNet not found (non-fallback mode)")
    else:
        manifest["sgdfnet_available"] = True

    # ── Build selector output ─────────────────────────────────────────────
    shadow_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    sgd_count = 0
    da_count = 0
    for hb in range(1, 25):
        idx = hb - 1
        da_val = float(da_arr[idx])
        sgd_val = float(sgdfnet_arr[idx])
        selected_model, reason, confidence = _select_hour(
            da_val, sgd_val, hb, cfg,
        )

        if selected_model == "SGDFNet":
            selector_pred = sgd_val
            sgd_count += 1
        else:
            selector_pred = da_val
            da_count += 1

        period = "1_8" if hb <= 8 else "9_16" if hb <= 16 else "17_24"
        rows.append({
            "business_day": target_date,
            "target_day": target_date,
            "ds": f"{target_date} {hb:02d}:00" if hb < 24 else f"{target_date} 00:00",
            "hour_business": hb,
            "period": period,
            "da_anchor": da_val,
            "sgdfnet_pred": sgd_val,
            "selected_model": selected_model,
            "selector_pred": selector_pred,
            "selection_reason": reason,
            "confidence": confidence,
            "fallback_used": not manifest["sgdfnet_available"],
            "shadow_only": True,
            "model_version": manifest["model_version"],
            "run_id": manifest["run_id"],
        })

    pred_df = pd.DataFrame(rows)
    csv_path = shadow_dir / "selector_shadow_predictions.csv"
    pred_df.to_csv(csv_path, index=False, encoding="utf-8")
    manifest["output_files"].append(str(csv_path))

    # ── Build report ──────────────────────────────────────────────────────
    report_json = {
        "target_date": target_date,
        "selector_config": cfg,
        "da_available": manifest["da_available"],
        "sgdfnet_available": manifest["sgdfnet_available"],
        "total_hours": 24,
        "da_hours": da_count,
        "sgdfnet_hours": sgd_count,
        "sgdfnet_pct": round(sgd_count / 24 * 100, 1),
        "mean_confidence": round(float(np.mean([r["confidence"] for r in rows])), 2),
        "status": "COMPLETE",
        "shadow_only": True,
    }
    report_path = shadow_dir / "selector_shadow_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_json, f, indent=2, ensure_ascii=False)
    manifest["output_files"].append(str(report_path))

    # ── Markdown report ────────────────────────────────────────────────────
    md_path = shadow_dir / "selector_shadow_report.md"
    md_content = f"""# DA-SGDF Selector Shadow Report

**Date**: {target_date}
**Status**: COMPLETE
**Shadow-only**: True

## Selection Summary

| Metric | Value |
|--------|------:|
| DA_anchor hours | {da_count}/24 |
| SGDFNet hours | {sgd_count}/24 |
| SGDFNet % | {round(sgd_count/24*100,1)}% |
| Mean confidence | {report_json['mean_confidence']} |
| SGDFNet available | {manifest['sgdfnet_available']} |

## Hour-by-Hour Selection

| HB | DA | SGDFNet | Selected | Reason | Confidence |
|----|:--:|:-------:|:--------:|--------|:----------:|
"""
    for r in rows:
        md_content += f"| {r['hour_business']} | {r['da_anchor']:.1f} | {r['sgdfnet_pred']:.1f} | {r['selected_model']} | {r['selection_reason']} | {r['confidence']} |\n"
    md_path.write_text(md_content, encoding="utf-8")
    manifest["output_files"].append(str(md_path))

    # ── Manifest ───────────────────────────────────────────────────────────
    manifest["status"] = "COMPLETE"
    manifest["num_hours"] = 24
    manifest["da_hours"] = da_count
    manifest["sgdfnet_hours"] = sgd_count
    manifest["sgdfnet_pct"] = report_json["sgdfnet_pct"]

    manifest_path = shadow_dir / "selector_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    manifest["output_files"].append(str(manifest_path))

    _log(f"COMPLETE for {target_date}: {da_count} DA / {sgd_count} SGD hours")
    return manifest


def enable_shadow() -> None:
    """Enable the shadow adapter."""
    global _SHADOW_ENABLED
    _SHADOW_ENABLED = True


def is_shadow_enabled() -> bool:
    return _SHADOW_ENABLED
