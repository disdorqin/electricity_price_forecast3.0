"""
Delivery report — formatted terminal output and file artifacts for daily
and range delivery status.

Each report covers:
  - Delivery status  (NORMAL / DEGRADED_DELIVERED / FAILED_NO_DELIVERY)
  - Five-stage pipeline results
  - Postflight validation
  - Next-day ledger readiness
  - Fallback information (if any)
  - Action required
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def write_daily_delivery_report(run_dir: Path, manifest: dict) -> None:
    """Write ``delivery_report.json`` and ``delivery_report.md``.

    Parameters
    ----------
    run_dir : Path
        The single-day run directory (``outputs/runs/YYYY-MM-DD``).
    manifest : dict
        The full run manifest.
    """
    report = _build_daily_report(manifest)

    json_path = run_dir / "delivery_report.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    md_path = run_dir / "delivery_report.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_daily_markdown(report, manifest, run_dir))

    logger.info(f"Delivery report JSON -> {json_path}")
    logger.info(f"Delivery report MD  -> {md_path}")


def print_daily_delivery_report(manifest: dict) -> None:
    """Print a coloured (ANSI-free) terminal delivery report."""
    report = _build_daily_report(manifest)
    target_date = manifest.get("target_date", "unknown")

    lines = [
        "=" * 60,
        f"DAILY DELIVERY REPORT — {target_date}",
        "=" * 60,
        "",
        f"DELIVERY STATUS: {report['delivery_status']}",
        f"EXIT CODE: {report['exit_code']}",
        "",
        "[1] FIVE-STAGE PIPELINE",
    ]

    for stage_name, stage_info in manifest.get("stages", {}).items():
        s = stage_info.get("status", "missing")
        lines.append(f"  {stage_name:<22} {s.upper()}")

    lines.extend([
        "",
        "[2] POSTFLIGHT CHECK",
    ])

    pf = report.get("postflight", {})
    pf_status = pf.get("status", "NOT RUN")
    pf_errors = pf.get("errors", [])

    lines.append(f"  submission_ready.csv     {'PASS' if pf_status == 'PASS' else 'FAIL'}")
    if pf_errors:
        for e in pf_errors[:5]:
            lines.append(f"    - {e}")
        if len(pf_errors) > 5:
            lines.append(f"    - ... and {len(pf_errors) - 5} more errors")

    lines.extend([
        "",
        "[3] NEXT DAY LEDGER READINESS",
    ])

    ndr = report.get("next_day_readiness", {})
    ndr_status = ndr.get("status", "NOT RUN")
    lines.append(f"  status: {ndr_status}")
    if ndr.get("window_start"):
        lines.append(f"  window: {ndr.get('window_start')} .. {ndr.get('window_end')}")
    missing_count = len(ndr.get("errors", []))
    lines.append(f"  missing count: {missing_count}")

    lines.extend([
        "",
        "[4] FALLBACK",
    ])

    fb = report.get("fallback", {})
    fb_used = fb.get("used", False)
    lines.append(f"  used: {'yes' if fb_used else 'no'}")
    if fb_used:
        lines.append(f"  method: {fb.get('method', 'N/A')}")
        lines.append(f"  reason: {fb.get('reason', 'N/A')}")

    lines.extend([
        "",
        "[5] OUTPUTS",
        f"  final file:",
        f"    {report.get('submission_ready_path', 'N/A')}",
        f"  run manifest:",
        f"    {report.get('manifest_path', 'N/A')}",
        f"  delivery report:",
        f"    {report.get('delivery_report_path', 'N/A')}",
        "",
        "[6] ACTION REQUIRED",
    ])

    ds = report["delivery_status"]
    if ds == "NORMAL":
        lines.append("  None.")
    elif ds == "DEGRADED_DELIVERED":
        lines.extend([
            "  - Today has an emergency output, but it is not a normal model delivery.",
            "  - Re-run normal chain after fixing the issue:",
            f"    python main.py {target_date} --force",
            "  - Check:",
            f"    outputs/runs/{target_date}/run_manifest.json",
            f"    outputs/runs/{target_date}/final/fallback_report.md",
        ])
    elif ds == "FAILED_NO_DELIVERY":
        lines.extend([
            "  - Check postflight errors",
            "  - Check data_path",
            "  - Check ledger window",
        ])

    lines.append("=" * 60)
    print("\n".join(lines))


def write_range_delivery_report(range_dir: Path, range_manifest: dict) -> None:
    """Write range-level ``range_delivery_report.json`` and ``.md``."""
    report = _build_range_report(range_manifest)

    json_path = range_dir / "range_delivery_report.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    md_path = range_dir / "range_delivery_report.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_range_markdown(report, range_manifest, range_dir))

    logger.info(f"Range delivery report JSON -> {json_path}")
    logger.info(f"Range delivery report MD  -> {md_path}")


def print_range_delivery_report(range_manifest: dict) -> None:
    """Print range-level terminal report."""
    report = _build_range_report(range_manifest)
    start = range_manifest.get("start_date", "?")
    end = range_manifest.get("end_date", "?")

    lines = [
        "=" * 60,
        f"RANGE DELIVERY REPORT — {start} to {end}",
        "=" * 60,
        "",
        f"RANGE DELIVERY STATUS: {report['delivery_status']}",
        f"RANGE STATUS: {range_manifest.get('status', '?')}",
        f"EXIT CODE: {report['exit_code']}",
        "",
        f"total_days:    {report['total_days']}",
        f"completed:     {report['completed_days']}",
        f"degraded:      {report['degraded_days']}",
        f"failed:        {report['failed_days']}",
        f"skipped:       {report['skipped_days']}",
        "",
        "DAILY RESULTS:",
    ]

    for dr in report.get("daily_results", []):
        marker = ""
        if dr["delivery_status"] == "DEGRADED_DELIVERED":
            marker = " [DEGRADED]"
        elif dr["delivery_status"] == "FAILED_NO_DELIVERY":
            marker = " [FAILED]"
        lines.append(
            f"  {dr['date']:<12} status={dr['status']:<10} "
            f"delivery={dr['delivery_status']:<20}{marker}"
        )

    lines.extend([
        "",
        f"DAYS NEEDING REPAIR: {len(report['days_needing_repair'])}",
    ])

    for d in report["days_needing_repair"]:
        lines.append(f"  - {d}")

    lines.append("")
    if report["delivery_status"] == "DEGRADED_DELIVERED":
        lines.append(
            "  Some days delivered via emergency fallback. "
            "Those days should be re-run with --force to "
            "restore normal ledger continuity."
        )
    elif report["delivery_status"] == "FAILED_NO_DELIVERY":
        lines.append(
            "  Some days have no usable output. "
            "Check individual run manifests for details."
        )

    lines.append("=" * 60)
    print("\n".join(lines))


# ---------------------------------------------------------------------------
# Internal report builders
# ---------------------------------------------------------------------------


def _build_daily_report(manifest: dict) -> dict:
    """Build a structured daily delivery report from the manifest."""
    target_date = manifest.get("target_date", "unknown")
    ds = manifest.get("delivery_status", "UNKNOWN")

    exit_code = _delivery_exit_code(ds)

    # Postflight
    pf = manifest.get("postflight", {})

    # Fallback
    fb = manifest.get("fallback", {})
    fallback = {
        "used": fb.get("fallback_used", False) or fb.get("success", False),
        "method": fb.get("fallback_method", "N/A"),
        "reason": fb.get("reason", "N/A"),
    }

    # Next-day readiness
    ndr = manifest.get("next_day_readiness", {})

    return {
        "report_type": "daily",
        "target_date": target_date,
        "delivery_status": ds,
        "exit_code": exit_code,
        "postflight": {
            "status": pf.get("status", "NOT RUN"),
            "errors": pf.get("errors", []),
            "warnings": pf.get("warnings", []),
        },
        "fallback": fallback,
        "next_day_readiness": {
            "status": ndr.get("status", "NOT RUN"),
            "window_start": ndr.get("window_start", ""),
            "window_end": ndr.get("window_end", ""),
            "errors": ndr.get("errors", []),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "submission_ready_path": pf.get("submission_ready_path", ""),
        "manifest_path": pf.get("manifest_path", ""),
        "delivery_report_path": "",
    }


def _build_range_report(range_manifest: dict) -> dict:
    """Build a structured range delivery report from the range manifest."""
    daily_results = []
    total = range_manifest.get("total_days", 0)
    completed = range_manifest.get("completed_days", 0)
    failed = range_manifest.get("failed_days", 0)
    skipped = range_manifest.get("skipped_days", 0)
    degraded = range_manifest.get("degraded_days", 0)

    days_needing_repair: list[str] = []

    for dr in range_manifest.get("daily_results", []):
        dd = dr.get("delivery_status", dr.get("status", "?"))
        daily_results.append({
            "date": dr["date"],
            "status": dr.get("status", "?"),
            "delivery_status": dd,
        })
        if dd == "FAILED_NO_DELIVERY":
            days_needing_repair.append(dr["date"])

    ds = range_manifest.get("delivery_status", "UNKNOWN")
    exit_code = _delivery_exit_code(ds)

    return {
        "report_type": "range",
        "start_date": range_manifest.get("start_date", ""),
        "end_date": range_manifest.get("end_date", ""),
        "delivery_status": ds,
        "range_status": range_manifest.get("status", ""),
        "exit_code": exit_code,
        "total_days": total,
        "completed_days": completed,
        "degraded_days": degraded,
        "failed_days": failed,
        "skipped_days": skipped,
        "daily_results": daily_results,
        "days_needing_repair": days_needing_repair,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _delivery_exit_code(delivery_status: str) -> int:
    """Map delivery status to exit code.

    NORMAL           -> 0
    DEGRADED_DELIVERED -> 2
    FAILED_NO_DELIVERY -> 1
    """
    if delivery_status == "NORMAL":
        return 0
    elif delivery_status == "DEGRADED_DELIVERED":
        return 2
    else:
        return 1


# ---------------------------------------------------------------------------
# Markdown builders
# ---------------------------------------------------------------------------


def _daily_markdown(report: dict, manifest: dict, run_dir: Path) -> str:
    """Build a markdown delivery report for single day."""
    target_date = report["target_date"]
    lines = [
        f"# Daily Delivery Report — {target_date}",
        "",
        f"**Delivery Status:** {report['delivery_status']}",
        f"**Exit Code:** {report['exit_code']}",
        "",
        "## 1. Five-Stage Pipeline",
        "| Stage | Status |",
        "|---|---|",
    ]

    for stage_name, stage_info in manifest.get("stages", {}).items():
        s = stage_info.get("status", "missing")
        lines.append(f"| {stage_name} | {s} |")

    lines.extend([
        "",
        "## 2. Postflight Check",
        f"**Status:** {report['postflight']['status']}",
    ])

    if report["postflight"]["errors"]:
        lines.append("### Errors")
        for e in report["postflight"]["errors"][:10]:
            lines.append(f"- {e}")

    lines.extend([
        "",
        "## 3. Next Day Ledger Readiness",
        f"**Status:** {report['next_day_readiness']['status']}",
        f"**Window:** {report['next_day_readiness']['window_start']} .. {report['next_day_readiness']['window_end']}",
        f"**Missing count:** {len(report['next_day_readiness']['errors'])}",
        "",
        "## 4. Fallback",
        f"**Used:** {'Yes' if report['fallback']['used'] else 'No'}",
    ])

    if report["fallback"]["used"]:
        lines.append(f"**Method:** {report['fallback']['method']}")
        lines.append(f"**Reason:** {report['fallback']['reason']}")

    lines.extend([
        "",
        "## 5. Outputs",
    ])
    sr_path = report.get("submission_ready_path", "")
    if sr_path:
        lines.append(f"- Submission ready: `{sr_path}`")
    mp = report.get("manifest_path", "")
    if mp:
        lines.append(f"- Run manifest: `{mp}`")
    lines.append(f"- Delivery report: `{run_dir / 'delivery_report.md'}`")

    lines.extend([
        "",
        "## 6. Action Required",
    ])

    ds = report["delivery_status"]
    if ds == "NORMAL":
        lines.append("None.")
    elif ds == "DEGRADED_DELIVERED":
        lines.append(
            "- This is an **emergency fallback delivery**, not a normal model output.\n"
            f"- Re-run normal chain: `python main.py {target_date} --force`\n"
            f"- Check: `{run_dir / 'final' / 'fallback_report.md'}`"
        )
    elif ds == "FAILED_NO_DELIVERY":
        lines.append(
            "- No usable output.\n"
            "- Check postflight errors, data_path, and ledger window."
        )

    lines.append("")
    return "\n".join(lines)


def _range_markdown(report: dict, range_manifest: dict, range_dir: Path) -> str:
    """Build a markdown delivery report for range."""
    start = report["start_date"]
    end = report["end_date"]
    lines = [
        f"# Range Delivery Report — {start} to {end}",
        "",
        f"**Delivery Status:** {report['delivery_status']}",
        f"**Range Status:** {report['range_status']}",
        f"**Exit Code:** {report['exit_code']}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total days | {report['total_days']} |",
        f"| Completed | {report['completed_days']} |",
        f"| Degraded | {report['degraded_days']} |",
        f"| Failed | {report['failed_days']} |",
        f"| Skipped | {report['skipped_days']} |",
        "",
        "## Daily Results",
        "| Date | Status | Delivery Status |",
        "|---|---|---|",
    ]

    for dr in report["daily_results"]:
        lines.append(f"| {dr['date']} | {dr['status']} | {dr['delivery_status']} |")

    lines.extend([
        "",
        "## Days Needing Repair",
    ])

    if report["days_needing_repair"]:
        for d in report["days_needing_repair"]:
            lines.append(f"- {d}")
    else:
        lines.append("None.")

    lines.append("")
    return "\n".join(lines)
