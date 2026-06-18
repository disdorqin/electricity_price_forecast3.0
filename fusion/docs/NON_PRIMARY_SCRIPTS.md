# Non-Primary Scripts

This note lists scripts that are valid and sometimes useful, but should not be
treated as the first control entry for the current repository state.

Read this after:

- `START_HERE.md`
- `docs/PROJECT_ENTRYPOINTS.md`
- `fusion/docs/OFFICIAL_SCRIPTS.md`

## Why this file exists

The repository has accumulated both:

- formal orchestration entrypoints
- lower-level utilities and historical helper scripts

That is normal, but it makes it easy to restart from the wrong layer.
This file reduces that risk.

## 1. Fusion lower-level scripts

These are real scripts, but they are not the first resume entry:

- `fusion/run_pipeline.py`
- `fusion/run_dayahead_pipeline.py`
- `fusion/run_realtime_pipeline.py`
- `fusion/run_fit.py`
- `fusion/run_fixed_window_fusion.py`
- `fusion/run_end_to_end_fixed_fusion.py`
- `fusion/run_final_fusion_pipeline.py`
- `fusion/run_rolling_backtest.py`

Use them when:

- you are debugging internals
- you want a partial pipeline stage
- you are intentionally bypassing the full formal suite

Do not use them when:

- you need the current official Fusion V1 comparison path
- you need the formal suite summary artifacts
- you want the documented default TimeMixer candidate behavior without extra judgment

## 2. TimeMixer lower-level scripts

These are also valid, but not the main control entry:

- `TimeMixer/pipeline_timemixer.py`
- `TimeMixer/pipeline_timemixer_single_task.py`

Use them when:

- you are working directly on standalone TimeMixer internals
- you need to debug the underlying pipeline behavior

Do not use them as the first entry when:

- your real target is fusion behavior
- you only need the currently accepted TimeMixer candidate path
- you are resuming after context compression and need the shortest reliable path

## 3. Practical rule

If you are unsure where to begin:

1. use `fusion/run_repro_training_length_suite.py` for train-length reproduction
2. use `fusion/run_full_fusion_suite.py` for the formal suite
3. use `fusion/runners/run_timemixer_enhanced_export.py` for the active fusion-facing TimeMixer bridge

Only drop below that layer if your task explicitly requires it.
