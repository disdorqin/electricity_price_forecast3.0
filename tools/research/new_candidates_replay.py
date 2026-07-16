"""
EFM3 V3.1-R1 — New candidate tracks A-F (corrected).

Each track implements the V3.1-R1 fixes (see v31_lib):
  A Tail Distribution   : A_q05 / A_q50 / A_q95 (LightGBM quantile) + A_QRA
  E Robust Tail Objectives: E_fair / E_huber (fair & huber)
  D Anchor-Heterogeneous: D_anchor (train-window qcut bins, no full-history leak)
  B Joint Midday Curve  : B_midday (9-16) + B_midday_full (1-8=DD,9-16=B,17-24=DD)
  C Seasonal Multi-Scale: C_winter/summer/shoulder + C_seasonal_full (index fix)
  F Regime Residual     : F_regime (strict OOF two-stage, no leakage)

DD baseline and metrics come from v31_lib (OOS DA proxy + fusion.metrics).
Run:  python tools/research/new_candidates_replay.py [--mini]
"""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v31_lib as L

TRACKS = {
    "A_q05":    ("quantile", {"alpha": 0.05}),
    "A_q50":    ("quantile", {"alpha": 0.50}),
    "A_q95":    ("quantile", {"alpha": 0.95}),
    "E_fair":   ("robust",   {"objective": "fair", "fair_c": 1.0}),
    "E_huber":  ("robust",   {"objective": "huber", "alpha": 0.5}),
    "D_anchor": ("anchor",   {}),
    "B_midday": ("midday",   {}),
    "C_winter": ("season",   {"season": "winter"}),
    "C_summer": ("season",   {"season": "summer"}),
    "C_shoulder":("season",  {"season": "shoulder"}),
    "F_regime": ("regime",   {}),
}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mini", action="store_true")
    args = ap.parse_args()
    L.run_replay(TRACKS, out_prefix="FH_NEW", mini=args.mini, verbose=True)
    print("DONE")
