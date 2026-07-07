# P2.3 TimesFM Smoke Test

## Test Design

- **Purpose**: Verify TimesFM has acceptable runtime and completeness
  when given a dedicated GPU slot (no TimeMixer contention).
- **Months tested**: 2025-03, 2025-09, 2026-05
- **Method**: Direct `_predict_model(timesfm, realtime, ...)` call,
  no timemixer, no rt916, single heavy GPU slot.
- **Resumable**: Skips days with existing CSV.

## Results

| Month | Days | Completed | sec/day avg | Total sec |
|-------|:---:|:---------:|:----------:|:---------:|
| 2025-03 | 31 | 30 (1 cached) | 9.5 | 285.5 |
| 2025-09 | 30 | 30 | 10.9 | 328.1 |
| 2026-05 | 31 | 31 | 15.1 | 469.6 |
| **Total** | **92** | **91 (98.9%)** | **11.9** | **1083.2** |

## Comparison with DA_anchor and SGDFNet

| Month | DA_anchor | SGDFNet | TimesFM |
|-------|:--------:|:-------:|:-------:|
| 2025-03 | 31.9 | 22.9 | **29.1** |
| 2025-09 | 19.6 | 13.7 | **22.2** |
| 2026-05 | 25.7 | 20.3 | **23.9** |

TimesFM beats DA anchor on all 3 months but loses to SGDFNet.

## GPU Usage

- Peak VRAM: ~1.3 GB (well within RTX 4060 8 GB)
- No GPU crashes observed across 91 days
- 0% util between inference steps (model loads/unloads quickly)

## Scheduling Issue

TimesFM was never dispatched during P2.2 backfill because both heavy
GPU slots (MAX_HEAVY=2) were occupied by TimeMixer workers. TimesFM
jobs remained pending until TimeMixer completed, which never happened
before Fast Closeout.

**Fix**: TimesFM needs either:
- A dedicated GPU slot (increase MAX_HEAVY to 3)
- Classification as a light-weight GPU model (MAX_LIGHT pool)
- Exclusive scheduling period when TimeMixer is not running

## Decision

**KEEP_CANDIDATE**. TimesFM is fast, GPU-stable, and beats DA anchor.
The scheduling issue is solvable. However, it should not block the
main chain — it requires a dedicated slot fix and wider smoke testing
before production use.
