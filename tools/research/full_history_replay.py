"""
EFM3 V3.1-R1 — Calibration replay (A05-style baselines).

V3.1-R1 correctness fixes applied via v31_lib:
  - DD baseline = rolling-origin OOS DA model (da_oos_pred), NOT da_actual.
  - metrics imported from fusion.metrics (no hand-rolled copies).
  - legal Oracle with invariants.

Run:  python tools/research/full_history_replay.py [--mini]
"""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v31_lib as L

CALIB = {
    "A05_med":  ("l2", {}),
    "A05_q05":  ("quantile", {"alpha": 0.05}),
    "A05_q95":  ("quantile", {"alpha": 0.95}),
    "A05_huber":("robust",   {"objective": "huber", "alpha": 0.5}),
}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mini", action="store_true")
    args = ap.parse_args()
    L.run_replay(CALIB, out_prefix="FH_CALIB", mini=args.mini, verbose=True)
    print("DONE")
