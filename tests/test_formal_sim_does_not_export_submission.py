"""formal_sim mode must NOT write a formal submission_ready.csv."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

DB_URL = os.environ.get("EFM3_TEST_DB_URL") or os.environ.get("EFM3_DB_URL", "")


@pytest.mark.skipif(not DB_URL, reason="EFM3_TEST_DB_URL not set — skipping DB-backed tests")
class TestFormalSimNoSubmission:
    def test_formal_sim_no_submission_csv(self):
        """formal_sim should NOT write delivery_outputs or submission_ready."""
        from pipelines.full_chain_orchestrator import run_full_chain

        result = run_full_chain(
            target_date="2026-01-25",
            mode="formal_sim",
            use_db=True,
            db_url=DB_URL,
            export_submission=False,
            config={},
        )

        # Check DB for delivery_outputs
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
            "SELECT COUNT(*) FROM efm_delivery_outputs WHERE run_id=%s",
            (result["run_id"],),
        )
        cnt = cur.fetchone()[0]
        conn.close()

        assert cnt == 0, (
            f"formal_sim should NOT write delivery_outputs, found {cnt} "
            f"(run_id={result['run_id']})"
        )

    def test_formal_sim_writes_ledger_tables(self):
        """formal_sim must still write efm_runs, efm_predictions, efm_postflight_checks."""
        from pipelines.full_chain_orchestrator import run_full_chain

        result = run_full_chain(
            target_date="2026-01-25",
            mode="formal_sim",
            use_db=True,
            db_url=DB_URL,
            export_submission=False,
            config={},
        )
        run_id = result["run_id"]

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

        cur.execute("SELECT COUNT(*) FROM efm_runs WHERE run_id=%s", (run_id,))
        assert cur.fetchone()[0] == 1, "efm_runs row missing"

        cur.execute("SELECT COUNT(*) FROM efm_predictions WHERE run_id=%s", (run_id,))
        assert cur.fetchone()[0] > 0, "efm_predictions rows missing"

        cur.execute("SELECT COUNT(*) FROM efm_postflight_checks WHERE run_id=%s", (run_id,))
        assert cur.fetchone()[0] > 0, "efm_postflight_checks rows missing"

        # Should have formal guard rows in postflight
        cur.execute(
            "SELECT COUNT(*) FROM efm_postflight_checks WHERE run_id=%s "
            "AND check_name LIKE 'formal_%%'",
            (run_id,),
        )
        formal_checks = cur.fetchone()[0]
        assert formal_checks > 0, "formal guard postflight checks missing in formal_sim"

        conn.close()
