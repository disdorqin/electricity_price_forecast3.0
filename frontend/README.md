# EFM3 Forecast Ledger Dashboard (Frontend)

React + Vite + TypeScript + ECharts control-plane dashboard for the EFM3
electricity-price forecasting platform. It only **queries, displays, audits and
triggers** — it never bypasses the safety gates in `main.py` / the orchestrator.

## Pages

| Page           | What it shows                                                        |
| -------------- | ------------------------------------------------------------------- |
| Dashboard      | backend/DB health, shadow safety, recent runs                       |
| Runs           | run table (status, delivery, exit) → detail                         |
| RunDetail      | timeline, events, postflight, delivery, prediction counts           |
| Predictions    | ECharts hourly curve (DA anchor / official baseline / router), table, **Lineage Graph** |
| DataSources    | data sources, source files (sha256), datasets, leakage cutoff       |
| ShadowSafety   | shadow_selected / final_from_shadow / unsafe runs + stop gates      |
| OpsConsole     | init-db / update-data / dry-run / shadow-monitoring (safe); formal / export (confirm) |

## Local development

```bash
npm install
# start the FastAPI backend (separate terminal) on :8000, then:
npm run dev
# open http://localhost:5173
```

The Vite dev server proxies `/api` to `http://localhost:8000`, so no CORS setup
is needed for local use.

## Build

```bash
npm run build      # tsc --noEmit + vite build -> dist/
npm run preview    # serve the production build
```

## Configuration

- `VITE_API_BASE` — override the API base URL (default: same-origin, so the proxy works).
- `VITE_API_TARGET` — backend URL used by the dev proxy (default `http://localhost:8000`).

## Safety notes

- `formal` and `export-submission` require a second on-screen confirmation.
- The backend enforces `confirm=true` server-side and redacts the DB password.
- No production/export action is ever triggered silently.
