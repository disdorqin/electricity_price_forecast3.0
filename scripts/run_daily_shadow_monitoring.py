#!/usr/bin/env python3
"""
EFM3 Daily Shadow Monitoring — automated daily ops.

Steps:
1. Update data (scan/import)
2. Build dataset version
3. Run dry_run full chain
4. Run DB health check
5. Run shadow safety check
6. Export markdown summary

Usage:
    python scripts/run_daily_shadow_monitoring.py --target-date 2026-07-03 --use-db
"""

import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_URL = os.environ.get("EFM3_DB_URL", "")


def _redact(url):
    if "@" not in url:
        return url
    prefix = url.split("://")[0] if "://" in url else ""
    rest = url.split("://")[1] if "://" in url else url
    user_pass, host = rest.split("@", 1)
    if ":" in user_pass:
        user = user_pass.split(":")[0]
        return f"{prefix}://{user}:****@{host}" if prefix else f"{user}:****@{host}"
    return url


def run_daily_shadow_monitoring(
    target_date: str,
    db_url: str = "",
    data_source: str = "all",
    with_p3_shadow: bool = False,
    with_selector_shadow: bool = False,
    report_dir: str = "outputs/db_shadow_monitoring",
    fail_on_warning: bool = False,
) -> dict:
    report_path = Path(report_dir) / target_date
    report_path.mkdir(parents=True, exist_ok=True)

    results = {
        "target_date": target_date,
        "started_at": datetime.now().isoformat(),
        "steps": {},
        "status": "RUNNING",
    }

    # Step 1: Update data (scan/import)
    try:
        from pipelines.data_update_pipeline import run_data_update
        update_result = run_data_update(
            target_date=target_date,
            source=data_source,
            scan_only=False,
            db_url=db_url or DB_URL,
        )
        results["steps"]["data_update"] = {
            "status": update_result.get("status", "FAIL"),
            "files_imported": update_result.get("files_imported", 0),
        }
    except Exception as e:
        results["steps"]["data_update"] = {"status": "FAIL", "error": str(e)}
        if fail_on_warning:
            raise

    # Step 2: Dry-run full chain
    try:
        from pipelines.full_chain_orchestrator import run_full_chain
        chain_result = run_full_chain(
            target_date=target_date,
            mode="dry_run",
            use_db=True,
            db_url=db_url or DB_URL,
            export_submission=False,
            config={"enable_p3_shadow": with_p3_shadow, "enable_selector_shadow": with_selector_shadow},
        )
        results["steps"]["full_chain"] = {
            "status": chain_result.get("status", "FAIL"),
            "run_id": chain_result.get("run_id"),
        }
    except Exception as e:
        results["steps"]["full_chain"] = {"status": "FAIL", "error": str(e)}

    # Step 3: DB health check
    try:
        from tools.db_ops.db_health_check import health_check
        health = health_check(db_url or DB_URL)
        results["steps"]["health_check"] = {
            "status": health.get("status", "FAIL"),
        }
    except Exception as e:
        results["steps"]["health_check"] = {"status": "FAIL", "error": str(e)}

    # Step 4: Shadow safety check
    try:
        from tools.db_ops.db_verify_shadow_safety import verify_shadow_safety
        safety = verify_shadow_safety(db_url or DB_URL)
        results["steps"]["shadow_safety"] = {
            "status": safety.get("status", "FAIL"),
        }
    except Exception as e:
        results["steps"]["shadow_safety"] = {"status": "FAIL", "error": str(e)}

    # Determine overall status
    all_ok = all(s.get("status") in ("OK", "COMPLETE", "PASS", "PARTIAL")
                 for s in results["steps"].values())
    results["status"] = "COMPLETE" if all_ok else "PARTIAL"
    results["finished_at"] = datetime.now().isoformat()

    # Write reports
    with open(report_path / "manifest.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    summary_lines = [
        f"# Daily Shadow Monitoring — {target_date}\n",
        f"## Status: {results['status']}\n",
        "| Step | Status | Detail |",
        "| ---- | ------ | ------ |",
    ]
    for step, info in results["steps"].items():
        detail = info.get("run_id", info.get("files_imported", info.get("error", "")))
        summary_lines.append(f"| {step} | {info.get('status', '?')} | {detail} |")

    with open(report_path / "run_summary.md", "w") as f:
        f.write("\n".join(summary_lines))

    print(f"\nDaily Shadow Monitoring: {results['status']}")
    for step, info in results["steps"].items():
        print(f"  {step}: {info.get('status')}")
    print(f"Reports: {report_path}")

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--use-db", action="store_true")
    parser.add_argument("--db-url", default="")
    parser.add_argument("--data-source", default="all")
    parser.add_argument("--with-p3-shadow", action="store_true")
    parser.add_argument("--with-selector-shadow", action="store_true")
    parser.add_argument("--report-dir", default="outputs/db_shadow_monitoring")
    parser.add_argument("--fail-on-warning", action="store_true")
    args = parser.parse_args()

    run_daily_shadow_monitoring(
        target_date=args.target_date,
        db_url=args.db_url,
        data_source=args.data_source,
        with_p3_shadow=args.with_p3_shadow,
        with_selector_shadow=args.with_selector_shadow,
        report_dir=args.report_dir,
        fail_on_warning=args.fail_on_warning,
    )
