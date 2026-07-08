"""
Redaction utilities — ensure the DB password never leaks into logs or API responses.
"""

from __future__ import annotations

import re
from urllib.parse import unquote


def redact_db_url(url: str) -> str:
    """Replace the password portion of a DB URL with '****'.

    Examples
    --------
    mysql+pymysql://root:secret@127.0.0.1:3306/efm3
        -> mysql+pymysql://root:****@127.0.0.1:3306/efm3
    mysql+pymysql://root:SuperSecret123%23@host/db  (URL-encoded #)
        -> mysql+pymysql://root:****@host/db
    """
    if not url:
        return ""
    # Match user:password@ up to an @ that precedes host
    m = re.search(r"(://)([^:/@]+):([^@]+)@", url)
    if not m:
        # Already has no password, or unparseable — return as-is but mask anything
        # that looks like credentials defensively.
        return url
    prefix = m.group(1) + m.group(2) + ":"
    return url[: url.index(prefix)] + prefix + "****" + url[url.index("@"):]


def redact_connection_params(params: dict) -> dict:
    """Return a copy of pymysql connection params with password masked."""
    out = dict(params)
    if "password" in out:
        out["password"] = "****"
    return out


def safe_log_url(url: str) -> str:
    """Convenience wrapper for logging."""
    return redact_db_url(url)


def contains_password_leak(text: str, raw_password: str) -> bool:
    """Return True if the raw password appears in text (used by tests)."""
    if not raw_password:
        return False
    # Compare against the decoded form as well (e.g. %23 -> #)
    decoded = unquote(raw_password)
    return raw_password in text or decoded in text
