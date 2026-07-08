# EFM3 Frontend Dashboard Design

React + Vite + TypeScript + ECharts single-page dashboard. It is **read/trigger
only** — it never writes to the ledger or bypasses safety gates.

## Pages

### Dashboard
Top-level status wall: backend health, DB health, DB table count, shadow-safety
status, shadow-selected count, unsafe-run count, and the 7 most recent runs.

### Runs
Table of all runs (run_id, target_date, mode, status, delivery_status, exit_code,
started/finished). Each row links to **Run Detail**.

### Run Detail
- Run timeline (summary + events).
- Postflight checks panel (pass/fail per check).
- Delivery outputs table.
- Prediction count / selected count / shadow count cards.

### Predictions
- Run selector (from recent runs).
- ECharts hourly price curve (hour_business 1..24) with one series per stage:
  - `da_anchor` (solid blue), `official_baseline` (solid purple),
    `seasonal_da_router` / `final_selected` (solid green, thicker),
    shadow stages (dashed orange/red).
  - The **selected final** is highlighted; shadow series can be toggled off.
- Hourly prediction table.
- Selected-final table.
- **Lineage Graph** for a chosen hour (see below).

### Data Sources
- Data sources registry.
- Source files with `sha256` (truncated), import status, detected time.
- Dataset versions with status, leakage cutoff, canonical-hour mapping.
- Data-update runs (mode/status/files/rows).

### Shadow Safety
- `shadow_selected_count`, `final_from_shadow_count`, `unsafe_run_count`.
- Stop-gates list (shadow must not become final; FAIL runs block delivery).
- Red warning when status ≠ SAFE.

### Ops Console
- Safe buttons: Init DB, Update Data, Run Dry-Run, Run Shadow Monitoring.
- Dangerous buttons (red): Export Submission, Run Formal — require a second
  on-screen confirmation.
- Result panel shows the backend JSON response.

## API client
`src/api/client.ts` wraps `fetch` against `/api/*`. The Vite dev server proxies
`/api` to the backend (`http://localhost:8000`), so no CORS setup is needed locally.
Base URL is overridable via `VITE_API_BASE`.

## Lineage Graph component
`components/LineageGraph.tsx` renders the per-hour chain as an ordered vertical
node list (source file → dataset → feature → candidates → router → selected →
postflight → delivery) using the `/api/lineage/{run_id}/hour/{hour}` response. No
graph database — plain JSON nodes + edges.
