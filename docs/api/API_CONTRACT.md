# EFM3 API-only Control Plane — API Contract

Auto-generated from `backend/app/main.py` via `scripts/export_openapi.py`.
Frontend (when added later) should consume this contract directly.

## Endpoints

| Method | Path | Tags |
| ------ | ---- | ---- |
| GET | `/` | - |
| GET | `/api/data-sources` | data_sources |
| GET | `/api/data-update-runs` | data_sources |
| GET | `/api/datasets` | datasets |
| GET | `/api/datasets/latest` | datasets |
| GET | `/api/datasets/readiness` | datasets |
| GET | `/api/datasets/{dataset_id}` | datasets |
| GET | `/api/health` | health |
| GET | `/api/health/db` | health |
| GET | `/api/health/schema` | health |
| GET | `/api/lineage/{run_id}` | lineage |
| GET | `/api/lineage/{run_id}/hour/{hour_business}` | lineage |
| POST | `/api/ops/export-submission` | ops |
| POST | `/api/ops/init-db` | ops |
| POST | `/api/ops/run-dry-run` | ops |
| POST | `/api/ops/run-formal` | ops |
| POST | `/api/ops/run-shadow-monitoring` | ops |
| POST | `/api/ops/update-data` | ops |
| GET | `/api/reports/available` | reports |
| GET | `/api/reports/db-health` | reports |
| GET | `/api/reports/latest` | reports |
| GET | `/api/reports/run/{run_id}` | reports |
| GET | `/api/reports/shadow-safety` | reports |
| GET | `/api/runs` | runs |
| GET | `/api/runs/{run_id}` | runs |
| GET | `/api/runs/{run_id}/delivery-outputs` | runs |
| GET | `/api/runs/{run_id}/detail` | runs |
| GET | `/api/runs/{run_id}/events` | runs |
| GET | `/api/runs/{run_id}/postflight` | postflight |
| GET | `/api/runs/{run_id}/predictions` | predictions |
| GET | `/api/runs/{run_id}/predictions/compare` | predictions |
| GET | `/api/runs/{run_id}/predictions/hourly` | predictions |
| GET | `/api/runs/{run_id}/predictions/selected` | predictions |
| GET | `/api/runs/{run_id}/summary` | runs |
| GET | `/api/source-files` | data_sources |

## Security

- `EFM3_OPS_ENABLED=false` (default): all `POST /api/ops/*` return **403**.
- Non-localhost requests require a valid `X-API-Key` header (set `EFM3_API_KEY`).
- Dangerous ops (`export-submission`, `run-formal`) require `confirm=true` **and** a non-empty `reason`.
- The DB password is never returned by any endpoint and is redacted from all logs.

## How to call (frontend later)

```ts
import type { components } from './openapi'; // generated via openapi-typescript
const res = await fetch('/api/runs', { headers: { 'X-API-Key': API_KEY } });
const runs = (await res.json()) as components['schemas']['RunSummary'][];
```
