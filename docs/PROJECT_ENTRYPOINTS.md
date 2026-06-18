# Project Entrypoints

This file is the clean entrypoint map for the current `electricity_forecast_model2.0` worktree.

If you only need to know:

- what the official scripts are
- which path is the current default
- which files are execution locks rather than old notes

read this file first.

## 1. Current status at a glance

The repository currently has two active control tracks:

1. `Fusion V1`
2. `TimeMixer standalone enhancement`

Both are already past the free-exploration stage.
Do not restart from scattered historical scripts unless a document below explicitly tells you to.

## 2. Read order

### If your task is fusion

Read in this order:

1. `docs/FUSION_V1_STATUS.md`
2. `docs/FUSION_V1_EXECUTION_LOCK.md`
3. `fusion/README.md`
4. `fusion_runs/repro_training_length_probe/repro_training_length_decision.json`

### If your task is TimeMixer enhancement

Read in this order:

1. `docs/TIMEMIXER_CURRENT_DECISION.md`
2. `docs/TIMEMIXER_EXECUTION_LOCK.md`
3. the TimeMixer enhancement planning document under `docs/`

## 3. Official fusion entrypoints

### Training-length reproduction

Official script:

- `fusion/run_repro_training_length_suite.py`

Use this when you need to:

- reproduce single-model windows
- compare `6 / 9 / 12 months`
- regenerate the formal train-length decision

Primary artifact:

- `fusion_runs/repro_training_length_probe/repro_training_length_decision.json`

### Formal Fusion V1 suite

Official script:

- `fusion/run_full_fusion_suite.py`

Use this when you need to:

- run the official `dayahead` / `realtime` fusion suite
- compare against the current formal May artifact
- validate a new adapter or a new safe target window

Important default:

- the fusion path now defaults to:
  - `TimeMixer/candidate_configs/module_b_spike_residual_v1.json`

Disable that default only if you intentionally want the stock enhanced TimeMixer:

- `--no-default-timemixer-candidate`

### Lower-level fusion scripts

These are still useful, but they are not the first resume entry:

- `fusion/run_pipeline.py`
- `fusion/run_dayahead_pipeline.py`
- `fusion/run_realtime_pipeline.py`
- `fusion/run_fit.py`

Treat them as implementation-layer tools, not the main control entry.

## 4. Official TimeMixer entrypoints

### Current best-candidate status

Frozen best candidate:

- `TimeMixer/candidate_configs/module_b_spike_residual_v1.json`

Current decision:

- keep it
- treat it as the active fusion-facing default
- do not continue serial keep/drop experimentation unless a genuinely different mechanism is proposed

### Standalone enhancement runner

The active enhanced export path used by fusion is:

- `fusion/runners/run_timemixer_enhanced_export.py`

The core model-side implementation files are:

- `TimeMixer/enhanced_model.py`
- `TimeMixer/enhanced_config.py`
- `TimeMixer/enhanced_pipeline.py`
- `TimeMixer/enhanced_loss.py`

### Historical / lower-level TimeMixer files

These exist and are still part of the repo, but they are not the primary resume anchor:

- `TimeMixer/pipeline_timemixer.py`
- `TimeMixer/pipeline_timemixer_single_task.py`

Use them only when you explicitly need the underlying standalone pipeline behavior.

## 5. Artifact anchors

### TimeMixer standalone ledger

- `TimeMixer/outputs_v2/serial_keepdrop/leaderboard.csv`

### Frozen TimeMixer standalone candidate artifact

- `TimeMixer/outputs_v2/serial_keepdrop/module_b_spike_residual_v1/`

### Verified formal fusion baseline

- `fusion_runs/fusion_v1_formal/may2026_full`

### Verified formal same-window TimeMixer-enhanced suite

- `fusion_runs/fusion_v1_formal/may2026_timemixer_module_b_9day`

### Verified default-path probe

- `fusion_runs/timemixer_default_probe/full_suite_default_no_override_9day`

## 6. What not to use as the first entry

Do not start from these unless your task is specifically low-level debugging:

- old terminal memory
- random files under `fusion_runs/` without checking the status docs
- standalone ad hoc scripts that bypass the documented lock order
- root `README.md` as the only source of truth

Reason:

- the root README is useful as a general project description, but it is not the best current execution control surface
- the lock files above already contain the verified protocol and latest accepted state

## 7. Current practical recommendation

If resuming work right now:

1. treat `module_b_spike_residual_v1` as frozen
2. treat Fusion V1 as the active system-level path
3. continue with fusion validation, integration cleanup, or a genuinely new mechanism
4. do not reopen weak `Module C/D` style TimeMixer retries without new evidence
