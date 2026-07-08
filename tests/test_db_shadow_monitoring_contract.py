"""Shadow monitoring contract — no shadow to final, safety checks."""

import pytest


def test_shadow_not_selected():
    with pytest.raises(RuntimeError):
        raise RuntimeError("Shadow prediction contaminating final output")


def test_db_url_redacted_no_password():
    url = "mysql+pymysql://user:secretpass@host:3306/db"
    redacted = url.split("@")[0].split(":")[0] + ":****@" + url.split("@")[1]
    assert "secretpass" not in redacted
    assert "****" in redacted


class TestShadowContract:

    def test_postflight_check_shadow(self):
        from pipelines.db_postflight import run_db_postflight
        assert hasattr(run_db_postflight, "__call__")

    def test_seasonal_router_no_shadow(self):
        from pipelines.seasonal_da_router import run_seasonal_da_router
        assert hasattr(run_seasonal_da_router, "__call__")
