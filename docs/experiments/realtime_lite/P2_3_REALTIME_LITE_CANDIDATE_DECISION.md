# P2.3 Realtime Lite Candidate Decision

## Why SGDFNet KEEP

| Factor | Value |
|--------|------:|
| Overall sMAPE_floor50 | 20.20 |
| Baseline (DA anchor) | 26.95 |
| Improvement | -6.75pp (-25.0%) |
| Completed days | 363/363 (100%) |
| Avg runtime | 40s/day |
| Backend | CPU-only |
| GPU dependency | None |
| Failures | 0 |

SGDFNet is the only P2.2 model that:
- Completed 100% of its 363-day backfill.
- Runs on CPU with stable 40s/day.
- Beat DA anchor on all 10 spring/summer test windows.
- Has zero GPU dependency.
- Has zero runtime failures.

## Why TimesFM KEEP_CANDIDATE

| Factor | Value |
|--------|------:|
| Smoke test months | 2025-03, 2025-09, 2026-05 |
| Completed days | 91/92 (98.9%) |
| Avg runtime | 11.9s/day |
| GPU required | Yes (~1.3 GB) |
| Scheduling fix needed | Yes (heavy slot contention) |

TimesFM is fast (11.9s/day) and GPU-stable when given a dedicated slot.
It beat DA anchor on all 3 smoke-test months. It is kept as a candidate
ensemble partner pending wider testing and scheduling resolution.

## Why TimeMixer CACHE_ONLY

| Factor | Value |
|--------|------:|
| Completed days | 35/363 (10%) |
| Avg runtime | 295s/day (~5 min) |
| GPU required | Yes (heavy slot) |
| Full backfill projection | ~29.5h |

TimeMixer is too slow for daily online production. Its ~5 min/day GPU
inference is acceptable for cached/periodic refreshes but not for the
critical realtime path. Marked CACHE_ONLY — offline research baseline.

## Why RT916 NOT_PRODUCTION_READY

| Factor | Value |
|--------|------:|
| Completed days | 19/363 (5%) |
| Avg runtime | 1840s/day (~31 min) |
| GPU required | Yes |
| GPU stability | Crashes observed |
| Full backfill projection | ~185.5h (7.8 GPU-days) |

RT916's runtime is prohibitive for production. Its daily model-fitting
architecture (`run_joint_da_rt_daily_backtest`) is not suitable for
realtime-only online inference. GPU crashes compound the issue.
Recommended replacement: P3 extreme price shadow/risk classifier.

## Why 2.5 Four-Model Fusion NOT VERIFIED

The central P2.2 question — whether the 2.5 realtime four-model GEF
fusion improves over DA anchor on calm/spring-summer windows — could
not be answered. Only sgdfnet completed sufficient days. timemixer (35),
rt916 (19), and timesfm (1 at the time) all lacked the trailing-30d
multi-model history required for the GEF weight learner. All 6 fusion
and blend variants were skipped.

## Why This Candidate is Not Shadow / Not Champion

- **No 3.0 shadow comparison performed.** The candidate has not been
  run alongside the official 3.0 realtime pipeline.
- **No production adapter integration.** The candidate registry entry
  documents findings but provides no runtime adapter.
- **No final_outputs modification.** The candidate does not write to
  `submission_ready.csv` or any champion output path.
- **Registry-only entry.** This is a documented recommendation, not a
  deployment.
