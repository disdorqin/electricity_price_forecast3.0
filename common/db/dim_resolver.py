"""
dim_resolver.py — Lightweight dimension name → ID resolver.

Resolves string names (e.g. "realtime_fused", "sgdfnet") to FK IDs in
dimension tables (efm_dim_stage, efm_dim_model, etc.). Auto-inserts missing
entries so the production circuit never fails due to a missing dimension.

Usage:
    resolver = DimResolver(conn)
    stage_id = resolver.resolve("stage", "realtime_fused")      # → 9
    model_id = resolver.resolve("model", "a05_composite")       # → 17
    step_id  = resolver.resolve_optional("step", "unknown")     # → None
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DimResolver:
    """Resolve dimension name strings to integer FK IDs."""

    TABLE_MAP: dict[str, str] = {
        "stage": "efm_dim_stage",
        "model": "efm_dim_model",
        "step": "efm_dim_step",
        "policy": "efm_dim_policy",
        "rule": "efm_dim_rule",
        "relation": "efm_dim_relation",
        "repairstage": "efm_dim_repairstage",
    }

    def __init__(self, conn: Any):
        self.conn = conn
        # Cache: {(dim_key, name) → id}
        self._cache: dict[tuple[str, str], int] = {}
        self._preload_all()

    def _preload_all(self) -> None:
        """Pre-load ALL dimension tables into cache (O(total_rows) ≈ 100)."""
        for dim_key, table in self.TABLE_MAP.items():
            cur = self.conn.cursor()
            try:
                cur.execute(f"SELECT id, name FROM {table}")
                for row_id, name in cur.fetchall():
                    self._cache[(dim_key, name)] = int(row_id)
            except Exception as exc:
                logger.warning("[DimResolver] failed to pre-load %s: %s", table, exc)
            finally:
                cur.close()

    def resolve(self, dim: str, name: str) -> Optional[int]:
        """Resolve ``name`` to its dimension table ID.

        Auto-inserts missing entries. Returns ``None`` only if the insert fails.
        """
        key = (dim, name)
        if key in self._cache:
            return self._cache[key]

        table = self.TABLE_MAP.get(dim)
        if table is None:
            logger.warning("[DimResolver] unknown dimension '%s'", dim)
            return None

        try:
            cur = self.conn.cursor()
            cur.execute(
                f"INSERT INTO {table} (name, description) VALUES (%s, %s)",
                (name, name),
            )
            self.conn.commit()
            new_id = int(cur.lastrowid)
            cur.close()
            self._cache[key] = new_id
            logger.info("[DimResolver] auto-inserted %s: %s (id=%d)", dim, name, new_id)
            return new_id
        except Exception as exc:
            logger.warning("[DimResolver] failed to insert %s=%s: %s", dim, name, exc)
            return None

    def resolve_optional(self, dim: str, name: str) -> Optional[int]:
        """Resolve ``name`` to its dimension table ID.

        Returns ``None`` if not found — never auto-inserts.
        """
        return self._cache.get((dim, name))
