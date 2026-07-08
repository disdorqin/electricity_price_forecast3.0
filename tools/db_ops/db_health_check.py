"""EFM3 DB Health Check — connection, tables, recent runs."""

import os
import sys
import pymysql
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

DB_URL = os.environ.get("EFM3_DB_URL", "")


def _redact(url: str) -> str:
    if "@" not in url:
        return url
    prefix = url.split("://")[0] if "://" in url else ""
    rest = url.split("://")[1] if "://" in url else url
    user_pass, host = rest.split("@", 1)
    if ":" in user_pass:
        user = user_pass.split(":")[0]
        return f"{prefix}://{user}:****@{host}" if prefix else f"{user}:****@{host}"
    return url


def health_check(db_url: str = ""):
    url = db_url or DB_URL
    if not url:
        print("ERROR: No DB URL. Set EFM3_DB_URL or pass --db-url")
        return {"status": "FAIL", "error": "no_db_url"}

    print(f"DB URL: {_redact(url)}")
    raw = url.replace("mysql+pymysql://", "").replace("mysql://", "")
    user_pass, rest = raw.split("@", 1) if "@" in raw else ("root", raw)
    user, password = user_pass.split(":", 1) if ":" in user_pass else (user_pass, "")
    from urllib.parse import unquote
    password = unquote(password)
    host_port, database = rest.split("/", 1) if "/" in rest else (rest, "efm3")
    host, port_str = host_port.split(":", 1) if ":" in host_port else (host_port, "3306")

    try:
        conn = pymysql.connect(host=host, port=int(port_str), user=user, password=password, database=database, connect_timeout=5)

        # Check tables
        with conn.cursor() as c:
            c.execute("SHOW TABLES")
            tables = [r[0] for r in c.fetchall()]
            print(f"\nTables ({len(tables)}): {', '.join(tables)}")

        # Check recent runs
        with conn.cursor() as c:
            c.execute("SELECT run_id, target_date, status, delivery_status, exit_code, started_at FROM efm_runs ORDER BY created_at DESC LIMIT 5")
            runs = c.fetchall()
            print(f"\nRecent runs ({len(runs)}):")
            for r in runs:
                print(f"  {r[0][:30]:30s} {r[1]} {r[2]:10s} {r[3]:20s} exit={r[4]}")

        # Check failed runs
        with conn.cursor() as c:
            c.execute("SELECT COUNT(*) FROM efm_runs WHERE status='FAIL'")
            failed = c.fetchone()[0]
            print(f"\nFailed runs: {failed}")

        # Check dataset versions
        with conn.cursor() as c:
            c.execute("SELECT status, COUNT(*) FROM efm_dataset_versions GROUP BY status")
            ds_status = c.fetchall()
            print(f"\nDataset versions:")
            for s, cnt in ds_status:
                print(f"  {s}: {cnt}")

        conn.close()
        return {"status": "OK", "tables": len(tables), "runs": len(runs), "failed": failed}

    except Exception as e:
        print(f"\nERROR: {e}")
        return {"status": "FAIL", "error": str(e)}


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    result = health_check(url)
    print(f"\nHealth: {result['status']}")
