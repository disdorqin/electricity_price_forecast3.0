"""Lineage API schemas — the project's showcase innovation.

Each node in the prediction lineage graph for one (run, hour_business):
  source_file -> dataset_version -> feature_snapshot -> candidate_predictions
  -> router_decision -> selected_final -> postflight -> delivery_output
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel


class LineageNode(BaseModel):
    node_type: str  # source_file | dataset_version | feature_snapshot | candidate | router | selected | postflight | delivery
    label: str
    detail: Optional[Any] = None


class LineageEdge(BaseModel):
    from_node: str
    to_node: str


class LineageResponse(BaseModel):
    run_id: str
    hour_business: int
    target_date: Optional[str] = None
    nodes: List[LineageNode] = []
    edges: List[LineageEdge] = []
    router_decision: Optional[dict] = None
    selected_reason: Optional[str] = None
    is_shadow: bool = False
    shadow_safe: Optional[bool] = None
