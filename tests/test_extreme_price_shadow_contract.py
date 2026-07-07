"""P3.2 shadow contract tests.

Contract checks required by the spec:
  - shadow output has exactly 24 rows
  - hour_business spans 1..24
  - no NaN anywhere
  - shadow_only == True
  - original_pred is preserved (never replaced by the corrected value)
  - required schema columns are present
"""
from types import SimpleNamespace

import pandas as pd

from pipelines.extreme_price_shadow import (
    run_extreme_price_shadow,
    load_config,
    REQUIRED_COLUMNS,
)

REAL_LEDGER = "outputs/ledger"


def _args(date, runs_root, ledger_root=REAL_LEDGER):
    return SimpleNamespace(
        date=date, start=None, end=None, shadow_only=False,
        extreme_price_shadow_config=None,
        ledger_root=ledger_root, runs_root=runs_root,
    )


def _run(date, tmp_path):
    m = run_extreme_price_shadow(_args(date, str(tmp_path)))
    return pd.read_csv(m["results"][date]["shadow_predictions_csv"])


def test_default_config_disabled():
    # Spec check #8: default configuration does NOT enable the shadow.
    assert load_config().enabled is False


def test_24_rows(tmp_path):
    df = _run("2026-02-10", tmp_path)
    assert len(df) == 24


def test_24_rows_degraded_path(tmp_path):
    # 2026-07-03 has no ledger predictions -> degraded but still a valid 24-row contract.
    df = _run("2026-07-03", tmp_path)
    assert len(df) == 24


def test_hours_1_to_24(tmp_path):
    df = _run("2026-02-10", tmp_path)
    assert sorted(df["hour_business"].tolist()) == list(range(1, 25))


def test_no_nan(tmp_path):
    df = _run("2026-02-10", tmp_path)
    assert not df.isna().any().any()


def test_shadow_only_true(tmp_path):
    df = _run("2026-02-10", tmp_path)
    assert (df["shadow_only"] == True).all()  # noqa: E712


def test_original_pred_preserved(tmp_path):
    df = _run("2026-02-10", tmp_path)
    assert "original_pred" in df.columns
    # original_pred is finite and present for every hour (never dropped/replaced)
    assert df["original_pred"].notna().all()
    # shadow_corrected_pred exists separately; when not applied it equals original_pred
    not_applied = df[~df["applied"]]
    if len(not_applied):
        assert (not_applied["shadow_corrected_pred"] == not_applied["original_pred"]).all()


def test_required_columns_present(tmp_path):
    df = _run("2026-02-10", tmp_path)
    for col in REQUIRED_COLUMNS:
        assert col in df.columns, f"missing required column: {col}"
