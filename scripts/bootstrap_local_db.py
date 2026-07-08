"""Bootstrap the local EFM3 MySQL ledger.

This is a thin, safe wrapper around the existing CLI:

  1. (optional) bring up the docker-compose MySQL service
  2. wait for the server to accept TCP connections
  3. run ``python main.py --init-db --db-url $EFM3_DB_URL`` to apply migrations

It never invents new commands and never prints the raw DB password.

Usage:
    MYSQL_ROOT_PASSWORD='SuperSecret123#' docker compose -f docker-compose.mysql.yml up -d
    python scripts/bootstrap_local_db.py                # uses $EFM3_DB_URL
    python scripts/bootstrap_local_db.py --docker       # also starts compose
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _redact(url: str) -> str:
    # Mask the password portion for any logging.
    try:
        from backend.app.utils.redaction import redact_db_url

        return redact_db_url(url)
    except Exception:
        if "@" in url:
            return url.split("@", 1)[0].split(":", 1)[0] + ":****@" + url.split("@", 1)[1]
        return url


def _wait_for_mysql(host: str, port: int, timeout: int = 60) -> bool:
    import socket

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=3):
                return True
        except OSError:
            time.sleep(2)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Bootstrap local EFM3 MySQL ledger.")
    ap.add_argument("--docker", action="store_true", help="Also start docker-compose MySQL.")
    ap.add_argument("--db-url", default=os.environ.get("EFM3_DB_URL"), help="MySQL URL (default $EFM3_DB_URL).")
    ap.add_argument("--no-init", action="store_true", help="Only start MySQL; skip --init-db.")
    args = ap.parse_args()

    if not args.db_url:
        print("ERROR: EFM3_DB_URL is not set. Copy .env.local.example to .env.local and fill it in.", file=sys.stderr)
        return 2

    if args.docker:
        if shutil.which("docker") is None:
            print("ERROR: docker not found on PATH; start MySQL manually or install docker.", file=sys.stderr)
            return 2
        print("Starting docker-compose MySQL (efm3-mysql)...")
        compose = REPO_ROOT / "docker-compose.mysql.yml"
        subprocess.run(["docker", "compose", "-f", str(compose), "up", "-d"], check=True)
        # Extract host/port from URL (best-effort; default localhost:3306).
        host, port = "127.0.0.1", 3306
        if "@" in args.db_url:
            netloc = args.db_url.split("@", 1)[1].split("/")[0]
            if ":" in netloc:
                host, port = netloc.split(":", 1)
                port = int(port)
        print(f"Waiting for MySQL at {host}:{port} ...")
        if not _wait_for_mysql(host, port):
            print("ERROR: MySQL did not become reachable in time.", file=sys.stderr)
            return 2

    if args.no_init:
        print("Skipping --init-db as requested.")
        return 0

    print(f"Applying EFM3 ledger schema to {_redact(args.db_url)} ...")
    rc = subprocess.run(
        [sys.executable, "main.py", "--init-db", "--db-url", args.db_url],
        cwd=REPO_ROOT,
    ).returncode
    if rc != 0:
        print("ERROR: main.py --init-db failed (see above).", file=sys.stderr)
        return rc
    print("Local EFM3 ledger is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
