"""
EFM3 Backend — settings.

All secrets come from environment variables (never hardcoded):
  EFM3_DB_URL   MySQL connection string (mysql+pymysql://user:pass@host:3306/efm3)
  EFM3_API_KEY  Optional API key required for non-localhost access to ops endpoints

The backend is local-first: by default ops endpoints are DISABLED unless
explicitly enabled, and formal/export operations always require confirm=true.
"""

from __future__ import annotations

import os
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EFM3_",
        env_file=".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # MySQL ledger URL — read from EFM3_DB_URL
    db_url: str = ""

    # Optional API key for non-localhost access
    api_key: str = ""

    # CORS origins (comma-separated). Default localhost only.
    cors_origins: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000"

    # Ops endpoints are OFF unless explicitly enabled.
    ops_enabled: bool = False

    # Comma-separated hosts considered "local" (bypass API key when ops disabled).
    ops_allow_from: str = "127.0.0.1,::1,testclient,localhost"

    # Hard subprocess timeout for any triggered pipeline (seconds).
    ops_timeout: int = 600

    # Application environment label.
    app_env: str = "local"

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def ops_allow_from_list(self) -> List[str]:
        return [h.strip() for h in self.ops_allow_from.split(",") if h.strip()]

    @property
    def db_configured(self) -> bool:
        return bool(self.db_url)


# Single shared settings instance.
settings = Settings()


def _repo_root() -> str:
    """Return the EFM3 repository root (parent of backend/)."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


REPO_ROOT = _repo_root()
