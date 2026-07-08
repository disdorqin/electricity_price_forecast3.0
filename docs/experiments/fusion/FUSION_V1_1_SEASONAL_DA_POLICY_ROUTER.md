# EFM3 Seasonal DA Policy Router (Fusion v1.1)

## Overview

The Seasonal DA Policy Router is the effective policy extracted from
Fusion Chain v1.1 evaluation. Despite being called "fusion," the actual
improvement comes from a single, simple rule: **use DA anchor in winter**.

This document defines the exact policy and its constraints.

## Policy Definition

```python
def seasonal_da_policy_router(month: int, da_anchor: float, official_pred: float) -> float:
    """
    Seasonal DA Policy Router — the effective strategy from Fusion v1.1.
    
    Winter months (11, 12, 1, 2): use DA anchor (day-ahead clearing price).
    Non-winter months: use official baseline (SGDFNet realtime prediction).
    
    Args:
        month: 1-12
        da_anchor: day-ahead clearing price
        official_pred: SGDFNet realtime prediction (or fused realtime)
    
    Returns:
        Selected prediction
    """
    if month in (11, 12, 1, 2):
        return da_anchor
    return official_pred
```

## Performance

| Metric | Official | DA Router | Delta |
|--------|--------:|----------:|------:|
| Overall (14mo) | 21.02 | 20.93 | -0.10 |
| Validation (6mo) | 25.84 | 25.64 | -0.20 |
| Winter | 24.70 | 24.36 | -0.34 |
| Non-winter | 19.57 | 19.57 | 0.00 |
| Negative | 15.39 | 16.68 | +1.29 |
| Spike | 16.92 | 16.41 | -0.51 |

**Trade-off**: The router improves winter and spike hours but degrades
negative hours (DA anchor is less accurate for negative prices).

## Diagnostic-Only Overlays

These overlays are **NOT** part of the active policy but are tracked
for diagnostic purposes:

### Selector Shadow (P2.11)

- Status: DIAGNOSTIC_ONLY
- Validation performance: 25.99 (worse than official)
- Non-winter only, not in 17-24 period
- Not recommended for production at current thresholds

### P3 Extreme Shadow

- Status: DIAGNOSTIC_ONLY
- 87% of hours produce identical predictions to original
- Current correction rate too low to add measurable value
- Keep running for negative-price monitoring

## Safeguards

The following are **STRICTLY FORBIDDEN**:

| Practice | Status |
|----------|--------|
| Selector winter promotion | ❌ BLOCKED |
| SGDFNet-only replacement | ❌ BLOCKED |
| P3 normal correction in production | ❌ BLOCKED |
| submission_ready.csv generation | ❌ BLOCKED |
| Final output overwrite | ❌ BLOCKED |
| Champion replacement | ❌ BLOCKED |
| Production deployment without shadow-only | ❌ BLOCKED |

## Deployment

- Mode: Shadow monitoring only
- Default: **Disabled** (must be explicitly enabled)
- Config: `configs/fusion_shadow_v1_1.yaml` (enabled: false)
- Outputs: `outputs/fusion_shadow_v1/`, `exports/efm3_candidates/fusion_chain/`
- Never: `final/`, `submission_ready.csv`, champion registry
