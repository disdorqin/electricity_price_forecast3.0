"""Password redaction tests — ensures the DB password never leaks.

These run with NO real credentials; they only assert the redaction logic.
"""

from backend.app.utils.redaction import (
    contains_password_leak,
    redact_connection_params,
    redact_db_url,
)

RAW = "SuperSecret123#"  # fake fixture password — never a real credential


def test_redact_db_url_masks_password():
    url = f"mysql+pymysql://root:{RAW}@127.0.0.1:3306/efm3"
    out = redact_db_url(url)
    assert RAW not in out
    assert "****" in out
    assert "root" in out  # user preserved
    assert "127.0.0.1:3306/efm3" in out  # host/db preserved


def test_redact_db_url_handles_encoded_hash():
    url = "mysql+pymysql://root:SuperSecret123%23@host/db"
    out = redact_db_url(url)
    assert "SuperSecret123" not in out
    assert "%23" not in out
    assert "****" in out


def test_redact_db_url_empty():
    assert redact_db_url("") == ""


def test_redact_connection_params_masks_password():
    params = {"host": "h", "user": "u", "password": RAW}
    out = redact_connection_params(params)
    assert out["password"] == "****"
    assert out["host"] == "h"


def test_contains_password_leak():
    assert contains_password_leak(f"error: {RAW}", RAW) is True
    assert contains_password_leak("all good", RAW) is False
    # encoded form (%23) does NOT leak the raw password -> correctly not flagged
    assert contains_password_leak("pw=SuperSecret123%23", RAW) is False
    # a literal decoded '#' in text IS a leak
    assert contains_password_leak("pw=SuperSecret123#", RAW) is True


def test_backend_logs_redact_url(monkeypatch):
    # The startup log path must not print the raw password.
    import logging
    from backend.app import main  # noqa: F401  (import triggers no logging of raw pwd)
    from backend.app.config import settings

    captured = {}

    class _Handler(logging.Handler):
        def emit(self, record):
            captured.setdefault("msgs", []).append(record.getMessage())

    h = _Handler()
    logging.getLogger("efm3.backend").addHandler(h)
    settings.db_url = f"mysql+pymysql://root:{RAW}@127.0.0.1:3306/efm3"
    main._startup()
    msgs = captured.get("msgs", [])
    assert msgs, "startup did not log"
    assert all(RAW not in m for m in msgs), "raw password leaked into logs"
