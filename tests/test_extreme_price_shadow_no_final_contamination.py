"""P3.2 no-final-contamination tests.

Guarantees required by the spec:
  - shadow does NOT modify final/ or submission_ready.csv
  - shadow does NOT write submission_ready.csv anywhere
  - shadow failure does NOT affect the main chain (no exception, safe manifest)
  - applied corrections never replace the original fused realtime prediction
  - default configuration does not enable the shadow
"""
from types import SimpleNamespace
from pathlib import Path

import pandas as pd

from pipelines.extreme_price_shadow import (
    run_extreme_price_shadow,
    run_extreme_price_shadow_safe,
    load_config,
)

REAL_LEDGER = "outputs/ledger"
DATE = "2026-02-10"


def _args(date, runs_root, ledger_root=REAL_LEDGER, **kw):
    d = dict(date=date, start=None, end=None, shadow_only=False,
             extreme_price_shadow_config=None,
             ledger_root=ledger_root, runs_root=runs_root)
    d.update(kw)
    return SimpleNamespace(**d)


def _make_fake_run(runs_root: str, date: str) -> Path:
    """Create a fake 3.0 run output (final + realtime final) to test non-contamination."""
    root = Path(runs_root) / date
    (root / "final").mkdir(parents=True, exist_ok=True)
    (root / "realtime" / "final").mkdir(parents=True, exist_ok=True)
    periods = ["1_8"] * 8 + ["9_16"] * 8 + ["17_24"] * 8
    rt = pd.DataFrame({
        "business_day": [date] * 24,
        "ds": [f"{date} {h:02d}:00:00" for h in range(1, 25)],
        "hour_business": list(range(1, 25)),
        "period": periods,
        "y_fused": [300.0] * 24,
    })
    rt.to_csv(root / "realtime" / "final" / "realtime_final_predictions.csv", index=False)
    rt.to_csv(root / "final" / "realtime_final_predictions.csv", index=False)
    sub = pd.DataFrame({
        "business_day": [date] * 24,
        "ds": [f"{date} {h:02d}:00:00" for h in range(1, 25)],
        "hour_business": list(range(1, 25)),
        "period": periods,
        "dayahead_price": [280.0] * 24,
        "realtime_price": [300.0] * 24,
    })
    sub.to_csv(root / "final" / "submission_ready.csv", index=False)
    return root


def test_default_config_off():
    assert load_config().enabled is False


def test_submission_ready_not_modified(tmp_path):
    root = _make_fake_run(str(tmp_path), DATE)
    sub_path = root / "final" / "submission_ready.csv"
    rt_final_path = root / "final" / "realtime_final_predictions.csv"
    before_sub = sub_path.read_bytes()
    before_rt = rt_final_path.read_bytes()

    run_extreme_price_shadow(_args(DATE, str(tmp_path)))

    # Final outputs must be byte-identical (never touched by the shadow).
    assert sub_path.read_bytes() == before_sub
    assert rt_final_path.read_bytes() == before_rt
    # No submission_ready.csv is ever written inside the shadow directory.
    shadow_dir = root / "extreme_price_shadow"
    assert (shadow_dir / "shadow_predictions.csv").exists()
    assert not (shadow_dir / "submission_ready.csv").exists()


def test_applied_does_not_replace_original(tmp_path):
    root = _make_fake_run(str(tmp_path), DATE)
    m = run_extreme_price_shadow(_args(DATE, str(tmp_path)))
    df = pd.read_csv(m["results"][DATE]["shadow_predictions_csv"])
    # original_pred comes straight from the 3.0 run output (300.0) and is preserved.
    assert df["original_pred"].tolist() == [300.0] * 24
    # Corrected values live in their own column; original is never overwritten.
    assert "shadow_corrected_pred" in df.columns


def test_shadow_failure_does_not_affect_main(tmp_path):
    # args without a date -> run_extreme_price_shadow raises; safe wrapper must
    # catch it and return a manifest (never propagate to the main chain).
    args = _args(None, str(tmp_path))
    manifest = run_extreme_price_shadow_safe(args)
    assert isinstance(manifest, dict)
    assert manifest.get("status") == "failed"
    assert manifest.get("final_contaminated") is False
    assert manifest.get("main_chain_affected") is False
    assert manifest.get("shadow_only") is True
