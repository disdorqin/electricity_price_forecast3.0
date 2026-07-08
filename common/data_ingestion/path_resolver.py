"""
Path resolution for EFM3 data sources.

Reads configs/data_sources.yaml and resolves root paths using
environment variables with fallback to configured defaults.
Supports Windows paths (backslashes) via pathlib.Path.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

# Relative path from common/data_ingestion/ to configs/
_CONFIG_RELATIVE = Path(__file__).resolve().parent.parent.parent / "configs" / "data_sources.yaml"


class PathResolver:
    """
    Resolves data source directories and file patterns from configuration.

    Environment variables take precedence over default_root in the YAML config.
    """

    def __init__(self, config_path: Optional[str | Path] = None):
        self._config_path = Path(config_path) if config_path else _CONFIG_RELATIVE
        self._config: dict[str, Any] = self._load_config()
        self._sources: dict[str, dict[str, Any]] = self._config.get("data_sources", {})

    # ── Public API ─────────────────────────────────────────────────

    def get_source_paths(self, source_key: str) -> list[Path]:
        """
        Resolve one or more root paths for a data source.

        Returns a list so that a single source_key may expand into
        multiple concrete directories.  Currently returns a single
        resolved root, but the interface is list-shaped for future
        multi-directory sources.
        """
        source = self._get_source(source_key)
        root = self._resolve_root(source)
        if not root:
            logger.warning("No root path resolved for source '%s'", source_key)
            return []
        root_path = Path(root)
        if not root_path.exists():
            logger.warning("Root path does not exist for source '%s': %s", source_key, root_path)
        return [root_path]

    def get_include_patterns(self, source_key: str) -> list[str]:
        """Return glob include patterns for a source."""
        source = self._get_source(source_key)
        return source.get("include_patterns", [])

    def get_exclude_patterns(self, source_key: str) -> list[str]:
        """Return glob exclude patterns for a source."""
        source = self._get_source(source_key)
        return source.get("exclude_patterns", [])

    def get_source_market(self, source_key: str) -> str:
        """Return the market label for a source."""
        source = self._get_source(source_key)
        return source.get("market", "shandong")

    def is_source_enabled(self, source_key: str) -> bool:
        """Check whether a source is enabled in config."""
        source = self._get_source(source_key)
        return bool(source.get("enabled", True))

    def list_sources(self) -> list[str]:
        """Return all configured source keys."""
        return list(self._sources.keys())

    def list_enabled_sources(self) -> list[str]:
        """Return only enabled source keys."""
        return [k for k, v in self._sources.items() if v.get("enabled", True)]

    # ── Internals ──────────────────────────────────────────────────

    def _load_config(self) -> dict[str, Any]:
        path = self._config_path
        if not path.exists():
            logger.warning("Config file not found at %s — using empty config", path)
            return {"data_sources": {}}
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def _get_source(self, source_key: str) -> dict[str, Any]:
        source = self._sources.get(source_key)
        if source is None:
            logger.warning("Unknown data source key: '%s'", source_key)
            return {}
        return source

    def _resolve_root(self, source: dict[str, Any]) -> Optional[str]:
        """
        Resolve root path: check env var first, fall back to default_root.

        Environment variable name is taken from the ``root_env`` field
        in the source definition.  If set, its value is used verbatim.
        Otherwise ``default_root`` is returned.
        """
        env_var = source.get("root_env", "")
        if env_var:
            env_val = os.environ.get(env_var)
            if env_val:
                logger.debug("Resolved %s from env %s = %s", source.get("root_env"), env_var, env_val)
                return env_val

        default = source.get("default_root")
        if default:
            logger.debug("Falling back to default_root for source: %s", default)
        return default
