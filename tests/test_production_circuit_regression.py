"""
Regression tests for the Production Circuit work.

These guard against accidentally breaking the EXISTING capabilities while the
new ``production_circuit`` chain was added:
  * the CLI ``--chain`` flag still lists the legacy chains,
  * old imports / entry points remain intact,
  * the metric-scope semantics extend (not replace) the legacy metrics.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tools.db_ops.db_yearly_metrics as mym  # noqa: E402


# ── R1: CLI --chain still exposes legacy + new chain ────────────────────
def test_parser_chain_choices_include_production_circuit():
    from cli.parser import build_parser
    parser = build_parser()
    # The choices are enforced by argparse; parse a valid new value.
    args = parser.parse_args(["--date", "2026-02-14", "--chain", "production_circuit"])
    assert args.chain == "production_circuit"
    # Legacy chains remain selectable (not clobbered).
    a2 = parser.parse_args(["--date", "2026-02-14", "--chain", "official"])
    assert a2.chain == "official"
    a3 = parser.parse_args(["--date", "2026-02-14", "--chain", "seasonal_da_router"])
    assert a3.chain == "seasonal_da_router"


# ── R2: production_circuit package still imports cleanly ────────────────
def test_production_circuit_package_importable():
    from pipelines.production_circuit import run_production_circuit, CircuitContext
    assert callable(run_production_circuit)
    assert CircuitContext is not None


# ── R3: legacy seasonal DA router unchanged / importable ────────────────
def test_seasonal_da_router_preserved():
    from pipelines.seasonal_da_router import run_seasonal_da_router
    assert callable(run_seasonal_da_router)


# ── R4: legacy metrics still compute; floor50 clips as documented ───────
def test_metric_functions_preserved_and_floor50_clips():
    preds = {1: 10.0, 2: 100.0}
    actuals = {1: 10.0, 2: 400.0}

    legacy = mym.compute_metrics(preds, actuals)
    floor = mym.compute_metrics_floor50(preds, actuals)
    for d in (legacy, floor):
        assert set(["smape", "mae", "rmse", "mape", "wmape"]).issubset(d.keys())

    # With a sub-50 value, floor50 must clamp and yield a LOWER smape than legacy.
    # h1: legacy 200*0/20=0 ; h2: legacy 200*300/500=120.0 -> mean 60.0
    # floor h1: 50/50 ->0 ; h2: 200*300/500=120 -> mean 60.0  (here equal)
    # Use a case where floor clearly matters:
    p2 = {1: 10.0, 2: 10.0}
    a2 = {1: 100.0, 2: 500.0}
    leg2 = mym.compute_metrics(p2, a2)["smape"]
    fl2 = mym.compute_metrics_floor50(p2, a2)["smape"]
    assert fl2 < leg2          # floor(50) clipping reduces the SMAPE
    assert fl2 > 0


# ── R5: old full-chain orchestrator entry point preserved ───────────────
def test_full_chain_orchestrator_preserved():
    from pipelines.full_chain_orchestrator import run_full_chain
    assert callable(run_full_chain)
