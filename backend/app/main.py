"""
EFM3 Control Plane — FastAPI entrypoint.

Run from the repository root:
    pip install -r backend/requirements.txt
    set EFM3_DB_URL=mysql+pymysql://root:****@127.0.0.1:3306/efm3
    uvicorn backend.app.main:app --reload --port 8000

The backend only triggers, queries, displays and audits. It never bypasses the
existing safety gates in main.py / orchestrator. Dangerous ops require confirm=true.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure the EFM3 repository root is importable (backend/app/main.py -> repo root).
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from .config import settings  # noqa: E402
from .utils.redaction import redact_db_url  # noqa: E402
from .routers import (  # noqa: E402
    data_sources,
    datasets,
    health,
    lineage,
    ops,
    postflight,
    predictions,
    reports,
    runs,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("efm3.backend")

app = FastAPI(title="EFM3 Control Plane", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(runs.router)
app.include_router(predictions.router)
app.include_router(postflight.router)
app.include_router(datasets.router)
app.include_router(data_sources.router)
app.include_router(ops.router)
app.include_router(reports.router)
app.include_router(lineage.router)


@app.on_event("startup")
def _startup() -> None:
    logger.info(
        "EFM3 Control Plane starting | db_url=%s | ops_enabled=%s | app_env=%s",
        redact_db_url(settings.db_url),
        settings.ops_enabled,
        settings.app_env,
    )


@app.get("/")
def root() -> dict:
    return {"service": "efm3-control-plane", "docs": "/docs", "health": "/api/health"}
