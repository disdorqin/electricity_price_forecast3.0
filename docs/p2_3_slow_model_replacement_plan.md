# P2.3 Slow Model Replacement Plan

Generated: 2026-07-07 UTC

---

## Overview

P2.2 Fast Closeout identified 3 of 4 realtime models as too slow or
incomplete for production. This document outlines the replacement/caching
strategy for each.

---

## 1. RT916 — NOT_PRODUCTION_READY

### Why Eliminated

| Factor | Value |
|--------|------:|
| Avg inference time | 1840s/day (~31 min) |
| Completed days | 19/363 (5%) |
| GPU dependency | RTX 4060 |
| GPU stability | crashes observed (rc=1073807364) |
| Coverage | ~2 days per month max |
| Production impact | blocks realtime pipeline |

### RT916 was designed as a spike-aware fusion model. Its high runtime
comes from `run_joint_da_rt_daily_backtest` which fits a joint DA/RT
model daily. This is architecturally expensive for a realtime-only task.

### Replacement Routes

| Route | Effort | Benefit | Feasibility |
|-------|--------|---------|-------------|
| **A. Spike-only lightweight model** | Medium | Spike detection at 1% of runtime | High — replace end-to-end training with a small spike classifier |
| **B. Cache-only inference** | Low | Precompute RT916 for known spike patterns | Medium — requires spike pattern clustering |
| **C. Knowledge distillation** | High | Lightweight student model | Low — requires full teacher backfill which failed |
| **D. Replace with risk classifier** | Medium | P3 extreme price shadow as spike signal | High — P3 already in development |
| **E. Abandon online full-day inference** | None | Eliminate RT916 from realtime entirely | Immediate — recommended default |

### Recommendation

**Adopt Route D + E.** Eliminate RT916 from the online realtime pipeline.
Replace its spike-awareness function with the P3 extreme price shadow/
risk classifier, which is already under development and does not require
daily model fitting. If spike-only lightweight inference is needed later,
Route A can be pursued independently.

---

## 2. TimeMixer — CACHE_ONLY

### Current Status

| Factor | Value |
|--------|------:|
| Avg inference time | 264-327s/day (~5 min) |
| Completed days | 35/363 (10%) |
| GPU dependency | RTX 4060 (heavy slot) |
| GPU stability | Stable during runs |
| Production viability | Acceptable for daily, not for backfill |

### Current Positioning

- **Not an online production requirement.** TimeMixer should NOT be in
  the critical path of the daily realtime prediction pipeline.
- **Cache-only.** Precompute TimeMixer predictions for key test windows
  or periodically (weekly/monthly) rather than daily.
- **Offline research.** TimeMixer can serve as a research baseline for
  future architecture comparisons.
- **No replacement needed.** TimeMixer is not the bottleneck — it is
  simply too heavy for daily production.

### Constraints for Cache

- Maximum window: 30-90 days cached at a time
- Refresh frequency: weekly or on model retrain
- Storage: ~24 KB per day per model (single CSV)
- Risk: stale predictions on regime-change days (spike clusters)

---

## 3. TimesFM — SKIPPED_PENDING

### Current Status

| Factor | Value |
|--------|------:|
| Avg inference time | ~13s/day (fastest model) |
| Completed days | 1/363 (0%) |
| GPU dependency | RTX 4060 (heavy slot) |
| GPU stability | Not tested (never ran) |
| Bottleneck | Heavy GPU slot contention with TimeMixer |

### Why It Failed

TimesFM was never dispatched during P2.2 backfill. The orchestrator
assigned both heavy GPU slots (MAX_HEAVY=2) to TimeMixer workers.
TimesFM jobs remained in the pending queue until all TimeMixer workers
completed, which never happened before the Fast Closeout termination.

### Path to Production

| Step | Status | Notes |
|------|--------|-------|
| 1. Dedicated slot | Pending | TimesFM needs its own slot or a light-slot classification |
| 2. 3-month smoke test | **In progress (P2.3)** | 2025-03, 2025-09, 2026-05 |
| 3. If smoke passes → KEEP_CANDIDATE | Pending | Mark as production-viable realtime model |
| 4. If smoke fails → SKIPPED_UNSTABLE | Fallback | Mark as not worth further investment |

### Recommendation

**Wait for P2.3 TimesFM smoke test results.** If the 3-month smoke test
shows stable ~13s/day with no GPU issues, promote TimesFM to
KEEP_CANDIDATE and include in the lite ensemble. If GPU failures appear,
mark SKIPPED_UNSTABLE and deprioritize.

---

## 4. SGDFNet — KEEP

### Current Status

| Factor | Value |
|--------|------:|
| Avg inference time | 40s/day (fastest reliable) |
| Completed days | 363/363 (100%) |
| GPU dependency | None (CPU-only) |
| GPU stability | N/A (CPU) |
| N failures | 0 |

### Why It Succeeds

- CPU-only: no GPU contention, no CUDA crashes
- Lightweight: 40s/day fits any batch or realtime window
- Full coverage: all 363 days complete on first pass
- Simple architecture: SGDFNet's CPU pipeline is robust

### Next Steps

1. Integrate P3 extreme price correction
2. Production adapter review
3. 3.0 shadow comparison (when available)
4. Monitor ensemble performance once TimesFM joins

---

## Summary

| Model | P2.2 Decision | P2.3 Plan | Timeline |
|-------|--------------|-----------|----------|
| SGDFNet | KEEP | ✅ Production candidate | Immediate |
| TimesFM | SKIPPED_PENDING | ⏳ 3-month smoke test | This session |
| TimeMixer | CACHE_ONLY | 📦 Cache offline | No change |
| RT916 | NOT_PRODUCTION_READY | 🗑️ Replace with P3 risk classifier | P3 timeline |
