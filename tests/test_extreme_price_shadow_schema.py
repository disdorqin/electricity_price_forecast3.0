"""P3.2 shadow schema tests.

Column-level schema + safety guarantees:
  - required columns present with sensible types
  - rollback_reason column present and non-null
  - correction cap is present (cap_hit column + abs(correction_amount) <= CAP_ABS)
    and echoed in the JSON report
  - spike_type values are within the valid set
  - no NaN
"""
import json
from types import SimpleNamespace
from pathlib import Path

import pandas as pd

from pipelines.extreme_price_shadow import (
    run_extreme_price_shadow,
    load_config,
    REQUIRED_COLUMNS,
)

REAL_LEDGER = "outputs/ledger"
DATE = "2026-02-10"


def _args(date, runs_root, ledger_root=REAL_LEDGER):
    return SimpleNamespace(
        date=date, start=None, end=None, shadow_only=False,
        extreme_price_shadow_config=None,
        ledger_root=ledger_root, runs_root=runs_root,
    )


def _run(date, tmp_path):
    m = run_extreme_price_shadow(_args(date, str(tmp_path)))
    res = m["results"][date]
    csv_path = res["shadow_predictions_csv"]
    df = pd.read_csv(csv_path)
    shadow_dir = Path(csv_path).parent
    return df, shadow_dir, res


def test_required_columns_present(tmp_path):
    df, _, _ = _run(DATE, tmp_path)
    for col in REQUIRED_COLUMNS:
        assert col in df.columns, f"missing required column: {col}"


def test_column_types(tmp_path):
    df, _, _ = _run(DATE, tmp_path)
    assert df["hour_business"].dtype.kind == "i"          # integer hours
    assert df["original_pred"].dtype.kind == "f"          # float price
    assert df["shadow_corrected_pred"].dtype.kind == "f"
    # shadow_only serializes as boolean True in CSV
    assert set(df["shadow_only"].unique()).issubset({True})


def test_rollback_reason_present(tmp_path):
    df, _, _ = _run(DATE, tmp_path)
    assert "rollback_reason" in df.columns
    assert df["rollback_reason"].notna().all()
    # only valid tokens: "none" or a non-empty reason string
    assert df["rollback_reason"].apply(lambda v: v in ("none",) or (isinstance(v, str) and len(v) > 0)).all()


def test_correction_cap_present(tmp_path):
    df, shadow_dir, _ = _run(DATE, tmp_path)
    cfg = load_config()
    # cap_hit column exists (cap presence signal)
    assert "cap_hit" in df.columns
    # every correction is bounded by the absolute cap
    assert (df["correction_amount"].abs() <= cfg.CAP_ABS + 1e-6).all()
    # the cap is echoed in the JSON report
    report = json.load(open(shadow_dir / "shadow_report.json"))
    assert report["correction_cap_abs"] == cfg.CAP_ABS
    assert "cap_hit_count" in report["summary"]


def test_spike_type_valid(tmp_path):
    df, _, _ = _run(DATE, tmp_path)
    assert set(df["spike_type"].unique()).issubset({"none", "high", "low"})


def test_no_nan(tmp_path):
    df, _, _ = _run(DATE, tmp_path)
    assert not df.isna().any().any()
