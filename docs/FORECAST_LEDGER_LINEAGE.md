# Forecast Ledger Lineage — Innovation Highlight

The **Lineage Graph** is the project's showcase feature. For any `(run_id,
hour_business)` it reconstructs the complete, auditable prediction chain that
produced the final selected price:

```
source_file
   ↓
dataset_version
   ↓
feature_snapshot
   ↓
candidate predictions   (da_anchor, official_baseline, selector_shadow, p3_shadow, …)
   ↓
seasonal_da_router decision
   ↓
selected final
   ↓
postflight
   ↓
delivery output
```

## Why it matters

EFM3's value proposition is *auditability*: every final price must be explainable
as a deterministic function of its inputs, with shadow candidates clearly separated
from the production selection. The lineage graph makes this visible in one view,
per hour, for any historical run.

## Data sources

`services/lineage_service.py` assembles the graph from the existing ledger tables
(all queries parameterized):

| Node               | Source table(s)                                  |
| ------------------ | ------------------------------------------------ |
| `source_file`      | `efm_source_files` (via `dataset_version.hashes`)|
| `dataset_version`  | `efm_dataset_versions` (by `target_date`)        |
| `feature_snapshot` | `efm_feature_snapshots`                          |
| `candidate`        | `efm_predictions` (non-shadow + shadow)          |
| `router`           | `efm_fusion_decisions`                           |
| `selected`         | `efm_predictions WHERE is_selected=1`            |
| `postflight`       | `efm_postflight_checks`                          |
| `delivery`         | `efm_delivery_outputs`                           |

## API

- `GET /api/lineage/{run_id}` — one node per hour summarizing the router decision.
- `GET /api/lineage/{run_id}/hour/{hour_business}` — full per-hour graph:
  ```json
  {
    "run_id": "...", "hour_business": 12, "target_date": "2026-01-15",
    "nodes": [ {"node_type":"source_file", ...}, ... ],
    "edges": [ {"from_node":"sf_0","to_node":"ds"}, ... ],
    "router_decision": {"policy_name":"seasonal_da_router", ...},
    "selected_reason": "winter DA anchor",
    "is_shadow": false, "shadow_safe": true
  }
  ```

## Frontend

`Predictions` page renders `LineageGraph` for a chosen hour. Shadow candidates are
shown dashed/amber; the selected final is highlighted green; an unsafe (shadow
selected as final) state flips the safety badge to UNSAFE.

## Design choice

No graph database, no extra infrastructure — just JSON nodes/edges computed on
read. This keeps the platform local-first and dependency-free while still
delivering the "showcase" explainability view.
