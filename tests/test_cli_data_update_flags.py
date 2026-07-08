"""CLI data update flags — default-off and compatibility."""

from __future__ import annotations

import argparse
from cli.parser import build_parser


def _parse(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def test_update_data_default_false():
    args = _parse(["2025-11-03"])
    assert getattr(args, "update_data", False) is False, "update_data must default False"


def test_scan_only_default_false():
    args = _parse(["2025-11-03"])
    assert getattr(args, "scan_only", False) is False


def test_data_source_default():
    args = _parse(["2025-11-03"])
    assert getattr(args, "data_source", "all") == "all"


def test_update_data_flag():
    args = _parse(["2025-11-03", "--update-data"])
    assert getattr(args, "update_data") is True


def test_scan_only_flag():
    args = _parse(["2025-11-03", "--scan-only"])
    assert getattr(args, "scan_only") is True


def test_data_source_flag():
    args = _parse(["2025-11-03", "--data-source", "two_five_reference"])
    assert getattr(args, "data_source") == "two_five_reference"


def test_target_start_end_date():
    args = _parse(["2025-11-03", "--target-start-date", "2025-11-01", "--target-end-date", "2025-11-30"])
    assert getattr(args, "target_start_date") == "2025-11-01"
    assert getattr(args, "target_end_date") == "2025-11-30"


def test_data_root():
    args = _parse(["2025-11-03", "--data-root", "/custom/path"])
    assert getattr(args, "data_root") == "/custom/path"


def test_old_command_unchanged():
    args = _parse(["2025-11-03"])
    assert args.pos_date == "2025-11-03"
    assert args.pipeline == "ledger_full"


def test_old_command_with_pipeline():
    args = _parse(["2025-11-03", "--pipeline", "ledger_smoke"])
    assert args.pipeline == "ledger_smoke"


def test_new_flags_with_old_command():
    args = _parse(["2025-11-03", "--update-data", "--use-db", "--mode", "dry_run"])
    assert args.pos_date == "2025-11-03"
    assert getattr(args, "update_data") is True
    assert getattr(args, "use_db") is True
    assert getattr(args, "mode") == "dry_run"
