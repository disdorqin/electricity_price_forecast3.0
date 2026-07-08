"""EFM3 DB Run Summary — show run details by run_id or date."""

import os
import sys
import json
import pymysql
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

DB_URL = os.environ.get("EFM3_DB_URL", "")


def _redact(url):
    if "@" not in url:
        return url
    prefix = url.split("://")[0] if "://" in url else ""
    rest = url.split("://")[1] if "://" in url else url
    user_pass, host = rest.split("@", 1)
    if ":" in user_pass:
        user = user_pass.split(":")[0]
        return f"{prefix}://{user}:****@{host}" if prefix else f"{user}:****@{host}"
    return url


def _connect(url):
    raw = url.replace("mysql+pymysql://", "").replace("mysql://", "")
    user_pass, rest = raw.split("@", 1) if "@" in raw else ("root", raw)
    user, password = user_pass.split(":", 1) if ":" in user_pass else (user_pass, "")
    from urllib.parse import unquote
    password = unquote(password)
    host_port, database = rest.split("/", 1) if "/" in rest else (rest, "efm3")
    host, port_str = host_port.split(":", 1) if ":" in host_port else (host_port, "3306")
    return pymysql.connect(host=host, port=int(port_str), user=user, password=password, database=database, connect_timeout=5)


def run_summary(run_id=None, target_date=None, db_url="", output_format="text"):
    url = db_url or DB_URL
    if not url:
        print("ERROR: No DB URL")
        return

    conn = _connect(url)
    results = {}

    if run_id:
        with conn.cursor() as c:
            c.execute("SELECT * FROM efm_runs WHERE run_id=%s", (run_id,))
            row = c.fetchone()
            if row:
                cols = [desc[0] for desc in c.description]
                results["run"] = dict(zip(cols, row))

        with conn.cursor() as c:
            c.execute("SELECT COUNT(*) FROM efm_predictions WHERE run_id=%s", (run_id,))
            results["prediction_count"] = c.fetchone()[0]

        with conn.cursor() as c:
            c.execute("SELECT check_name, passed FROM efm_postflight_checks WHERE run_id=%s", (run_id,))
            results["postflight"] = [{"check": r[0], "passed": r[1]} for r in c.fetchall()]

        with conn.cursor() as c:
            c.execute("SELECT output_type, output_path, row_count FROM efm_delivery_outputs WHERE run_id=%s", (run_id,))
            results["delivery"] = [{"type": r[0], "path": r[1], "rows": r[2]} for r in c.fetchall()]

        with conn.cursor() as c:
            c.execute("SELECT event_type, event_name, created_at FROM efm_run_events WHERE run_id=%s ORDER BY created_at", (run_id,))
            results["events"] = [{"type": r[0], "name": r[1], "time": str(r[2])} for r in c.fetchall()]

    elif target_date:
        with conn.cursor() as c:
            c.execute("SELECT run_id, target_date, status, mode, delivery_status FROM efm_runs WHERE target_date=%s ORDER BY created_at DESC", (target_date,))
            results["runs"] = [{"id": r[0], "date": str(r[1]), "status": r[2], "mode": r[3], "delivery": r[4]} for r in c.fetchall()]

    conn.close()

    if output_format == "json":
        print(json.dumps(results, indent=2, default=str))
    else:
        print(f"\n{'='*60}")
        print(f"Run Summary: {run_id or target_date}")
        print(f"{'='*60}")
        if "run" in results:
            r = results["run"]
            print(f"Status: {r.get('status')} | Delivery: {r.get('delivery_status')} | Exit: {r.get('exit_code')}")
            print(f"Mode: {r.get('mode')} | Chain: {r.get('chain_version')}")
            print(f"Started: {r.get('started_at')} | Finished: {r.get('finished_at')}")
            print(f"Predictions: {results.get('prediction_count', 0)}")
            if results.get("postflight"):
                print(f"\nPostflight checks:")
                for p in results["postflight"]:
                    print(f"  {'✓' if p['passed'] else '✗'} {p['check']}")
            if results.get("delivery"):
                print(f"\nDeliveries:")
                for d in results["delivery"]:
                    print(f"  {d['type']}: {d['path']} ({d['rows']} rows)")
            if results.get("events"):
                print(f"\nEvents ({len(results['events'])}):")
                for e in results["events"]:
                    print(f"  [{e['type']}] {e['name']}")
        elif "runs" in results:
            for r in results["runs"]:
                print(f"  {r['id'][:30]:30s} {r['date']} {r['status']:10s} {r['mode']:10s} {r['delivery']:20s}")

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id")
    parser.add_argument("--target-date")
    parser.add_argument("--db-url", default="")
    parser.add_argument("--format", default="text", choices=["text", "json"])
    args = parser.parse_args()
    run_summary(args.run_id, args.target_date, args.db_url, args.format)
