#!/usr/bin/env python
"""Run a full-month DB dry-run for the EFM3 DB-ledger chain.

For each date in [start_date, end_date], calls ``run_full_chain`` directly
(lightweight — no heavy legacy training). Captures per-date run_id, status,
and step details, then writes a manifest + summary to *report_dir*.

Usage:
    python scripts/run_monthly_db_dry_run.py ^
        --start-date 2026-01-01 --end-date 2026-01-31 ^
        --db-url "mysql+pymysql://root:PASS%23@127.0.0.1:3306/efm3" ^
        --chain seasonal_da_router ^
        --continue-on-fail ^
        --report-dir outputs/db_monthly_dry_run/2026-01
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# Add repo root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("monthly_dry_run")


DB_URL = os.environ.get("EFM3_DB_URL", "")


@dataclass
class DayResult:
    date: str
    run_id: str = ""
    status: str = "SKIPPED"
    delivery_status: str = ""
    exit_code: int = -1
    steps: dict = field(default_factory=dict)
    detail: str = ""


def daterange(start: str, end: str):
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    for n in range((e - s).days + 1):
        yield (s + timedelta(n)).isoformat()


def run_date(target_date: str, db_url: str, chain: str, mode: str, update_data: bool) -> DayResult:
    result = DayResult(date=target_date)
    try:
        from pipelines.full_chain_orchestrator import run_full_chain

        config = {}
        if chain:
            config["chain"] = chain

        # formal_sim: strict guards but no submission export
        is_formal = mode in ("formal", "formal_sim")
        export_sub = mode == "formal" and update_data  # only formal+update exports

        chain_result = run_full_chain(
            target_date=target_date,
            mode=mode,
            use_db=True,
            db_url=db_url,
            export_submission=export_sub,
            export_report=False,
            config=config,
        )
        result.run_id = chain_result.get("run_id", "")
        result.status = chain_result.get("status", "UNKNOWN")
        result.delivery_status = chain_result.get("delivery_status", "")
        result.exit_code = chain_result.get("exit_code", -1)
        result.steps = {
            k: {"status": v.get("status"), "detail": str(v.get("detail", ""))[:200]}
            for k, v in chain_result.get("steps", {}).items()
        }
        result.detail = (
            f"steps_ok={all(s.get('status')=='ok' for s in result.steps.values())} "
            f"preds={_count_preds(db_url, result.run_id)}"
        )
    except Exception as exc:
        result.status = "FAIL"
        result.detail = str(exc)[:300]
        logger.exception("[%s] Dry-run failed", target_date)
    return result


def _count_preds(db_url: str, run_id: str) -> int:
    """Quick count of predictions for a run_id (optional, silent on failure)."""
    try:
        import pymysql
        from urllib.parse import unquote

        u = db_url.split("//", 1)[1]
        up, hp = u.split("@")
        user, pw = up.split(":")
        hp, dbn = hp.split("/")
        host, port = hp.split(":")
        pw = unquote(pw)
        c = pymysql.connect(host=host, port=int(port), user=user, password=pw, database=dbn)
        cur = c.cursor()
        cur.execute("SELECT COUNT(*) FROM efm_predictions WHERE run_id=%s", (run_id,))
        n = cur.fetchone()[0]
        c.close()
        return n
    except Exception:
        return -1


def write_manifest(results: list[DayResult], report_dir: Path):
    report_dir.mkdir(parents=True, exist_ok=True)
    # Manifest
    manifest = {
        "total": len(results),
        "pass": sum(1 for r in results if r.status == "COMPLETE"),
        "fail": sum(1 for r in results if r.status == "FAIL"),
        "partial": sum(1 for r in results if r.exit_code != 0 and r.status not in ("SKIPPED", "FAIL")),
        "days": [asdict(r) for r in results],
    }
    manifest_path = report_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Manifest written to %s", manifest_path)

    # Summary markdown
    md_lines = [
        f"# Monthly DB Dry-run: {results[0].date} ~ {results[-1].date}",
        "",
        f"Total days: {len(results)}  |  PASS: {manifest['pass']}  |  "
        f"FAIL: {manifest['fail']}  |  PARTIAL: {manifest['partial']}",
        "",
        "| Date | run_id | status | delivery | exit | detail |",
        "| ---- | ------ | ------ | -------- | ---: | ------ |",
    ]
    for r in results:
        rid_short = r.run_id[:20] + "…" if len(r.run_id) > 20 else r.run_id
        md_lines.append(
            f"| {r.date} | {rid_short} | {r.status} | {r.delivery_status} | "
            f"{r.exit_code} | {r.detail} |"
        )

    summary_path = report_dir / "run_summary.md"
    summary_path.write_text("\n".join(md_lines), encoding="utf-8")
    logger.info("Summary written to %s", summary_path)


def main():
    ap = argparse.ArgumentParser(description="EFM3 Monthly DB Dry-run")
    ap.add_argument("--start-date", required=True)
    ap.add_argument("--end-date", required=True)
    ap.add_argument("--db-url", default=DB_URL, help="MySQL URL (default $EFM3_DB_URL)")
    ap.add_argument("--chain", default="seasonal_da_router")
    ap.add_argument("--mode", default="dry_run", choices=["dry_run", "shadow", "formal", "formal_sim"],
        help="Run mode for each date (default: dry_run).")
    ap.add_argument("--update-data", action="store_true", help="Pass --update-data to main.py (not used with run_full_chain)")
    ap.add_argument("--continue-on-fail", action="store_true", help="Continue on per-day failure")
    ap.add_argument("--report-dir", default="outputs/db_monthly_dry_run")
    args = ap.parse_args()

    if not args.db_url:
        ap.error("EFM3_DB_URL not set; pass --db-url or set the env variable.")

    results: list[DayResult] = []
    dates = list(daterange(args.start_date, args.end_date))
    logger.info("Monthly DB dry-run: %s ~ %s (%d dates)", args.start_date, args.end_date, len(dates))

    for i, d in enumerate(dates, 1):
        logger.info("[%d/%d] %s ...", i, len(dates), d)
        r = run_date(d, args.db_url, args.chain, args.mode, args.update_data)
        results.append(r)
        if r.status == "FAIL" and not args.continue_on_fail:
            logger.error("Aborting at %s (--continue-on-fail not set)", d)
            break
        if i % 10 == 0:
            logger.info("  — %d/%d done, running mid-flush", i, len(dates))

    report_dir = Path(args.report_dir)
    write_manifest(results, report_dir)
    logger.info("Done — %d days, PASS=%d, FAIL=%d", len(results),
                sum(1 for r in results if r.status == "COMPLETE"),
                sum(1 for r in results if r.status == "FAIL"))


if __name__ == "__main__":
    main()
