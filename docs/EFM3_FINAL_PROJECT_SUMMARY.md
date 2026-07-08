# EFM3 Final Project Summary

## 1. Project Goal

Build and evaluate a real-time electricity price forecasting system for the
Shandong power market (山东电力现货市场), with experimental "fusion" overlay
lines (P1 day-ahead shadow, P2 realtime selector, P3 extreme correction)
to determine if they add value over the 3.0 official baseline.

## 2. Why Fusion Converged to a Seasonal DA Router

The original fusion vision was a complex multi-layer policy graph combining:
- P1 day-ahead shadow candidates
- P2 realtime DA-SGDF selector
- P3 extreme price correction overlay

After 14 months of evaluation across 10+ policy variants with train/validation
split and strict leakage controls, **every variant converged to the same result**:

**The only measurable improvement comes from using DA anchor in winter.**

This is not because the fusion system failed — it's because the data
honestly shows that:

1. **DA anchor (25.59)** beats SGDFNet official (25.84) in validation,
   entirely due to winter months (24.36 vs 24.70)
2. **P3 corrections are effectively zero** in practice — 87% of hours
   have `shadow_corrected_pred == original_pred`
3. **Selector degrades performance** (25.99, worse than official 25.84)

## 3. What P1/P2/P3 Found

| Line | Finding |
| ---- | ------- |
| **P1 day-ahead** | Cfg05 candidate shows 14.68 sMAPE. Remains in shadow registry — not merged into realtime fusion. |
| **P2 realtime selector** | Selector shadow degrades validation set (25.99 > 25.84). Never recommended for production. Winter DA-only is the correct default. |
| **P3 extreme correction** | 87% of hours produce identical predictions. Corrections are too rare to add measurable value at current thresholds. Keep running for negative-price monitoring only. |

## 4. Why Fusion v1.1 Is Not Complex Fusion

Fusion v1.1 tested 5 new policy variants on top of v1's 10 variants.
Every v1.1 variant produced **identical results** to `conservative_fusion_v1`
(validation 25.64). This is because:

- P3 corrections don't fire (zero effective change)
- Selector rarely triggers (and when it does, it degrades quality)
- The only effective rule is winter DA anchor

The honest conclusion: **Fusion v1.1 is a Seasonal DA Policy Router, not a
model fusion engine.** Calling it "fusion" would be misleading.

## 5. Why Not Production

| Reason | Detail |
| ------ | ------ |
| Improvement is marginal | 0.20pp — exactly meets threshold, not robust |
| DA anchor alone is stronger | 25.59 vs 25.64 — simplest baseline wins |
| P3/selector add nothing | All complex variants converge to same result |
| Winter-only benefit | Non-winter months show zero improvement |
| Selector actively harms | Validation 25.99 > official 25.84 |

## 6. Why Shadow Monitoring Is the Correct Stage

Shadow monitoring allows:
- Collecting live data on the seasonal DA router's actual performance
- Monitoring P3 extreme correction coverage over time
- Detecting winter DA anchor drift without production risk
- Making data-driven production decisions later (not now)

The current evidence supports **monitoring**, not **deploying**.

## 7. Recommended Operation

```bash
# Default operation (no shadows — clean, safe)
python main.py YYYY-MM-DD

# With shadow monitoring (for diagnostic collection)
python main.py YYYY-MM-DD --enable-extreme-price-shadow
python main.py YYYY-MM-DD --enable-realtime-da-sgdf-selector-shadow
```

Fusion v1.1 `--enable-fusion-shadow-v1` is NOT available on main — it was
registry/docs/tests only. The policy it defines (winter DA anchor) is already
understood and can be applied manually.

## 8. Evidence Needed for Production

If seasonal DA router production is ever considered:

1. 30+ days of live shadow monitoring data
2. Validation that winter DA anchor improvement is stable
3. P3 correction rate improvement (currently 13% applied, need >50%)
4. Selector performance recovery (currently degrading vs official)
5. 2.5 baseline comparison (currently unavailable — repo archived)
6. Human review of delivery impact
7. Rollback plan documented
8. No leakage/contamination during entire monitoring period

## Final Note

The 0.20pp improvement is small, honest, and defensible. It is not
overstated. The engineering process of testing 15+ policy variants,
finding most add nothing, and converging to a simple seasonal rule
is itself a valuable result — it proves the screening process worked.
