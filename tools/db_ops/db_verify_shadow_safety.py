"""EFM3 DB Verify Shadow Safety — check no shadow contamination."""

import os
import sys
import json
import pymysql
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

DB_URL = os.environ.get("EFM3_DB_URL", "")


def _connect(url):
    raw = url.replace("mysql+pymysql://", "").replace("mysql://", "")
    user_pass, rest = raw.split("@", 1) if "@" in raw else ("root", raw)
    user, password = user_pass.split(":", 1) if ":" in user_pass else (user_pass, "")
    from urllib.parse import unquote
    password = unquote(password)
    host_port, database = rest.split("/", 1) if "/" in rest else (rest, "efm3")
    host, port_str = host_port.split(":", 1) if ":" in host_port else (host_port, "3306")
    return pymysql.connect(host=host, port=int(port_str), user=user, password=password, database=database, connect_timeout=5)


def verify_shadow_safety(db_url="", detail=False):
    url = db_url or DB_URL
    if not url:
        print("ERROR: No DB URL")
        return {"status": "FAIL", "error": "no_db_url"}

    conn = _connect(url)
    results = {"checks": {}}

    # Check 1: Any is_shadow=true AND is_selected=true?
    with conn.cursor() as c:
        c.execute("SELECT COUNT(*) FROM efm_predictions WHERE is_shadow=TRUE AND is_selected=TRUE")
        shadow_selected = c.fetchone()[0]
        results["checks"]["shadow_selected"] = shadow_selected == 0
        results["shadow_selected_count"] = shadow_selected

    # Check 2: Any postflight check named shadow_not_final that FAILED?
    with conn.cursor() as c:
        c.execute("SELECT COUNT(*) FROM efm_postflight_checks WHERE check_name='shadow_not_final' AND passed=FALSE")
        shadow_failed = c.fetchone()[0]
        results["checks"]["shadow_failed_postflight"] = shadow_failed == 0
        results["shadow_failed_postflight_count"] = shadow_failed

    # Check 3: Recent runs with status
    with conn.cursor() as c:
        c.execute("SELECT run_id, target_date, status, delivery_status FROM efm_runs ORDER BY created_at DESC LIMIT 10")
        results["recent_runs"] = [{"id": r[0], "date": str(r[1]), "status": r[2], "delivery": r[3]} for r in c.fetchall()]

    conn.close()

    all_pass = all(results["checks"].values())
    results["status"] = "PASS" if all_pass else "FAIL"

    print(f"\nShadow Safety: {results['status']}")
    for check, passed in results["checks"].items():
        print(f"  {'✓' if passed else '✗'} {check}")
    if detail:
        print(f"\nShadow selected: {results.get('shadow_selected_count', 0)}")
        print(f"Postflight shadow failures: {results.get('shadow_failed_postflight_count', 0)}")
        print(f"\nRecent runs:")
        for r in results.get("recent_runs", []):
            print(f"  {r['id'][:30]} {r['date']} {r['status']:10s} {r['delivery']:20s}")

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url", default="")
    parser.add_argument("--detail", action="store_true")
    args = parser.parse_args()
    verify_shadow_safety(args.db_url, args.detail)
