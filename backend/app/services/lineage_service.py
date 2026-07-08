"""Lineage service — builds the prediction lineage graph for one (run, hour).

This is the project's showcase innovation. For a single business hour it assembles
the full, auditable chain:

    source_file -> dataset_version -> feature_snapshot -> candidate_predictions
    -> router_decision -> selected_final -> postflight -> delivery_output

All queries are parameterized; no free-form SQL.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from pymysql.connections import Connection

from .base import q_all, q_one

_SHADOW_STAGES = ("selector_shadow", "p3_shadow", "extreme_price_shadow", "shadow")


def lineage_run_summary(conn: Connection, run_id: str) -> Optional[Dict[str, Any]]:
    """Run-level lineage: one node per hour summarizing the router decision."""
    run = q_one(conn, "SELECT run_id, target_date, mode, status FROM efm_runs WHERE run_id=%s", (run_id,))
    if not run:
        return None
    target_date = run.get("target_date")

    decisions = q_all(
        conn,
        "SELECT hour_business, policy_name, selected_model, decision_reason "
        "FROM efm_fusion_decisions WHERE run_id=%s ORDER BY hour_business",
        (run_id,),
    )
    selected = q_all(
        conn,
        "SELECT hour_business, stage, model_name, is_shadow FROM efm_predictions "
        "WHERE run_id=%s AND is_selected=1 ORDER BY hour_business",
        (run_id,),
    )

    nodes = [
        {
            "node_id": f"h_{d['hour_business']}",
            "node_type": "router",
            "label": f"H{d['hour_business']} {d['policy_name']} -> {d['selected_model']}",
            "detail": d,
        }
        for d in decisions
    ]
    shadow_count = sum(1 for s in selected if s.get("is_shadow"))
    summary = {
        "hour_count": len(decisions),
        "selected_model_by_hour": {str(d["hour_business"]): d["selected_model"] for d in decisions},
    }

    return {
        "run_id": run_id,
        "hour_business": 0,
        "target_date": str(target_date) if target_date else None,
        "nodes": nodes,
        "edges": [],
        "router_decision": summary,
        "selected_reason": None,
        "is_shadow": shadow_count > 0,
        "shadow_safe": shadow_count == 0,
    }


def _hashes(dataset: Optional[dict]) -> List[str]:
    if not dataset:
        return []
    raw = dataset.get("source_file_hashes")
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if isinstance(raw, dict):
        return [str(v) for v in raw.values() if v]
    if isinstance(raw, list):
        return [str(v) for v in raw if v]
    return []


def get_lineage(conn: Connection, run_id: str, hour_business: int) -> Dict[str, Any]:
    """Return a lineage dict (nodes/edges/router/selected) or {} if run missing."""
    run = q_one(conn, "SELECT run_id, target_date, mode, status FROM efm_runs WHERE run_id=%s", (run_id,))
    if not run:
        return {}

    target_date = run.get("target_date")

    # 1. dataset version for the run's target date
    dataset = q_one(
        conn,
        "SELECT dataset_id, target_date, status, row_counts, leakage_cutoff, canonical_hour_mapping "
        "FROM efm_dataset_versions WHERE target_date=%s ORDER BY created_at DESC LIMIT 1",
        (target_date,),
    )

    # 2. source files referenced by the dataset
    source_files: List[dict] = []
    hashes = _hashes(dataset)
    if hashes:
        placeholders = ",".join(["%s"] * len(hashes))
        source_files = q_all(
            conn,
            f"SELECT file_name, file_path, file_sha256, import_status, file_size "
            f"FROM efm_source_files WHERE file_sha256 IN ({placeholders}) LIMIT 50",
            hashes,
        )

    # 3. feature snapshot
    feature = q_one(
        conn,
        "SELECT feature_hash, feature_json FROM efm_feature_snapshots "
        "WHERE run_id=%s AND hour_business=%s LIMIT 1",
        (run_id, hour_business),
    )

    # 4. candidate predictions (non-shadow) + shadow candidates
    candidates = q_all(
        conn,
        "SELECT stage, model_name, model_version, pred_price, is_shadow, is_selected, selected_reason "
        "FROM efm_predictions WHERE run_id=%s AND hour_business=%s AND is_shadow=0 ORDER BY stage",
        (run_id, hour_business),
    )
    shadows = q_all(
        conn,
        "SELECT stage, model_name, pred_price FROM efm_predictions "
        "WHERE run_id=%s AND hour_business=%s AND is_shadow=1 ORDER BY stage",
        (run_id, hour_business),
    )

    # 5. router decision
    router = q_one(
        conn,
        "SELECT policy_name, selected_model, decision_reason, decision_json "
        "FROM efm_fusion_decisions WHERE run_id=%s AND hour_business=%s LIMIT 1",
        (run_id, hour_business),
    )

    # 6. selected final
    selected = q_one(
        conn,
        "SELECT stage, model_name, pred_price, selected_reason, is_shadow FROM efm_predictions "
        "WHERE run_id=%s AND hour_business=%s AND is_selected=1 LIMIT 1",
        (run_id, hour_business),
    )

    # 7. postflight (run-level)
    postflight = q_all(
        conn, "SELECT check_name, passed, details FROM efm_postflight_checks WHERE run_id=%s", (run_id,)
    )

    # 8. delivery (run-level)
    delivery = q_all(
        conn,
        "SELECT output_type, output_path, row_count, file_hash FROM efm_delivery_outputs WHERE run_id=%s",
        (run_id,),
    )

    # ---- Build graph nodes / edges ----
    nodes: List[dict] = []
    edges: List[dict] = []
    prev = None

    def add(node_id: str, node_type: str, label: str, detail: Any):
        nodes.append({"node_id": node_id, "node_type": node_type, "label": label, "detail": detail})

    for i, sf in enumerate(source_files):
        nid = f"sf_{i}"
        add(nid, "source_file", sf.get("file_name") or f"source_{i}", sf)
    if source_files:
        add("ds", "dataset_version", f"dataset {dataset.get('dataset_id') if dataset else '?'}", dataset)
        for i in range(len(source_files)):
            edges.append({"from_node": f"sf_{i}", "to_node": "ds"})

    if feature:
        add("fs", "feature_snapshot", f"feature {feature.get('feature_hash','')[:10]}", feature)
        if source_files:
            edges.append({"from_node": "ds", "to_node": "fs"})

    for c in candidates:
        nid = f"cand_{c['stage']}"
        add(nid, "candidate", f"{c['stage']} / {c['model_name']}", c)
        if feature:
            edges.append({"from_node": "fs", "to_node": nid})
        elif source_files:
            edges.append({"from_node": "ds", "to_node": nid})

    for s in shadows:
        nid = f"shadow_{s['stage']}"
        add(nid, "candidate", f"[shadow] {s['stage']} / {s['model_name']}", {**s, "is_shadow": True})
        if feature:
            edges.append({"from_node": "fs", "to_node": nid})

    if router:
        add("router", "router", f"router: {router.get('policy_name')}", router)
        for c in candidates:
            edges.append({"from_node": f"cand_{c['stage']}", "to_node": "router"})
        for s in shadows:
            edges.append({"from_node": f"shadow_{s['stage']}", "to_node": "router"})

    if selected:
        add("selected", "selected", f"selected: {selected.get('model_name')}", selected)
        if router:
            edges.append({"from_node": "router", "to_node": "selected"})

    if postflight:
        add("postflight", "postflight", f"postflight ({sum(1 for p in postflight if p['passed'])}/{len(postflight)} passed)", postflight)
        if selected:
            edges.append({"from_node": "selected", "to_node": "postflight"})

    if delivery:
        add("delivery", "delivery", f"delivery ({len(delivery)} outputs)", delivery)
        if postflight:
            edges.append({"from_node": "postflight", "to_node": "delivery"})

    is_shadow = bool(selected and selected.get("is_shadow"))
    shadow_safe = not is_shadow

    selected_reason = (selected or {}).get("selected_reason") or (router or {}).get("decision_reason")

    return {
        "run_id": run_id,
        "hour_business": hour_business,
        "target_date": str(target_date) if target_date else None,
        "nodes": nodes,
        "edges": edges,
        "router_decision": router,
        "selected_reason": selected_reason,
        "is_shadow": is_shadow,
        "shadow_safe": shadow_safe,
    }
