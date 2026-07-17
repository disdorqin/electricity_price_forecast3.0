"""
Archive pre-V3.1 DB runs (TASK-11).

UPDATE efm_runs SET status = 'ARCHIVED'
WHERE started_at < '2026-07-16' AND chain_version < '3.1';

Run with: python scripts/archive_pre_v31_runs.py
"""
from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from sqlalchemy import create_engine, text

DB_URL = os.environ.get(
    "EFM3_DB_URL",
    "mysql+pymysql://root:Zlt20060313%23@127.0.0.1:3306/efm3",
)


def main():
    engine = create_engine(DB_URL, pool_pre_ping=True)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "UPDATE efm_runs SET status = 'ARCHIVED' "
                "WHERE started_at < '2026-07-16' AND chain_version < '3.1'"
            )
        )
        affected = result.rowcount
        conn.commit()
    print(f"[OK] Archived {affected} pre-V3.1 runs")
    engine.dispose()


if __name__ == "__main__":
    main()
