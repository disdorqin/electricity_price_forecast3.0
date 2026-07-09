"""EFM3 Production Circuit — new, DB-ledger-v2 prediction pipeline.

This package implements the FULL production circuit required to reach 3.0
parity with the 2.5 production link:

  data_update → dayahead_chain → dayahead_repair → dayahead_fusion
  → dayahead_classifier → dayahead_task_final
  (mirrored for realtime)
  → cross_task_fusion → separator_repair → delivery_final
  → postflight → metrics → finish_run

Every step records itself in ``efm_pipeline_steps``; every intermediate
prediction, repair decision, fusion candidate and final is persisted to the
DB Ledger V2 tables (migration 005). The old ``seasonal_da_router`` chain is
NOT replaced — this is an additive, opt-in chain selected via ``--chain
production_circuit``.
"""

from __future__ import annotations

from .circuit_orchestrator import CircuitContext, run_production_circuit

__all__ = ["CircuitContext", "run_production_circuit"]
