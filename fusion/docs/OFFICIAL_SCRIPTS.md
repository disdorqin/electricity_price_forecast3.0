# Official Scripts

This note lists the scripts that should be treated as the current formal
entrypoints inside `electricity_forecast_model2.0`.

Use this together with:

- `START_HERE.md`
- `docs/PROJECT_ENTRYPOINTS.md`
- `docs/FUSION_V1_STATUS.md`
- `docs/TIMEMIXER_CURRENT_DECISION.md`
- `fusion/docs/NON_PRIMARY_SCRIPTS.md`

## 1. Fusion V1

### Formal full-suite entry

- `fusion/run_full_fusion_suite.py`

Purpose:

- run formal `dayahead` and `realtime` fusion together
- shrink target coverage to the latest cross-model safe boundary
- write suite summaries and joint reports

### Formal training-length reproduction entry

- `fusion/run_repro_training_length_suite.py`

Purpose:

- reproduce single-model monthly windows
- compare candidate train lengths
- write the formal train-length decision artifact

## 2. TimeMixer

### Active fusion-facing TimeMixer export runner

- `fusion/runners/run_timemixer_enhanced_export.py`

Purpose:

- call `TimeMixer/enhanced_pipeline.py`
- export TimeMixer outputs into the fusion-compatible layout
- carry the current preferred candidate path into fusion

### Current preferred candidate config

- `TimeMixer/candidate_configs/module_b_spike_residual_v1.json`

## 3. Lower-level scripts

These still matter, but they are not the first resume or control entry:

- `fusion/run_pipeline.py`
- `fusion/run_dayahead_pipeline.py`
- `fusion/run_realtime_pipeline.py`
- `fusion/run_fit.py`
- `TimeMixer/pipeline_timemixer.py`
- `TimeMixer/pipeline_timemixer_single_task.py`

Use them when you are debugging internals or intentionally working below the
formal orchestration layer.

If you are unsure whether a script is official or merely useful, default to the
entrypoints listed in this file and treat the non-primary list as opt-in only.
