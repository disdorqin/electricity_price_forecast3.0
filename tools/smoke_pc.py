"""Diagnostic smoke runner for the production circuit.

Imports ONLY the production_circuit package (avoids main.py's heavy
ledger-pipeline imports), runs the circuit for one date, and prints each
pipeline step as it is recorded so any stall is localized.

Usage:
    python tools/smoke_pc.py --date 2026-02-14 --mode formal_sim
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# Make sure we import the project's production_circuit, not a stray copy.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipelines.production_circuit.circuit_orchestrator import (
    StepRecorder,
    run_production_circuit,
)


def _install_step_tracer() -> None:
    """Print every recorded step with flush so a hang is localized."""
    orig = StepRecorder.record

    def traced(self, *args, **kwargs):
        # signature: record(self, run_id, target_date, task, step_name,
        #                    step_order, status, ...)
        step_name = kwargs.get("step_name") or (args[3] if len(args) > 3 else "?")
        status = kwargs.get("status") or (args[5] if len(args) > 5 else "?")
        status_v = getattr(status, "value", status)
        print(f"[step] {step_name:20s} -> {status_v}", flush=True)
        return orig(self, *args, **kwargs)

    StepRecorder.record = traced


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--mode", default="formal_sim")
    args = ap.parse_args()

    db_url = os.environ.get("EFM3_DB_URL", "")
    if not db_url:
        for line in open(".env.local", encoding="utf-8"):
            if line.strip().startswith("EFM3_DB_URL"):
                db_url = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not db_url:
        print("ERROR: EFM3_DB_URL not found", flush=True)
        return 2

    _install_step_tracer()
    print(f"CALL run_production_circuit(date={args.date}, mode={args.mode})", flush=True)
    t0 = time.monotonic()
    try:
        res = run_production_circuit(
            target_date=args.date,
            mode=args.mode,
            use_db=True,
            db_url=db_url,
            config={},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"EXCEPTION after {round(time.monotonic()-t0,1)}s: {exc!r}", flush=True)
        import traceback
        traceback.print_exc()
        return 1
    print(f"DONE in {round(time.monotonic()-t0,1)}s", flush=True)
    print("RESULT:", flush=True)
    for k in ("run_id", "status", "recommendation", "smoke_result",
              "realtime_model_available", "dayahead_model_available",
              "realtime_final_present", "runtime_s"):
        print(f"  {k} = {res.get(k)}", flush=True)
    print("STEPS:", res.get("steps"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
