"""
Forecast service — subprocess-based pipeline trigger for EFM3.

The pipeline (main.py) runs in the conda epf-2 environment with all heavy
dependencies (lightgbm, catboost, pymysql, etc.). We isolate it via subprocess
to avoid environment conflicts with the backend venv.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Paths
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent  # backend/
_REPO_ROOT = _BACKEND_DIR.parent  # repo root
_MAIN_PY = _REPO_ROOT / "main.py"
_OUTPUTS_DIR = _REPO_ROOT / "outputs"

# Conda epf-2 python (required for pipeline execution)
_CONDA_PYTHON = Path("D:/computer_download/environment/conda/epf-2/python.exe")


def _build_job_id(target_date: str) -> str:
    """Generate a unique job identifier."""
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"efm3_{target_date}_{ts}"


def trigger_forecast(
    target_date: str,
    mode: str = "formal_sim",
    force: bool = False,
) -> dict:
    """Trigger a full pipeline run via subprocess.

    Returns immediately with job_id and initial status. The actual pipeline
    runs asynchronously; poll get_forecast_status() for progress.
    """
    if not _MAIN_PY.exists():
        return {"error": f"main.py not found at {_MAIN_PY}"}
    if not _CONDA_PYTHON.exists():
        return {"error": f"conda python not found at {_CONDA_PYTHON}"}

    job_id = _build_job_id(target_date)
    log_dir = _OUTPUTS_DIR / target_date
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "run.log"

    # Build command
    cmd = [str(_CONDA_PYTHON), str(_MAIN_PY), target_date, "--mode", mode]
    if force:
        cmd.append("--force")

    # Launch subprocess (non-blocking)
    try:
        log_fh = open(log_file, "w", encoding="utf-8")
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(_REPO_ROOT),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        logger.info("Forecast job %s started (pid=%d)", job_id, proc.pid)

        # Write job metadata
        meta_file = log_dir / "job_meta.json"
        meta = {
            "job_id": job_id,
            "target_date": target_date,
            "mode": mode,
            "force": force,
            "pid": proc.pid,
            "started_at": datetime.now().isoformat(),
            "status": "RUNNING",
            "log_file": str(log_file),
        }
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "job_id": job_id,
            "status": "RUNNING",
            "pid": proc.pid,
            "log_file": str(log_file),
        }
    except Exception as exc:
        logger.error("Failed to start forecast job: %s", exc)
        return {"error": str(exc)}


def get_forecast_status(target_date: str) -> dict:
    """Check the status of a forecast job.

    Reads job_meta.json and checks if the process is still running.
    """
    log_dir = _OUTPUTS_DIR / target_date
    meta_file = log_dir / "job_meta.json"

    if not meta_file.exists():
        # Check if the folder exists with outputs (manual run)
        if log_dir.exists():
            return {
                "target_date": target_date,
                "status": "COMPLETED",
                "source": "manual_run",
                "outputs_dir": str(log_dir),
            }
        return {
            "target_date": target_date,
            "status": "NOT_FOUND",
            "detail": f"No outputs folder found for {target_date}",
        }

    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": f"Failed to read job metadata: {exc}"}

    # Check if process is still running
    pid = meta.get("pid")
    if pid:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # SYNCHRONIZE
            if handle:
                kernel32.CloseHandle(handle)
                # Process still exists
                meta["status"] = "RUNNING"
            else:
                # Process finished
                meta["status"] = "COMPLETED"
        except Exception:
            # Non-Windows or check failed; assume completed if log exists
            meta["status"] = "COMPLETED"

    # Check for completion indicators in log
    log_file = Path(meta.get("log_file", ""))
    if log_file.exists():
        try:
            log_content = log_file.read_text(encoding="utf-8", errors="replace")
            if "DELIVERY MANIFEST" in log_content:
                meta["status"] = "COMPLETED"
                # Extract stage statuses
                stages = {}
                for line in log_content.splitlines():
                    if "->" in line and any(s in line for s in ["cleanup", "sync", "da_predict", "rt_predict", "circuit", "export"]):
                        parts = line.strip().split("->")
                        if len(parts) == 2:
                            stage_name = parts[0].strip()
                            stage_status = parts[1].strip()
                            stages[stage_name] = stage_status
                meta["stages"] = stages
            elif "ABORT" in log_content or "ERROR" in log_content:
                meta["status"] = "FAILED"
                # Extract error detail
                for line in log_content.splitlines():
                    if "ERROR" in line or "ABORT" in line:
                        meta["error_detail"] = line.strip()
                        break
        except Exception:
            pass

    return {
        "job_id": meta.get("job_id"),
        "target_date": target_date,
        "status": meta.get("status", "UNKNOWN"),
        "started_at": meta.get("started_at"),
        "stages": meta.get("stages", {}),
        "error_detail": meta.get("error_detail"),
    }


def get_forecast_outputs(target_date: str) -> dict:
    """Return the file tree and key CSV paths for a completed forecast.

    Structure mirrors outputs/<date>/:
      actual/
        shandong_pmos_hourly.csv
        <date>.csv
      predict/
        dayahead/  predict.csv weight.csv fuse.csv final.csv
        realtime/  predict.csv weight.csv fuse.csv final.csv module_repair.csv
    """
    root = _OUTPUTS_DIR / target_date
    if not root.exists():
        return {"error": f"outputs/{target_date} does not exist"}

    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            files.append({
                "path": str(p.relative_to(root)),
                "size_bytes": p.stat().st_size,
            })

    # Key CSV paths (for quick access)
    key_paths = {
        "da_predict": "predict/dayahead/predict.csv",
        "da_weight": "predict/dayahead/weight.csv",
        "da_fuse": "predict/dayahead/fuse.csv",
        "da_final": "predict/dayahead/final.csv",
        "rt_predict": "predict/realtime/predict.csv",
        "rt_weight": "predict/realtime/weight.csv",
        "rt_fuse": "predict/realtime/fuse.csv",
        "rt_final": "predict/realtime/final.csv",
        "rt_module_repair": "predict/realtime/module_repair.csv",
        "actual_full": "actual/shandong_pmos_hourly.csv",
    }

    # Check which key files exist
    existing = {}
    for key, rel in key_paths.items():
        full = root / rel
        if full.exists():
            existing[key] = str(full)

    # Delivery path (legacy location)
    delivery = _REPO_ROOT / "outputs" / "runs" / target_date / "delivery" / "submission_ready.csv"

    return {
        "target_date": target_date,
        "root": str(root),
        "file_count": len(files),
        "files": files,
        "key_csvs": existing,
        "delivery_path": str(delivery) if delivery.exists() else None,
    }
