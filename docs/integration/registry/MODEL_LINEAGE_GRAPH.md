# EFM3 V3.1-FINAL — Model Lineage Graph

```
PRODUCTION BASELINE (UNCHANGED)
├── DD (day-ahead price as RT) → FALLBACK_MODEL
├── IHMAE (intra-hour MAE) → SECONDARY_CANDIDATE
└── A05 = 0.5×DD + 0.5×IHMAE → PRIMARY

RETAINED RESEARCH COMPONENTS
├── NegCorr_w120_V5_CANONICAL → CORRECTION_ON_A05
│   └── Basis for w180 (wider window, same architecture)
├── NegCorr_w180_V5_CANONICAL → CORRECTION_ON_A05
└── NegCorr_w120_V51_ADAPTIVE_PREWARM → RESEARCH reference (V5.1)

BLOCKED (infra)
├── NegCorr_w270/w360/expanding → needs full-history training panel
├── Robust Direct Delta (all variants) → needs window-cost infra
└── Joint Midday Curve (all variants) → needs window-cost infra

REJECTED
├── PC1 curve correction → destructive on overall
├── Central expert → failed V5 screening
├── Spike expert → failed V5 screening
├── Original trident → replaced by A05
└── UnderCorr → cold-start unsafe (research reference only)
```
