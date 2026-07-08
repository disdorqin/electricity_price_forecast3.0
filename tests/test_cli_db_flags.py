"""Tests for CLI DB-related flags.

Ensures that the new DB-ledger / full-chain flags are properly defined
in the argparse parser and that old commands remain backward-compatible.
"""

from cli.parser import build_parser, normalize_date_args


# ── Fixture ──────────────────────────────────────────────────────────────────


def _parse(raw_args):
    """Parse raw CLI-style args and return the argparse Namespace."""
    parser = build_parser()
    args = parser.parse_args(raw_args)
    return args


def _parse_and_normalize(raw_args):
    """Parse and run normalize_date_args (mimics the real CLI flow)."""
    parser = build_parser()
    args = parser.parse_args(raw_args)
    normalize_date_args(args, parser)
    return args


# ── 1. Import check ──────────────────────────────────────────────────────────


def test_build_parser_imports():
    """build_parser can be imported from cli.parser."""
    assert callable(build_parser), "build_parser should be a callable function"


# ── 2. Default values for new DB flags ────────────────────────────────────────


def test_default_values():
    """All DB-related flags have the expected default values."""
    args = _parse([])  # minimal invocation

    # DB flags
    assert args.use_db is False
    assert args.db_url is None
    assert args.init_db is False
    assert args.mode == "dry_run"
    assert args.chain == "official"
    assert args.export_submission is False
    assert args.export_report is False

    # Existing basic flags (sanity)
    assert args.pipeline == "ledger_full"
    assert args.date is None
    assert args.pos_date is None


# ── 3. Individual flag setting ────────────────────────────────────────────────


class TestSetFlags:
    """Each new DB flag sets correctly when provided on the command line."""

    def test_use_db(self):
        args = _parse(["--use-db"])
        assert args.use_db is True

        args = _parse([])
        assert args.use_db is False

    def test_db_url(self):
        url = "mysql+pymysql://user:pass@localhost:3306/efm3"
        args = _parse(["--db-url", url])
        assert args.db_url == url

        args = _parse([])
        assert args.db_url is None

    def test_init_db(self):
        args = _parse(["--init-db"])
        assert args.init_db is True

        args = _parse([])
        assert args.init_db is False

    def test_mode_values(self):
        # Default
        args = _parse([])
        assert args.mode == "dry_run"

        for mode in ("dry_run", "shadow", "formal"):
            args = _parse(["--mode", mode])
            assert args.mode == mode, f"Expected --mode {mode!r}"

    def test_chain_values(self):
        # Default
        args = _parse([])
        assert args.chain == "official"

        for chain in ("official", "seasonal_da_router"):
            args = _parse(["--chain", chain])
            assert args.chain == chain, f"Expected --chain {chain!r}"

    def test_export_submission(self):
        args = _parse(["--export-submission"])
        assert args.export_submission is True

        args = _parse([])
        assert args.export_submission is False

    def test_export_report(self):
        args = _parse(["--export-report"])
        assert args.export_report is True

        args = _parse([])
        assert args.export_report is False


# ── 4. Old-command backward-compatibility ─────────────────────────────────────


class TestOldCommandCompatibility:
    """Commands that worked before the DB flags were added must still work."""

    def test_positional_date_only(self):
        """main.py 2025-11-03"""
        args = _parse_and_normalize(["2025-11-03"])
        assert args.date == "2025-11-03"
        assert args.pipeline == "ledger_full"

    def test_date_flag(self):
        """main.py --date 2025-11-03"""
        args = _parse_and_normalize(["--date", "2025-11-03"])
        assert args.date == "2025-11-03"
        assert args.pipeline == "ledger_full"

    def test_positional_date_with_pipeline(self):
        """main.py 2025-11-03 --pipeline ledger_smoke"""
        args = _parse_and_normalize(["2025-11-03", "--pipeline", "ledger_smoke"])
        assert args.date == "2025-11-03"
        assert args.pipeline == "ledger_smoke"

    def test_all_old_flags_still_present(self):
        """Pre-existing flags should still have their expected defaults."""
        args = _parse(["2025-11-03"])
        assert args.target == "both"
        assert args.models == "all"
        assert args.force is False
        assert args.seed == 42
        assert args.deterministic is False
        assert args.data_path == "data/shandong_pmos_hourly.xlsx"
        assert args.ledger_root == "outputs/ledger"
        assert args.runs_root == "outputs/runs"
