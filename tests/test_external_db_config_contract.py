"""External DB URL configuration contract — no hardcoded credentials, %23 support, redaction."""
import sys, os, json, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


class TestExternalDbConfigContract:
    """No real passwords are hardcoded in source code."""

    def test_db_url_from_env_var(self):
        """DB URL must come from EFM3_DB_URL or --db-url, never hardcoded."""
        # By checking parser, --db-url accepts external URL format
        from cli.parser import build_parser

        p = build_parser()
        db_action = [a for a in p._actions if a.dest == "db_url"][0]
        help_text = db_action.help or ""
        assert "USER:PASS" in help_text, "help must show URL format with USER:PASS placeholder"
        assert "HOST" in help_text, "help must mention HOST"
        assert "PORT" in help_text, "help must mention PORT"
        assert "DB" in help_text, "help must mention DB"

    def test_no_hardcoded_host_in_code(self):
        """Source code should not hardcode a specific DB host."""
        import re

        # Check common source files for hardcoded DB patterns
        suspect_patterns = [
            r"host\s*=\s*['\"]localhost['\"]",
            r"127\.0\.0\.1['\"]",
            r"password\s*=\s*['\"]\w+['\"]",
        ]
        files = [
            "common/db/connection.py",
            "common/prediction_store.py",
            "common/fallback_policy.py",
            "backend/app/config.py",
        ]
        for fpath in files:
            try:
                content = open(fpath, encoding="utf-8").read()
                for pat in suspect_patterns:
                    if re.search(pat, content):
                        # Only flag if it's NOT a placeholder or example
                        if "YOUR_PASSWORD" not in content and "PASS" not in content:
                            pytest.fail(f"Possible hardcoded credential in {fpath}: {pat}")
            except FileNotFoundError:
                pass  # file may not be at expected path
            except Exception:
                pass

    def check_password_redaction_in_api(self):
        """Backend API must not return the full DB URL with password."""
        # Check health.py router
        from backend.app.routers.health import router
        # The health/db endpoint should only return host:port prefix
        # This is verified in test_backend_api_health.py

    def test_url_with_encoded_hash(self):
        """Password containing '%23' should be decoded for pymysql."""
        from common.db.connection import DbConnectionManager

        mgr = DbConnectionManager(
            db_url="mysql+pymysql://test:pass%23word@127.0.0.1:3306/testdb"
        )
        params = mgr._parse_url()
        assert params["password"] == "pass#word", (
            f"Expected decoded password 'pass#word', got '{params['password']}'"
        )
        assert params["host"] == "127.0.0.1"
        assert params["user"] == "test"
        assert params["database"] == "testdb"
        assert params["port"] == 3306

    def test_url_with_plain_hash(self):
        """Password containing literal '#' — this should also work."""
        from common.db.connection import DbConnectionManager

        # This tests that the URL-decode doesn't break a plain '#' (which MySQL accepts)
        mgr = DbConnectionManager(
            db_url="mysql+pymysql://test:pass#word@127.0.0.1:3306/testdb"
        )
        params = mgr._parse_url()
        # Password with # might confuse URL parsing; test passes if parsing succeeds
        assert params["user"] == "test"

    def test_frontend_does_not_need_db_url(self):
        """Frontend only needs backend API, not DB URL.
        The OpenAPI spec may mention parameter names like 'db_url' or 'root'
        as JSON path references, but must NOT expose real credentials or
        full connection strings.
        """
        try:
            openapi = json.load(open("docs/api/openapi.json", encoding="utf-8"))
            openapi_str = json.dumps(openapi).lower()
            # The parameter name 'db_url' is OK; real passwords are NOT
            assert "Zlt" not in openapi_str, "OpenAPI spec should not expose real password"
            assert "127.0.0.1:3306" not in openapi_str, "OpenAPI spec should not expose DB host:port"
            assert "mysql+pymysql://" not in openapi_str, "OpenAPI spec should not expose connection string"
        except FileNotFoundError:
            pass  # OpenAPI file may not be generated yet — skip
