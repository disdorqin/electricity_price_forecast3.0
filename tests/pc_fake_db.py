"""
pc_fake_db.py — A lightweight, dependency-free fake MySQL ledger for unit
testing the production_circuit package without a live database.

It implements just enough of the pymysql connection/cursor protocol
(get_connection / cursor / execute / fetchall / fetchone / commit / close /
lastrowid, plus context-manager cursor) and a tiny SQL subset parser to support
the exact statements emitted by ``pipelines.production_circuit.step_recorder``
and the chain modules:

  * INSERT ... VALUES (...) [ON DUPLICATE KEY UPDATE ...]
  * SELECT <cols> FROM <table> [WHERE <cond> AND <cond> ...] [ORDER BY <col>]
    where each <cond> is one of:
        col = %s
        col = 'literal'
        col IS NOT NULL
        col IN ('a','b',...)

The fake stores rows as dicts keyed by table name, auto-assigns ``id`` for the
known auto-increment tables, and returns the requested columns as tuples.
"""

from __future__ import annotations

import re
from typing import Any, Optional

AUTO_ID_TABLES = {
    "efm_pipeline_steps",
    "efm_predictions",
    "efm_prediction_lineage_edges",
    "efm_repair_decisions",
    "efm_fusion_candidates",
    "efm_task_finals",
    "efm_delivery_finals",
}


class FakeCursor:
    def __init__(self, conn: "FakeConn"):
        self.conn = conn
        self._result: list[tuple] = []
        self.lastrowid: Optional[int] = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    # ── protocol ──────────────────────────────────────────────────────
    def execute(self, sql: str, params: Optional[Any] = None) -> None:
        self.lastrowid = None
        self._result = []
        flat = " ".join(str(sql).split())  # collapse whitespace/newlines
        params = list(params) if params is not None else []
        up = flat.upper()
        if up.startswith("SELECT"):
            self._result = self._select(flat, params)
        elif up.startswith("INSERT"):
            self._insert(flat, params)
        # other statements (UPDATE/DELETE) are no-ops for our tests
        return None

    def fetchall(self) -> list[tuple]:
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None

    # ── INSERT ─────────────────────────────────────────────────────────
    def _insert(self, flat: str, params: list) -> None:
        base = flat.split(" ON DUPLICATE KEY UPDATE ")[0]
        m = re.match(
            r"INSERT INTO (\w+)\s*\((.*?)\)\s*VALUES\s*\((.*)\)\s*$",
            base, re.IGNORECASE | re.DOTALL,
        )
        if not m:
            return
        table = m.group(1)
        cols = [c.strip() for c in m.group(2).split(",")]
        vals = [v.strip() for v in m.group(3).split(",")]
        row: dict[str, Any] = {}
        piter = iter(params)
        for col, raw in zip(cols, vals):
            if raw == "%s":
                row[col] = next(piter, None)
            else:
                # literal constant (not used by the recorder, but be safe)
                row[col] = raw.strip().strip("'")
        if table in AUTO_ID_TABLES:
            self.conn._counter[table] = self.conn._counter.get(table, 0) + 1
            rid = self.conn._counter[table]
            row["id"] = rid
            self.lastrowid = rid
        self.conn.tables.setdefault(table, []).append(row)

    # ── SELECT ─────────────────────────────────────────────────────────
    def _select(self, flat: str, params: list) -> list[tuple]:
        if "LAST_INSERT_ID()" in flat.upper():
            return [(self.lastrowid or 0,)]
        # Split off trailing ORDER BY / LIMIT clauses FIRST so the WHERE
        # clause can never swallow them (the optional ORDER BY in the old
        # single-regex let the lazy WHERE group consume it via the end-anchor).
        rest = flat
        order = None
        limit = None
        m_lim = re.search(r"\s+LIMIT\s+(\d+)\s*$", rest, re.IGNORECASE)
        if m_lim:
            limit = int(m_lim.group(1))
            rest = rest[: m_lim.start()]
        m_ord = re.search(r"\s+ORDER BY\s+([\w,\s]+)\s*$", rest, re.IGNORECASE)
        if m_ord:
            order = m_ord.group(1).strip()
            rest = rest[: m_ord.start()]
        m = re.match(
            r"SELECT (.+?) FROM (\w+)(?:\s+WHERE\s+(.+))?$",
            rest, re.IGNORECASE | re.DOTALL,
        )
        if not m:
            return []
        cols = [c.strip() for c in m.group(1).split(",")]
        table = m.group(2)
        where = m.group(3)
        rows = list(self.conn.tables.get(table, []))
        if where:
            rows = [r for r in rows if self._where(r, where, params)]
        if order:
            order_cols = [c.strip() for c in order.split(",")]
            rows = sorted(rows, key=lambda r: tuple(r.get(c, 0) or 0 for c in order_cols))
        if limit is not None:
            rows = rows[:limit]
        # Aggregation: COUNT(*) -> a single row with the row count.
        if len(cols) == 1 and re.match(r"COUNT\(\*\)", cols[0], re.IGNORECASE):
            return [(len(rows),)]
        return [tuple(r.get(c) for c in cols) for r in rows]

    @staticmethod
    def _where(row: dict, where: str, params: list) -> bool:
        conds = re.split(r"\s+AND\s+", where, flags=re.IGNORECASE)
        piter = iter(params)
        for cond in conds:
            cond = cond.strip()
            up = cond.upper()
            if "IS NOT NULL" in up:
                col = cond.split()[0]
                if row.get(col) is None:
                    return False
                continue
            if "IN (" in up:
                cm = re.match(r"(\w+)\s+IN\s*\((.*)\)", cond, re.IGNORECASE)
                col = cm.group(1)
                raw_vals = [v.strip() for v in cm.group(2).split(",")]
                vals = []
                for v in raw_vals:
                    if v == "%s":
                        # bind the next positional param (mirrors real MySQL)
                        vals.append(str(next(piter, None)))
                    else:
                        vals.append(v.strip().strip("'"))
                if str(row.get(col)) not in vals:
                    return False
                continue
            cm = re.match(r"(\w+)\s*=\s*(.+)", cond)
            if not cm:
                continue
            col = cm.group(1)
            rhs = cm.group(2).strip()
            if rhs == "%s":
                val = next(piter, None)
                if row.get(col) != val:
                    return False
            else:
                lit = rhs.strip().strip("'")
                if str(row.get(col)) != lit:
                    return False
        return True


class FakeConn:
    def __init__(self, mgr: "FakeDbManager"):
        self.mgr = mgr
        self.tables = mgr.tables
        self._counter = mgr._counter

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


class FakeDbManager:
    """Drop-in for ``DbConnectionManager`` in tests."""

    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {}
        self._counter: dict[str, int] = {}

    def get_connection(self) -> FakeConn:
        return FakeConn(self)

    # Mirror DbConnectionManager.new_connection(): every caller gets its own
    # independent connection so the production_circuit package (which now uses
    # new_connection() to avoid the shared-singleton double-close hazard) is
    # exercised identically under the fake backend.
    def new_connection(self) -> FakeConn:
        return FakeConn(self)

    # ── helpers ────────────────────────────────────────────────────────
    def seed_actual_prices(self, target_date: str, pairs: list[tuple]) -> None:
        """pairs: list of (hour_business, da_anchor, rt_actual).

        Each row also stores ``target_date`` so the production queries that
        filter ``WHERE target_date=%s`` observe the seeded rows.
        """
        for hb, da, rt in pairs:
            self.tables.setdefault("efm_actual_prices", []).append(
                {"target_date": target_date, "hour_business": hb,
                 "da_anchor": da, "rt_actual": rt}
            )

    def count_rows(self, table: str) -> int:
        return len(self.tables.get(table, []))


def make_ctx(run_id: str = "efm3_pc_test", target_date: str = "2026-02-14") -> Any:
    """Build a CircuitContext backed by a FakeDbManager + StepRecorder."""
    from pipelines.production_circuit.circuit_orchestrator import CircuitContext
    from pipelines.production_circuit.step_recorder import StepRecorder

    mgr = FakeDbManager()
    return (
        mgr,
        CircuitContext(
            run_id=run_id,
            target_date=target_date,
            db_mgr=mgr,
            recorder=StepRecorder(mgr),
            store=None,
            config={},
            mode="dry_run",
        ),
    )
