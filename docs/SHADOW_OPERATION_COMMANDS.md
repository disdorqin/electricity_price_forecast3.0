# Shadow Operation Commands

> How to run each shadow module. All shadows are default OFF.

---

## Environment

```bash
# All commands require conda epf-2 (main.py depends on pymysql)
PYTHON=D:/computer_download/environment/conda/epf-2/python.exe
```

## Production Default (No Shadows)

```bash
# Default production run — no shadow output generated
$PYTHON main.py YYYY-MM-DD
```

**This is the only production command.** All others are observation-only.

## Enable P3 Extreme Price Shadow

```bash
# Controlled shadow — corrects extreme negative/spike prices observationally
$PYTHON main.py YYYY-MM-DD --enable-extreme-price-shadow

# Shadow-only mode (reaffirms intent)
$PYTHON main.py YYYY-MM-DD --pipeline extreme_price_shadow

# With custom config
$PYTHON main.py YYYY-MM-DD --enable-extreme-price-shadow --extreme-price-shadow-config /path/to/config.yaml
```

**Output:** `outputs/runs/{date}/extreme_price_shadow/`
- `shadow_predictions.csv` — 24 rows, shadow_only=true, original_pred preserved
- `shadow_report.json` — Summary metrics
- `shadow_report.md` — Readable report
- `rollback_report.json` — Rollback audit

**Never writes:** `final/`, `submission_ready.csv`

## Enable Realtime DA-SGDF Selector Shadow

```bash
# Observes selector behavior without affecting production
$PYTHON main.py YYYY-MM-DD --enable-realtime-da-sgdf-selector-shadow

# With custom config
$PYTHON main.py YYYY-MM-DD --enable-realtime-da-sgdf-selector-shadow \
  --realtime-selector-shadow-config /path/to/config.yaml
```

> **Note:** The selector shadow reads SGDFNet predictions from the run output
> directory. Use the `ledger_full` pipeline (default) to generate them.

**Output:** `outputs/runs/{date}/realtime_da_sgdf_selector_shadow/`
- `selector_shadow_predictions.csv` — 24 rows with da_anchor, sgdfnet_pred, selected_model
- `selector_shadow_report.json` — Summary
- `selector_shadow_report.md` — Readable report

**Never writes:** `final/`, `submission_ready.csv`

## Enable Both Shadows Simultaneously

```bash
# Both shadows run safely in parallel — separate directories, no conflicts
$PYTHON main.py YYYY-MM-DD --enable-extreme-price-shadow --enable-realtime-da-sgdf-selector-shadow
```

**Verified:** P3.5 coexistence test — 20 winter dates, 480 hours, 0 conflicts.

## Running Tests

```bash
# All shadow-related test suites (conda epf-2)
$PYTHON -m pytest tests/test_extreme_price_shadow_*.py \
  tests/test_realtime_da_sgdf_selector_shadow_*.py \
  tests/test_system_shadow_coexistence_registry.py \
  tests/test_extreme_price_winter_monitoring_registry.py \
  -q

# Registry tests
$PYTHON -m pytest tests/test_dayahead_shadow_registry.py \
  tests/test_realtime_lite_candidate_registry.py \
  tests/test_realtime_da_sgdf_selector_registry.py \
  tests/test_realtime_selector_winter_policy.py \
  tests/test_realtime_canonical_loader.py \
  -q
```

## Important Notes

1. **Default is OFF**: Without `--enable-*` flags, no shadow code runs.
2. **Shadow directory isolation**: Each shadow writes to its own directory.
3. **No output pollution**: Neither shadow reads or writes `final/` or `submission_ready.csv`.
4. **Winter selector behavior**: In Nov-Feb, selector chooses DA_anchor 97%+.
   This is expected (P2.13 winter NO-GO policy).
5. **P3 history requirement**: P3 shadow requires at least 5-10 days of history
   for classifier training. Early winter dates (Nov 1-10) may produce degraded results.
6. **No risk pack**: P3 shadow operates in degraded feature mode until risk pack is available.
