#!/usr/bin/env python3
"""EFM3 latest metrics + DB coverage summary (release-seal helper).

Reads the newest metrics JSON under the outputs dir and (optionally) the live
MySQL ledger to report:

  - latest metrics file path
  - latest SMAPE / MAE / RMSE / WMAPE (from the newest metrics json)
  - evaluated days + DB coverage (if EFM3_DB_URL configured)
  - API handoff status (openapi + handoff doc presence)

Does NOT re-run experiments. Lightweight: pure stdlib + pymysql.

Usage:
    python tools/db_ops/db_latest_metrics_summary.py \
        [--metrics-dir outputs/db_yearly_formal_sim] \
        [--db-url $EFM3_DB_URL] \
        [--output-md path] [--output-json path]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent.parent


def parse_db_url(db_url: str) -> dict:
    """Parse a mysql+pymysql:// URL. URL-decodes the password (%23 -> #)."""
    u = urlparse(db_url)
    userinfo, hostport = u.netloc.rsplit("@", 1)
    user, pw = userinfo.split(":", 1)
    pw = unquote(pw)
    host, port = hostport.rsplit(":", 1)
    database = u.path.lstrip("/")
    return {
        "host": host,
        "port": int(port),
        "user": user,
        "password": pw,
        "database": database,
    }


def find_latest_metrics(metrics_dir: Path):
    """Return (path, dict) for the newest *.json metrics file, or (None, None)."""
    if not metrics_dir.exists():
        return None, None
    files = glob.glob(str(metrics_dir / "**" / "*.json"), recursive=True)
    # prefer files that actually contain a "yearly" key
    candidates = []
    for f in files:
        p = Path(f)
        if p.name.startswith("PARTIAL"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and "yearly" in data:
            candidates.append((p.stat().st_mtime, p, data))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, path, data = candidates[0]
    return path, data


def db_coverage(db_url: str) -> dict | None:
    """Query live ledger for run/date coverage. Returns None on any failure."""
    try:
        import pymysql
    except Exception:
        return None
    try:
        params = parse_db_url(db_url)
        conn = pymysql.connect(
            host=params["host"], port=params["port"], user=params["user"],
            password=params["password"], database=params["database"],
            connect_timeout=10,
        )
        cur = conn.cursor()
        out = {}

        def one(sql, *a):
            cur.execute(sql, a)
            return cur.fetchone()

        r = one("SELECT MIN(target_date), MAX(target_date) FROM efm_runs WHERE mode='formal_sim'")
        out["formal_sim_min"] = str(r[0]) if r and r[0] else None
        out["formal_sim_max"] = str(r[1]) if r and r[1] else None
        r = one("SELECT COUNT(DISTINCT target_date) FROM efm_runs WHERE mode='formal_sim'")
        out["evaluated_days"] = r[0] if r else 0
        r = one("SELECT MIN(target_date), MAX(target_date) FROM efm_actual_prices")
        out["actual_prices_min"] = str(r[0]) if r and r[0] else None
        out["actual_prices_max"] = str(r[1]) if r and r[1] else None
        conn.close()
        return out
    except Exception as e:  # pragma: no cover - defensive
        return {"error": str(e)}


def api_handoff_status() -> dict:
    openapi = ROOT / "docs" / "api" / "openapi.json"
    handoff = ROOT / "docs" / "FRONTEND_HANDOFF.md"
    examples = ROOT / "docs" / "api" / "FRONTEND_API_EXAMPLES.md"
    seal = ROOT / "docs" / "RELEASE_SEAL_BACKEND_RC.md"
    return {
        "openapi_contract": openapi.exists(),
        "frontend_handoff_doc": handoff.exists(),
        "api_examples_doc": examples.exists(),
        "release_seal_doc": seal.exists(),
        "ready_for_handoff": all(
            [openapi.exists(), handoff.exists(), examples.exists(), seal.exists()]
        ),
    }


def build_summary(metrics_dir: Path, db_url: str | None) -> dict:
    path, data = find_latest_metrics(metrics_dir)
    yearly = data.get("yearly", {}) if data else {}
    period = data.get("period", {}) if data else {}

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "repo_role": "backend / db / forecast ledger",
        "latest_metrics_file": str(path.relative_to(ROOT)) if path else None,
        "period": period,
        "metrics": {
            "smape": yearly.get("smape"),
            "mae": yearly.get("mae"),
            "rmse": yearly.get("rmse"),
            "wmape": yearly.get("wmape"),
            "evaluable_days": yearly.get("evaluable_days"),
        },
        "db": db_coverage(db_url) if db_url else {"configured": False},
        "api_handoff": api_handoff_status(),
    }
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="EFM3 latest metrics + DB coverage summary")
    ap.add_argument("--metrics-dir", type=Path, default=ROOT / "outputs" / "db_yearly_formal_sim")
    ap.add_argument("--db-url", default=os.environ.get("EFM3_DB_URL", ""))
    ap.add_argument("--output-md", type=Path, default=None)
    ap.add_argument("--output-json", type=Path, default=None)
    args = ap.parse_args(argv)

    db_url = args.db_url or None
    summary = build_summary(args.metrics_dir, db_url)

    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\n[written] {args.output_json}")

    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        m = summary["metrics"]
        lines = [
            "# EFM3 Latest Metrics Summary",
            "",
            f"- generated: {summary['generated_at']}",
            f"- latest metrics file: {summary['latest_metrics_file']}",
            f"- period: {summary['period']}",
            "",
            "## Metrics (yearly)",
            "",
            f"- SMAPE: {m['smape']}",
            f"- MAE: {m['mae']}",
            f"- RMSE: {m['rmse']}",
            f"- WMAPE: {m['wmape']}",
            f"- evaluable_days: {m['evaluable_days']}",
            "",
            "## DB coverage",
            "",
        ]
        db = summary["db"]
        for k, v in db.items():
            lines.append(f"- {k}: {v}")
        lines.append("")
        lines.append("## API handoff")
        lines.append("")
        for k, v in summary["api_handoff"].items():
            lines.append(f"- {k}: {v}")
        args.output_md.write_text("\n".join(lines), encoding="utf-8")
        print(f"[written] {args.output_md}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
