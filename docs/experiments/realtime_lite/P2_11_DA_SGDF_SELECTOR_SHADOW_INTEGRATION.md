# P2.11 DA-SGDF Selector Shadow Integration

## Overview

This branch (`agent/p2.11-realtime-da-sgdf-selector-shadow`) integrates the
P2.9 conservative DA-SGDF selector as a **default-off shadow adapter** in the
3.0 main repo.

## Files Added

| File | Status | Notes |
|------|--------|-------|
| `pipelines/realtime_da_sgdf_selector_shadow.py` | ✅ New | Conservative gate selector (default off) |
| `configs/realtime_da_sgdf_selector_shadow.yaml` | ✅ New | Configuration |
| `cli/parser.py` | ✏️ Modified | Added --enable-realtime-da-sgdf-selector-shadow |
| `main.py` | ✏️ Modified | Hook for ledger_full and ledger_full_range |
| `tests/test_realtime_da_sgdf_selector_shadow_contract.py` | ✅ New | 15 contract tests |
| `tests/test_realtime_da_sgdf_selector_shadow_no_final_contamination.py` | ✅ New | 4 no-contamination tests |
| `docs/experiments/realtime_lite/P2_11_DA_SGDF_SELECTOR_SHADOW_INTEGRATION.md` | ✅ New | This file |
| `docs/experiments/realtime_lite/P2_11_DA_SGDF_SELECTOR_SHADOW_REPORT.md` | ✅ New | Integration report |

## Selector Rules

Conservative gate (P2.9):
- Default: DA_anchor
- SGDFNet selected only when:
  - DA-SGDF gap > 50
  - Price is normal (0 < DA < 200)
  - Not in 17_24 period
  - Not negative price
- Fallback: always DA
- Expected SGD selection rate: ~2% (147/7249 hours in P2.9)

## Activation

```bash
# Default (no selector output)
python main.py 2026-07-03

# With selector shadow
python main.py 2026-07-03 --enable-realtime-da-sgdf-selector-shadow

# With custom config
python main.py 2026-07-03 --enable-realtime-da-sgdf-selector-shadow \
    --realtime-selector-shadow-config configs/custom_selector.yaml
```

## Safety

- Default off: no flag = no output
- Failures never modify exit_code or delivery_status
- No final/ or submission_ready.csv writes
- No champion replacement
- No RT916/TimeMixer dependency
