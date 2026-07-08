#!/usr/bin/env python
"""Audit the EFM3 MySQL ledger across a date range.

Queries all 8 operational tables, checks expectations (final_selected=24,
fusion=24, postflight-PASS, no forbidden outputs), and writes a structured
report (Markdown + JSON).

Usage:
    python tools/db_ops/db_monthly_audit.py ^
        --start-date 2026-01-01 --end-date 2026-02-28 ^
        --db-url "mysql+pymysql://root:PASS%23@127.0.0.1:3306/efm3" ^
        --output-md docs/experiments/e2e/audit_jan_feb.md ^
        --output-json outputs/db_monthly_dry_run/audit_jan_feb.json
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
from typing import Optional, Any
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("monthly_audit")


DB_URL = os.environ.get("EFM3_DB_URL", "")


# ── Connection helper ────────────────────────────────────────────

def _connect(db_url: str):
    import pymysql
    u = db_url.split("//", 1)[1]
    up, hp = u.split("@")
    user, pw = up.split(":")
    hp, dbn = hp.split("/")
    host, port = hp.split(":")
    pw = unquote(pw)
    return pymysql.connect(host=host, port=int(port), user=user, password=pw, database=dbn)


# ── Per-date audit ───────────────────────────────────────────────

@dataclass
class AuditDay:
    date: str
    run_id: str = ""
    run_status: str = ""
    delivery_status: str = ""
    exit_code: int = -1
    da_anchor_rows: int = 0
    official_baseline_rows: int = 0
    seasonal_router_rows: int = 0
    final_selected_rows: int = 0
    fusion_decision_rows: int = 0
    shadow_rows: int = 0
    data_update_runs: int = 0
    data_update_status: str = ""
    dataset_versions: int = 0
    dataset_version_status: str = ""
    postflight_checks: int = 0
    postflight_pass: int = 0
    postflight_fail: int = 0
    postflight_detail: list[dict] = field(default_factory=list)
    delivery_outputs: int = 0
    delivery_paths: list[str] = field(default_factory=list)
    forbidden_output: bool = False
    anomalies: list[str] = field(default_factory=list)
    result: str = "UNKNOWN"
    # Key check flags
    shadow_not_final_pass: bool = False
    hour_coverage_pass: bool = False
    selected_source_pass: bool = False


def audit_single(cur, target_date: str) -> AuditDay:
    a = AuditDay(date=target_date)

    # ── 1. efm_runs ─────────────────────────────────────────────
    cur.execute(
        "SELECT run_id, status, delivery_status, exit_code FROM efm_runs "
        "WHERE target_date=%s ORDER BY started_at DESC LIMIT 1",
        (target_date,),
    )
    r = cur.fetchone()
    if not r:
        a.anomalies.append("No efm_runs row for this date")
        return a
    a.run_id = r[0]
    a.run_status = r[1]
    a.delivery_status = r[2]
    a.exit_code = r[3]

    # ── 2. efm_data_update_runs ─────────────────────────────────
    cur.execute(
        "SELECT status FROM efm_data_update_runs WHERE target_date=%s ORDER BY started_at DESC LIMIT 1",
        (target_date,),
    )
    r = cur.fetchone()
    a.data_update_runs = 1 if r else 0
    a.data_update_status = r[0] if r else "N/A (no update run)"

    # ── 3. efm_dataset_versions ─────────────────────────────────
    cur.execute(
        "SELECT status FROM efm_dataset_versions WHERE target_date=%s ORDER BY leakage_cutoff DESC LIMIT 1",
        (target_date,),
    )
    r = cur.fetchone()
    a.dataset_versions = 1 if r else 0
    a.dataset_version_status = r[0] if r else "N/A"

    # ── 4. efm_predictions ──────────────────────────────────────
    a.da_anchor_rows = _count(cur, "efm_predictions", a.run_id, "AND stage='da_anchor'")
    a.official_baseline_rows = _count(cur, "efm_predictions", a.run_id, "AND stage='official_baseline'")
    a.seasonal_router_rows = _count(cur, "efm_predictions", a.run_id, "AND stage='seasonal_da_router'")
    a.final_selected_rows = _count(cur, "efm_predictions", a.run_id,
                                   "AND task='final' AND stage='final_selected' AND is_selected=1 AND is_shadow=0")
    a.shadow_rows = _count(cur, "efm_predictions", a.run_id, "AND is_shadow=1 AND is_selected=1")

    if a.final_selected_rows == 0 and a.da_anchor_rows == 0:
        a.anomalies.append("No predictions (data incomplete)")
    elif a.final_selected_rows != 24:
        a.anomalies.append(f"final_selected rows = {a.final_selected_rows} (expected 24)")

    # ── 5. efm_fusion_decisions ─────────────────────────────────
    a.fusion_decision_rows = _count(cur, "efm_fusion_decisions", a.run_id)
    if a.fusion_decision_rows == 0 and a.da_anchor_rows > 0:
        a.anomalies.append(f"fusion_decisions = {a.fusion_decision_rows} (expected 24)")
    elif a.fusion_decision_rows not in (0, 24):
        a.anomalies.append(f"fusion_decisions = {a.fusion_decision_rows} (expected 0 or 24)")

    # ── 6. efm_postflight_checks ────────────────────────────────
    cur.execute(
        "SELECT check_name, passed, details FROM efm_postflight_checks "
        "WHERE run_id=%s ORDER BY check_name",
        (a.run_id,),
    )
    checks = cur.fetchall()
    a.postflight_checks = len(checks)
    a.postflight_pass = sum(1 for c in checks if c[1])
    a.postflight_fail = sum(1 for c in checks if not c[1])
    a.postflight_detail = [{"check": c[0], "passed": bool(c[1]), "details": (c[2] or "")[:200]} for c in checks]

    for c in checks:
        if c[0] == "shadow_not_final":
            a.shadow_not_final_pass = bool(c[1])
            if not c[1]:
                a.anomalies.append("shadow_not_final FAIL — shadow leaked into final")
        if c[0] == "hour_range":
            a.hour_coverage_pass = bool(c[1])
        if c[0] == "selected_source":
            a.selected_source_pass = bool(c[1])

    if a.postflight_fail > 0 and a.da_anchor_rows > 0:
        a.anomalies.append(f"postflight: {a.postflight_pass}/{a.postflight_checks} passed (expected 8/8 for data-rich date)")

    # ── 7. efm_delivery_outputs ────────────────────────────────
    cur.execute(
        "SELECT output_type, output_path FROM efm_delivery_outputs WHERE run_id=%s",
        (a.run_id,),
    )
    outputs = cur.fetchall()
    a.delivery_outputs = len(outputs)
    a.delivery_paths = [o[1] for o in outputs]

    for o in outputs:
        path = (o[1] or "").lower()
        if "formal" in path or "submission_ready" in path:
            a.forbidden_output = True
            a.anomalies.append(f"FORBIDDEN output: {o[1]}")

    if a.forbidden_output:
        a.anomalies.append("Formal submission found — this is a forbidden output for dry_run mode!")

    # ── Result classification ──────────────────────────────────
    if a.forbidden_output:
        a.result = "FAIL"
    elif a.da_anchor_rows == 0:
        a.result = "NO_DATA"
    elif a.final_selected_rows == 24 and a.postflight_fail == 0:
        a.result = "PASS"
    elif a.final_selected_rows == 24 and a.postflight_fail > 0:
        a.result = "WARN"
    else:
        a.result = "PARTIAL"

    return a


def _count(cur, table: str, run_id: str, extra: str = "") -> int:
    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id=%s {extra}", (run_id,))
    return cur.fetchone()[0]


# ── Date range iterator ──────────────────────────────────────────

def daterange(start: str, end: str):
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    for n in range((e - s).days + 1):
        yield (s + timedelta(n)).isoformat()


# ── Reporting ────────────────────────────────────────────────────

def generate_markdown(audits: list[AuditDay]) -> str:
    lines = ["# EFM3 Monthly DB Audit Report", ""]
    lines.append(f"Period: {audits[0].date} ~ {audits[-1].date}")
    lines.append(f"Total dates: {len(audits)}")
    lines.append(f"PASS: {sum(1 for a in audits if a.result == 'PASS')}")
    lines.append(f"NO_DATA: {sum(1 for a in audits if a.result == 'NO_DATA')}")
    lines.append(f"PARTIAL/WARN: {sum(1 for a in audits if a.result in ('PARTIAL','WARN'))}")
    lines.append(f"FAIL: {sum(1 for a in audits if a.result == 'FAIL')}")
    lines.append(f"Anomalies: {sum(1 for a in audits if a.anomalies)}")
    lines.append("")

    # Table: Daily DB Results
    lines.append("## Daily DB Results")
    lines.append("| Date | run status | delivery | exit | da_anchor | final_sel | fusion | pflight | output | result |")
    lines.append("| ---- | ---------- | -------- | ---: | --------: | --------: | -----: | ------: | -----: | ------ |")
    for a in audits:
        pf_str = f"{a.postflight_pass}/{a.postflight_checks}" if a.postflight_checks else "-"
        lines.append(
            f"| {a.date} | {a.run_status} | {a.delivery_status} | {a.exit_code} "
            f"| {a.da_anchor_rows} | {a.final_selected_rows} "
            f"| {a.fusion_decision_rows} | {pf_str} "
            f"| {a.delivery_outputs} | {a.result} |"
        )
    lines.append("")

    # Anomalies
    anomalous = [a for a in audits if a.anomalies]
    if anomalous:
        lines.append("## Anomalies")
        lines.append("| Date | Anomalies |")
        lines.append("| ---- | --------- |")
        for a in anomalous:
            for anom in a.anomalies:
                lines.append(f"| {a.date} | {anom} |")
        lines.append("")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="EFM3 Monthly DB Audit")
    ap.add_argument("--start-date", required=True)
    ap.add_argument("--end-date", required=True)
    ap.add_argument("--db-url", default=DB_URL)
    ap.add_argument("--output-md", type=Path, default=None)
    ap.add_argument("--output-json", type=Path, default=None)
    args = ap.parse_args()

    if not args.db_url:
        ap.error("EFM3_DB_URL not set; pass --db-url or set the env variable.")

    conn = _connect(args.db_url)
    cur = conn.cursor()

    audits: list[AuditDay] = []
    dates = list(daterange(args.start_date, args.end_date))
    logger.info("Auditing %d dates...", len(dates))

    for i, d in enumerate(dates, 1):
        a = audit_single(cur, d)
        audits.append(a)
        if i % 20 == 0:
            logger.info("  %d/%d — anomalies so far: %d", i, len(dates),
                        sum(1 for x in audits if x.anomalies))

    conn.close()

    # Output artifacts
    report = {"period": {"start": args.start_date, "end": args.end_date},
              "summary": {
                  "total": len(audits),
                  "pass": sum(1 for a in audits if a.result == "PASS"),
                  "no_data": sum(1 for a in audits if a.result == "NO_DATA"),
                  "partial": sum(1 for a in audits if a.result == "PARTIAL"),
                  "warn": sum(1 for a in audits if a.result == "WARN"),
                  "fail": sum(1 for a in audits if a.result == "FAIL"),
                  "anomalies_total": sum(1 for a in audits if a.anomalies),
              },
              "days": [asdict(a) for a in audits]}

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("JSON written to %s", args.output_json)

    md = generate_markdown(audits)
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(md, encoding="utf-8")
        logger.info("Markdown written to %s", args.output_md)

    # Print summary to console
    anom_count = sum(1 for a in audits if a.anomalies)
    logger.info(
        "Audit complete: %d dates | PASS=%d NO_DATA=%d PARTIAL/WARN=%d FAIL=%d anomalies=%d",
        len(audits),
        report["summary"]["pass"],
        report["summary"]["no_data"],
        report["summary"]["partial"] + report["summary"]["warn"],
        report["summary"]["fail"],
        anom_count,
    )
    for a in audits:
        if a.anomalies:
            print(f"  [{a.date}] {a.result}: {'; '.join(a.anomalies)}")


if __name__ == "__main__":
    main()
