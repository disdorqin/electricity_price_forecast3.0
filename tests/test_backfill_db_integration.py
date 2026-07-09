"""DB integration test for the Shandong PMOS backfill.

Env-gated on EFM3_TEST_DB_URL. Creates a throwaway *test* database (only when
the database name contains "test", as a safety guard against dropping
production), runs the backfill against a small synthetic window, and asserts
row counts plus idempotent re-run behavior.

Usage:
    EFM3_TEST_DB_URL="mysql+pymysql://root:PASS%23@127.0.0.1:3306/efm3_backfill_test" \
        python -m pytest tests/test_backfill_db_integration.py -q
"""
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def test_db_url():
    url = os.environ.get("EFM3_TEST_DB_URL")
    if not url:
        pytest.skip("EFM3_TEST_DB_URL not set — skipping DB integration test")

    # Safety guard: never drop a database whose name lacks "test".
    dbname = urlparse(url).path.lstrip("/")
    if "test" not in dbname.lower():
        pytest.skip(f"Refusing to provision non-test database '{dbname}'")

    import pymysql
    from common.db.connection import DbConnectionManager
    from common.db.schema import init_schema

    mgr = DbConnectionManager(db_url=url)
    params = mgr._parse_url()
    srv = pymysql.connect(
        host=params["host"], port=params["port"], user=params["user"],
        password=params["password"], connect_timeout=10,
    )
    srv.cursor().execute(f"DROP DATABASE IF EXISTS `{dbname}`")
    srv.cursor().execute(f"CREATE DATABASE `{dbname}` CHARACTER SET utf8mb4")
    srv.close()

    conn = mgr.get_connection()
    init_schema(conn)
    conn.close()

    yield url

    srv = pymysql.connect(
        host=params["host"], port=params["port"], user=params["user"],
        password=params["password"], connect_timeout=10,
    )
    srv.cursor().execute(f"DROP DATABASE IF EXISTS `{dbname}`")
    srv.close()


def _write_synth_csv(path: Path, dates: list[str]) -> None:
    rows = []
    for d in dates:
        for h in range(1, 25):
            ts = f"{d} 00:00" if h == 24 else f"{d} {h:02d}:00"
            rows.append((ts, float(h) + 100.0, float(h) + 200.0))
    df = pd.DataFrame(rows, columns=["时刻", "日前电价", "实时电价"])
    df.to_csv(path, index=False, encoding="gbk")


def _run_backfill(csv_path: Path, db_url: str) -> int:
    from tools.db_ops import backfill_shandong_pmos_csv as mod
    argv = [
        "backfill",
        "--csv-path", str(csv_path),
        "--start-date", "2026-03-01",
        "--end-date", "2026-03-03",
        "--db-url", db_url,
        "--encoding", "gbk",
        "--commit",
    ]
    old = sys.argv
    sys.argv = argv
    try:
        return mod.main()
    finally:
        sys.argv = old


def _counts(db_url: str) -> dict:
    import pymysql
    u = urlparse(db_url)
    ui, hp = u.netloc.rsplit("@", 1)
    user, pw = ui.split(":", 1)
    pw = unquote(pw)
    host, port = hp.rsplit(":", 1)
    conn = pymysql.connect(host=host, port=int(port), user=user, password=pw,
                           database=u.path.lstrip("/"))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM efm_market_data_hourly WHERE data_type='da_price'")
    da = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM efm_market_data_hourly WHERE data_type='rt_price'")
    rt = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM efm_actual_prices")
    act = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM efm_predictions WHERE stage='da_anchor'")
    da_led = cur.fetchone()[0]
    conn.close()
    return {"da": da, "rt": rt, "act": act, "da_led": da_led}


def test_backfill_writes_expected_counts(test_db_url, tmp_path):
    csv_path = tmp_path / "synth.csv"
    _write_synth_csv(csv_path, ["2026-03-01", "2026-03-02", "2026-03-03"])

    rc = _run_backfill(csv_path, test_db_url)
    assert rc == 0

    c = _counts(test_db_url)
    assert c["da"] == 3 * 24
    assert c["rt"] == 3 * 24
    assert c["act"] == 3 * 24
    assert c["da_led"] == 3 * 24


def test_backfill_is_idempotent(test_db_url, tmp_path):
    csv_path = tmp_path / "synth.csv"
    _write_synth_csv(csv_path, ["2026-03-01", "2026-03-02", "2026-03-03"])

    _run_backfill(csv_path, test_db_url)  # first commit
    _run_backfill(csv_path, test_db_url)  # second commit (upsert)
    c = _counts(test_db_url)
    # counts must be unchanged after re-run (ON DUPLICATE KEY UPDATE)
    assert c["da"] == 3 * 24
    assert c["rt"] == 3 * 24
    assert c["act"] == 3 * 24
    assert c["da_led"] == 3 * 24
