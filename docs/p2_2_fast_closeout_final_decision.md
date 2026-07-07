# P2.2 Fast Closeout Final Decision

Generated: 2026-07-07 UTC

---

## Overview

P2.2 was designed to verify whether the 2.5 realtime four-model GEF fusion
(≈23% claimed improvement over DA anchor) is effective on calm/spring-summer
windows, given that Phase G proved DA anchor dominates on the winter window
(2026-01-25 ~ 2026-02-25). The original plan backfilled 363 days × 4 models
realtime (sgdfnet, timemixer, rt916, timesfm) across 10 test windows + 2
history months.

The experiment was terminated early (Fast Closeout) because 3 of 4 models
failed runtime/cost criteria. Only sgdfnet completed all 363 days.

---

## Model Decisions

| Model     | Status              | Avg sec/day | Completed | Reason |
|-----------|---------------------|------------:|----------:|--------|
| sgdfnet   | **KEEP** ✅         | 40s (CPU)   | 363/363   | CPU-only, 100% coverage, production-ready |
| timemixer | **CACHE_ONLY** ⚠️   | 295s (GPU)  | 35/363    | ~5 min/day GPU, acceptable daily inference but full backfill ~29.5h |
| rt916     | **NOT_PRODUCTION_READY** ❌ | 1840s (GPU)| 19/363 | ~31 min/day GPU, GPU crashes observed, not viable for online production |
| timesfm   | **SKIPPED_PENDING** ❌ | 13s (GPU)  | 1/363     | Never dispatched due to heavy GPU slot contention; 13s/day is fast, scheduling fix needed |

---

## 2.5 Four-Model Fusion Status

**Not verified.** Only sgdfnet has full data. The remaining three models
(timemixer/rt916/timesfm) have 35/19/1 days respectively — insufficient for
the trailing-30d GEF weight learning mechanism (`DailyLedgerGEF` requires
4 concurrent models with ≥30d history per window). Without multi-model
coverage, the central P2.2 question remains unanswered.

All 6 fusion/blend variants were SKIPPED:
- fused_2p5 (GEF weights)
- equal_blend, rolling_opt, period_aware
- sgdfnet_dominant, rt916_spike_only

---

## 3.0 Shadow Status

**This candidate does NOT enter 3.0 shadow.** Reason:
- sgdfnet alone is not a shadow of the 2.5 fusion; a single-model candidate
  cannot represent the four-model ensemble that the 2.5 release claims.
- The 3.0 shadow comparison requires a complete or representative replica
  of the target pipeline, which P2.2 cannot provide.

---

## Slow Models as Production Risk

| Model     | Risk Level | Impact |
|-----------|-----------|--------|
| rt916     | **Critical** | 1840s/day + GPU crashes would block realtime pipeline |
| timemixer | **Medium**  | 295s/day GPU is acceptable for daily but not batch backfill |
| timesfm   | **Low**     | 13s/day — scheduling is the only barrier |

---

## Transition to P2.3 Lite Candidate

P2.3 picks up the production-viable components from P2.2:

1. **DA anchor** — always-available baseline
2. **SGDFNet** — verified KEEP, CPU-only, full coverage
3. **TimesFM** — pending scheduling fix (3-month smoke test in P2.3)
4. **P3 extreme price shadow/risk** — auxiliary observation only
5. **Lightweight residual/risk model** — future work

Prohibited from P2.3 production candidate:
- ❌ rt916 in current implementation
- ❌ timemixer as online required model
- ❌ Full-scale multi-model backfill
- ❌ Shadow/champion status
