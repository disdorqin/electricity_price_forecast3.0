"""
Dimension (lookup-table) resolver for the 3NF EFM3 ledger.

The 3NF schema stores free-text domains (stage, model, policy, check, ...) as
foreign keys to ``efm_dim_*`` tables. This module resolves a human-readable
*name* to its surrogate *id* at the data-access boundary, auto-creating the
dimension row on first use. Reads can map an id back to its name.

All public functions take a pymysql ``Connection`` as first argument.
"""
from __future__ import annotations

from typing import Optional

# kind -> dimension table name
_DIM_TABLE: dict[str, str] = {
    "stage": "efm_dim_stage",
    "model": "efm_dim_model",
    "policy": "efm_dim_policy",
    "check": "efm_dim_check",
    "output": "efm_dim_output",
    "artifact": "efm_dim_artifact",
    "event": "efm_dim_event",
    "datatype": "efm_dim_datatype",
    "relation": "efm_dim_relation",
    "rule": "efm_dim_rule",
    "repairstage": "efm_dim_repairstage",
    "market": "efm_dim_market",
    "unit": "efm_dim_unit",
    "sourcetype": "efm_dim_sourcetype",
    "importstatus": "efm_dim_importstatus",
    "step": "efm_dim_step",
}

# Process-wide caches. Dimension name->id is stable for the life of the DB
# (we never delete dimension rows), so caching is safe and speeds up hot paths.
_id_cache: dict[tuple[str, str], int] = {}
_name_cache: dict[tuple[str, int], str] = {}


def _table(kind: str) -> str:
    t = _DIM_TABLE.get(kind)
    if t is None:
        raise ValueError(f"unknown dimension kind: {kind!r}")
    return t


def clear_dim_cache() -> None:
    """Clear caches (e.g. after a schema rebuild)."""
    _id_cache.clear()
    _name_cache.clear()


def resolve_dim_id(conn, kind: str, name: Optional[str], description: Optional[str] = None) -> Optional[int]:
    """Return the surrogate id for ``name`` in dimension ``kind``.

    Auto-creates the row if it does not exist. Returns ``None`` when
    ``name`` is ``None`` (nullable FK columns).
    """
    if name is None:
        return None
    key = (kind, str(name))
    cached = _id_cache.get(key)
    if cached is not None:
        return cached
    table = _table(kind)
    with conn.cursor() as cur:
        cur.execute(f"SELECT id FROM {table} WHERE name=%s", (name,))
        row = cur.fetchone()
        if row is not None:
            did = int(row[0])
        else:
            cur.execute(
                f"INSERT INTO {table} (name, description) VALUES (%s, %s) "
                f"ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)",
                (name, description),
            )
            did = int(cur.lastrowid)
            conn.commit()
    _id_cache[key] = did
    _name_cache[(kind, did)] = str(name)
    return did


def dim_name(conn, kind: str, dim_id: Optional[int]) -> Optional[str]:
    """Return the name for a dimension id (inverse of :func:`resolve_dim_id`)."""
    if dim_id is None:
        return None
    key = (kind, int(dim_id))
    cached = _name_cache.get(key)
    if cached is not None:
        return cached
    table = _table(kind)
    with conn.cursor() as cur:
        cur.execute(f"SELECT name FROM {table} WHERE id=%s", (dim_id,))
        row = cur.fetchone()
    name = row[0] if row else None
    if name is not None:
        _name_cache[key] = name
        _id_cache[(kind, name)] = int(dim_id)
    return name


def resolve_dim_ids(conn, kind: str, names: list[Optional[str]]) -> list[Optional[int]]:
    """Resolve a list of names at once (preserves order, keeps None)."""
    return [resolve_dim_id(conn, kind, n) for n in names]
