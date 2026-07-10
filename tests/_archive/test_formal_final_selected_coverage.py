"""Formal/formal_sim guard: final_selected rows must be 24."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

DB_URL = os.environ.get("EFM3_TEST_DB_URL") or os.environ.get("EFM3_DB_URL", "")


@pytest.mark.skipif(not DB_URL, reason="EFM3_TEST_DB_URL not set — skipping DB-backed tests")
class TestFormalFinalSelectedCoverage:
    def test_formal_sim_good_date_has_24_selected(self):
        """formal_sim on a data-rich date should PASS (final_selected=24)."""
        from pipelines.full_chain_orchestrator import run_full_chain

        result = run_full_chain(
            target_date="2026-01-25",  # known good date
            mode="formal_sim",
            use_db=True,
            db_url=DB_URL,
            export_submission=False,
            config={},
        )
        assert result["status"] in ("COMPLETE", "PARTIAL"), (
            f"Expected COMPLETE for good date, got {result['status']}: {json.dumps(result, indent=2, default=str)}"
        )
        # Check that efm_predictions has final_selected=24 for this run
        import pymysql
        from urllib.parse import unquote

        u = DB_URL.split("//", 1)[1]
        up, hp = u.split("@")
        user, pw = up.split(":")
        hp, dbn = hp.split("/")
        host, port = hp.split(":")
        pw = unquote(pw)
        conn = pymysql.connect(host=host, port=int(port), user=user, password=pw, database=dbn)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM efm_predictions WHERE run_id=%s "
            "AND task='final' AND stage='final_selected' AND is_selected=1 AND is_shadow=0",
            (result["run_id"],),
        )
        cnt = cur.fetchone()[0]
        conn.close()
        assert cnt == 24, f"final_selected rows = {cnt} (expected 24)"

    def test_formal_sim_bad_date_enforces_guard(self):
        """formal_sim on a no-data date enforces guard and writes formal checks."""
        from pipelines.full_chain_orchestrator import run_full_chain
        import pymysql
        from urllib.parse import unquote

        u = DB_URL.split("//", 1)[1]
        up, hp = u.split("@")
        user, pw = up.split(":")
        hp, dbn = hp.split("/")
        host, port = hp.split(":")
        pw = unquote(pw)
        conn = pymysql.connect(host=host, port=int(port), user=user, password=pw, database=dbn)
        cur = conn.cursor()

        result = run_full_chain(
            target_date="2026-01-01",
            mode="formal_sim",
            use_db=True,
            db_url=DB_URL,
            export_submission=False,
            config={},
        )
        assert result["status"] == "FAIL", f"Expected FAIL, got {result['status']}"

        # Verify formal guard check was written to postflight
        cur.execute(
            "SELECT check_name, passed FROM efm_postflight_checks WHERE run_id=%s "
            "AND check_name='formal_final_selected_coverage'",
            (result["run_id"],),
        )
        row = cur.fetchone()
        assert row is not None, "formal_final_selected_coverage check not written"
        assert row[1] == 0, "formal_final_selected_coverage should be FAIL(0)"

        # Verify formal guard event was written
        cur.execute(
            "SELECT event_name FROM efm_run_events WHERE run_id=%s AND event_type='formal_guard'",
            (result["run_id"],),
        )
        events = cur.fetchall()
        assert len(events) > 0, "formal_guard events should exist"
        conn.close()
