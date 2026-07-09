"""
contracts.py — Shared data contracts for the EFM3 Production Circuit.

Defines the canonical task / stage enums and the dataclasses that flow
between circuit steps. Importing this module MUST NOT trigger any DB or
model imports (keeps it safe for unit tests that only need the schema).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class CircuitTask(str, Enum):
    """Top-level task the circuit node belongs to."""

    DAYAHEAD = "dayahead"
    REALTIME = "realtime"
    FUSION = "fusion"
    DELIVERY = "delivery"


class CircuitStage(str, Enum):
    """Every intermediate / final stage in the production circuit.

    These values are written to ``efm_predictions.stage`` and to the V2
    audit tables. They intentionally mirror the 2.5 production semantics
    (raw → repaired → weighted → fused → classifier_adjusted → task_final,
    mirrored per task, plus the cross-task / separator / delivery tail).
    """

    # --- Day-ahead sub-chain ---
    DAYAHEAD_RAW_MODEL = "dayahead_raw_model"
    DAYAHEAD_MODULE_REPAIRED = "dayahead_module_repaired"
    DAYAHEAD_WEIGHTED = "dayahead_weighted"
    DAYAHEAD_FUSED = "dayahead_fused"
    DAYAHEAD_CLASSIFIER_ADJUSTED = "dayahead_classifier_adjusted"
    DAYAHEAD_TASK_FINAL = "dayahead_task_final"

    # --- Real-time sub-chain ---
    REALTIME_RAW_MODEL = "realtime_raw_model"
    REALTIME_MODULE_REPAIRED = "realtime_module_repaired"
    REALTIME_WEIGHTED = "realtime_weighted"
    REALTIME_FUSED = "realtime_fused"
    REALTIME_CLASSIFIER_ADJUSTED = "realtime_classifier_adjusted"
    REALTIME_TASK_FINAL = "realtime_task_final"

    # --- Cross-task tail ---
    CROSS_TASK_FUSION = "cross_task_fusion"
    SEPARATOR_REPAIRED = "separator_repaired"
    DELIVERY_FINAL = "delivery_final"

    # --- Benchmark (NOT a model) ---
    BENCHMARK_DA_ANCHOR = "benchmark_da_anchor"


class RepairStage(str, Enum):
    MODULE_REPAIR = "module_repair"
    WEIGHTED_REPAIR = "weighted_repair"
    SEPARATOR_REPAIR = "separator_repair"
    NO_OP = "no_op"


class StepStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    NEEDS_MODEL_OUTPUT = "NEEDS_MODEL_OUTPUT"


@dataclass
class PredictionBatch:
    """A group of 24h predictions emitted by one circuit node."""

    run_id: str
    target_date: str
    task: CircuitTask
    stage: CircuitStage
    rows: list[dict[str, Any]] = field(default_factory=list)
    model_name: Optional[str] = None
    model_version: Optional[str] = None
    source_step: Optional[str] = None
    is_final_candidate: bool = False
    is_shadow: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def row_count(self) -> int:
        return len(self.rows)


@dataclass
class CircuitStepResult:
    """Returned by every circuit node. Carries both human + machine signals."""

    step_name: str
    status: StepStatus
    message: str = ""
    input_count: int = 0
    output_count: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
    # Structured payload for downstream nodes (e.g. produced batch ids).
    artifacts: dict[str, Any] = field(default_factory=dict)


@dataclass
class RepairDecision:
    run_id: str
    target_date: str
    task: CircuitTask
    hour_business: int
    repair_stage: RepairStage
    rule_name: str
    before_value: Optional[float] = None
    after_value: Optional[float] = None
    source_prediction_id: Optional[int] = None
    repaired_prediction_id: Optional[int] = None
    reason: Optional[str] = None
    severity: str = "info"


@dataclass
class FusionCandidate:
    run_id: str
    target_date: str
    task: CircuitTask
    hour_business: int
    candidate_model: str
    candidate_stage: CircuitStage
    candidate_prediction_id: Optional[int] = None
    weight_value: Optional[float] = None
    rank_value: Optional[int] = None
    score_json: dict[str, Any] = field(default_factory=dict)
    selected: bool = False
    rejected_reason: Optional[str] = None


@dataclass
class TaskFinal:
    run_id: str
    target_date: str
    task: CircuitTask  # dayahead | realtime
    hour_business: int
    final_price: float
    final_stage: CircuitStage
    final_prediction_id: Optional[int] = None
    source_policy: Optional[str] = None
    confidence_score: Optional[float] = None


@dataclass
class DeliveryFinal:
    run_id: str
    target_date: str
    hour_business: int
    delivery_price: float
    delivery_policy: str
    dayahead_final_id: Optional[int] = None
    realtime_final_id: Optional[int] = None
    delivery_prediction_id: Optional[int] = None
    separator_rule: Optional[str] = None
    fallback_reason: Optional[str] = None


# Convenience: stage → which task it belongs to (for efm_predictions.task).
STAGE_TO_TASK: dict[CircuitStage, CircuitTask] = {
    CircuitStage.DAYAHEAD_RAW_MODEL: CircuitTask.DAYAHEAD,
    CircuitStage.DAYAHEAD_MODULE_REPAIRED: CircuitTask.DAYAHEAD,
    CircuitStage.DAYAHEAD_WEIGHTED: CircuitTask.DAYAHEAD,
    CircuitStage.DAYAHEAD_FUSED: CircuitTask.DAYAHEAD,
    CircuitStage.DAYAHEAD_CLASSIFIER_ADJUSTED: CircuitTask.DAYAHEAD,
    CircuitStage.DAYAHEAD_TASK_FINAL: CircuitTask.DAYAHEAD,
    CircuitStage.REALTIME_RAW_MODEL: CircuitTask.REALTIME,
    CircuitStage.REALTIME_MODULE_REPAIRED: CircuitTask.REALTIME,
    CircuitStage.REALTIME_WEIGHTED: CircuitTask.REALTIME,
    CircuitStage.REALTIME_FUSED: CircuitTask.REALTIME,
    CircuitStage.REALTIME_CLASSIFIER_ADJUSTED: CircuitTask.REALTIME,
    CircuitStage.REALTIME_TASK_FINAL: CircuitTask.REALTIME,
    CircuitStage.CROSS_TASK_FUSION: CircuitTask.FUSION,
    CircuitStage.SEPARATOR_REPAIRED: CircuitTask.DELIVERY,
    CircuitStage.DELIVERY_FINAL: CircuitTask.DELIVERY,
    CircuitStage.BENCHMARK_DA_ANCHOR: CircuitTask.DAYAHEAD,
}
