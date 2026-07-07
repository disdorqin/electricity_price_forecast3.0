#!/usr/bin/env python3
"""
EFM3 Fusion Chain v1 — Post-Run Analysis & Report Generator

Reads the outputs from a fusion run and generates human-readable reports.
Can be run after run_fusion_shadow_v1.py completes.

Usage:
    python scripts/analyze_fusion_shadow_v1.py [--export-root <path>]
"""

from __future__ import annotations

import json
import sys
import os
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def analyze_fusion_results(export_root: str):
    """Read and print analysis of fusion results."""
    export_path = Path(export_root)
    
    # ── Load metrics ──
    with open(export_path / "fusion_monthly_metrics.json") as f:
        monthly = json.load(f)
    
    with open(export_path / "fusion_scene_metrics.json") as f:
        scenes = json.load(f)
    
    daily = pd.read_csv(export_path / "fusion_daily_metrics.csv")
    
    with open(export_path / "promotion_decision.json") as f:
        decision = json.load(f)
    
    # Print summary
    print("=" * 80)
    print("EFM3 Fusion Chain v1 — Analysis Summary")
    print("=" * 80)
    
    print("\n## Leaderboard")
    print(f"{'Variant':40s} {'Overall':>8s} {'Winter':>8s} {'Non-Winter':>10s} {'Negative':>9s} {'Spike':>7s}")
    print("-" * 82)
    
    for vname, smapes in scenes.items():
        ov = smapes.get("overall", "N/A")
        wi = smapes.get("winter", "N/A")
        nw = smapes.get("non_winter", "N/A")
        ne = smapes.get("negative", "N/A")
        sp = smapes.get("spike", "N/A")
        fmt = lambda v: f"{v:.2f}" if isinstance(v, (int, float)) and not pd.isna(v) else "N/A"
        print(f"{vname:40s} {fmt(ov):>8s} {fmt(wi):>8s} {fmt(nw):>10s} {fmt(ne):>9s} {fmt(sp):>7s}")
    
    print("\n## Monthly Winners")
    if monthly:
        # Find winner per month
        months = set()
        for vname, mdata in monthly.items():
            for m, v in mdata.items():
                months.add(m)
        
        for month in sorted(months):
            best_v = None
            best_s = float("inf")
            for vname, mdata in monthly.items():
                s = mdata.get(month)
                if s is not None and s < best_s:
                    best_s = s
                    best_v = vname
            print(f"  {month}: {best_v} ({best_s:.2f})")
    
    print(f"\n## Decision: {decision.get('recommendation', 'N/A')}")
    print(f"  Reason: {decision.get('reason', 'N/A')}")
    
    # Daily stats
    if len(daily) > 0:
        print(f"\n## Daily Metrics: {len(daily)} rows (variants × days)")
        print(f"  Variants: {daily['variant'].nunique()}")
        print(f"  Days: {daily['target_day'].nunique()}")
    
    print("\n" + "=" * 80)
    print("Report files available in:", export_root)
    print("=" * 80)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-root",
                       default="exports/efm3_candidates/fusion_chain/fusion_v1_first_big_run")
    args = parser.parse_args()
    analyze_fusion_results(args.export_root)
