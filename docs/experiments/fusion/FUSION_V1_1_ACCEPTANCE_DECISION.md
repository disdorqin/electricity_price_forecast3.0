# EFM3 Fusion v1.1 — Acceptance Decision

## Summary

Fusion v1.1 reaches **SHADOW_MONITORING_READY** with a 0.20pp validation improvement
over official baseline (25.84 → 25.64). This meets the acceptance threshold but is
a borderline result that must **not be overstated**.

## Key Facts

| Metric | Value | Note |
|--------|-------|------|
| Official baseline (val) | 25.84 | SGDFNet prediction |
| Best fusion (val) | 25.64 | conservative_fusion_v1 / all v1.1 variants |
| Delta | **-0.20pp** | Meets threshold (>=0.20), but marginal |
| DA anchor (val) | **25.59** | Simpler, stronger — -0.25pp |
| Selector (val) | 25.99 | Worse than official — +0.15pp |
| P3 correction rate | ~13% | Only 369/2832 hours have `applied=True` |
| P3 identical hours | 87% | `shadow_corrected_pred == original_pred` |

## Honest Assessment

**The improvement does not come from "complex fusion."**
The 0.20pp gain is entirely attributable to the Winter DA anchor policy:

- Winter months (11/12/1/2): official 24.70 → DA anchor 24.36 (improves **0.34pp**)
- Non-winter months: official 19.57 = fusion 19.57 (identical — no change)

Conservative_fusion_v1 applies **Winter DA-only + P3 very-high-confidence overlay**.
Since P3 corrections are effectively zero in current production (87% of hours
unchanged), the Winter DA anchor is the sole source of improvement.

**DA anchor alone (25.59) beats the fusion (25.64) on validation set.**
This means the simplest possible policy — "use DA anchor in winter" — outperforms
all complex fusion variants.

## What Fusion v1.1 Actually Is

Fusion v1.1 is **not** a model fusion system. It is a **seasonal DA policy router**:

```
If month in [11, 12, 1, 2]:
    use DA_anchor
else:
    use official_baseline (SGDFNet)
```

The P3 extreme shadow and selector shadow overlays do not contribute measurable
improvement at current thresholds and should remain **diagnostic-only**.

## Decision

**FUSION_V1_1_RECOMMENDATION: SHADOW_MONITORING_READY**
**FUSION_V1_1_RESULT: PASS**

This allows:
- Shadow monitoring of the seasonal DA policy
- Diagnostic tracking of P3/selector in parallel
- Documentation of the finding for future reference

This does **NOT** allow:
- Production deployment as a model fusion system
- Champion replacement
- submission_ready.csv generation
- final output overwrite

## Next Steps

1. Compare seasonal DA router vs 2.5 stable baseline (see comparison plan)
2. If confirmed, document as seasonal DA policy router, not fusion engine
3. Keep P3/selector in diagnostic-only mode until correction rates improve
