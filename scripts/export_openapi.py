"""Export the EFM3 Control Plane OpenAPI schema.

Generates:
  * docs/api/openapi.json   — full OpenAPI 3 schema (machine-readable)
  * docs/api/API_CONTRACT.md — human-readable endpoint contract

Run with the backend environment:
    backend/.venv/Scripts/python.exe scripts/export_openapi.py
(or any Python that has fastapi + the project on PYTHONPATH).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_DIR = REPO_ROOT / "docs" / "api"


def _main() -> int:
    # Make the repo root importable (backend.app, common).
    if str(REPO_ROOT) not in os.sys.path:
        os.sys.path.insert(0, str(REPO_ROOT))

    from backend.app.main import app  # noqa: E402

    API_DIR.mkdir(parents=True, exist_ok=True)

    spec = app.openapi()
    spec_path = API_DIR / "openapi.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {spec_path} ({len(json.dumps(spec))} bytes)")

    # Build a Markdown contract from the OpenAPI paths.
    lines = [
        "# EFM3 API-only Control Plane — API Contract",
        "",
        "Auto-generated from `backend/app/main.py` via `scripts/export_openapi.py`.",
        "Frontend (when added later) should consume this contract directly.",
        "",
        "## Endpoints",
        "",
        "| Method | Path | Tags |",
        "| ------ | ---- | ---- |",
    ]
    paths = spec.get("paths", {})
    for path in sorted(paths.keys()):
        for method, op in paths[path].items():
            if method.lower() not in {"get", "post", "put", "delete", "patch"}:
                continue
            tags = ", ".join(op.get("tags", [])) or "-"
            lines.append(f"| {method.upper()} | `{path}` | {tags} |")
    lines += [
        "",
        "## Security",
        "",
        "- `EFM3_OPS_ENABLED=false` (default): all `POST /api/ops/*` return **403**.",
        "- Non-localhost requests require a valid `X-API-Key` header (set `EFM3_API_KEY`).",
        "- Dangerous ops (`export-submission`, `run-formal`) require `confirm=true` **and** a non-empty `reason`.",
        "- The DB password is never returned by any endpoint and is redacted from all logs.",
        "",
        "## How to call (frontend later)",
        "",
        "```ts",
        "import type { components } from './openapi'; // generated via openapi-typescript",
        "const res = await fetch('/api/runs', { headers: { 'X-API-Key': API_KEY } });",
        "const runs = (await res.json()) as components['schemas']['RunSummary'][];",
        "```",
        "",
    ]
    contract_path = API_DIR / "API_CONTRACT.md"
    contract_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {contract_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
