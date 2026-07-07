# P2.3 Slow Model Replacement Plan

## RT916 — Eliminated from Production

### Why Eliminated

| Factor | Value |
|--------|------:|
| Avg inference time | 1840s/day (~31 min) |
| Completed days | 19/363 (5%) |
| GPU dependency | RTX 4060 |
| GPU stability | Crashes observed (rc=1073807364) |
| Production impact | Blocks realtime pipeline |

### Replacement Path

**Recommended: Replace with P3 extreme price shadow/risk classifier.**

The P3 extreme price correction pipeline is already under development
and can handle spike detection without daily model fitting. Other
options considered:

- Spike-only lightweight model: Medium effort, lower benefit
- Cache-only inference: Low effort, medium benefit
- Knowledge distillation: High effort, low feasibility
- Abandon online full-day inference: Immediate, recommended

## TimeMixer — Cache-Only

| Factor | Value |
|--------|------:|
| Avg inference time | 295s/day (~5 min) |
| Completed days | 35/363 (10%) |
| GPU required | Yes (heavy slot) |
| Full backfill projection | ~29.5h |

**Positioning**: Cache-only, offline research. Not in the critical
production path. Precompute for key windows; refresh weekly.

**No replacement needed.** TimeMixer is not the bottleneck — it is
simply too heavy for daily online production.

## TimesFM — Dedicated Slot Required

| Factor | Value |
|--------|------:|
| Avg inference time | 11.9s/day (fastest) |
| Completed days | 91/92 (98.9%) |
| GPU required | Yes (~1.3 GB) |
| Scheduling fix needed | Yes |

**Path to production**:
1. ✅ 3-month smoke test passed (P2.3)
2. ⏳ Dedicated GPU slot scheduling
3. ⏳ Wider smoke test (10-window)
4. ⏳ SGDFNet + TimesFM lite ensemble test

## SGDFNet — Production Candidate

| Factor | Value |
|--------|------:|
| Avg inference time | 40s/day |
| Completed days | 363/363 |
| GPU required | None (CPU) |
| Failures | 0 |

Already the core of the realtime lite candidate. No replacement needed.

## Forward Direction

**Lite Realtime Pipeline**: SGDFNet (CPU, core) + TimesFM (GPU, ensemble)
+ P3 extreme price shadow/risk classifier (spike detection).

timemixer and rt916 are not in this path.
