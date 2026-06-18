# Fusion

This folder contains the formal fusion workflow for `electricity_forecast_model2.0`.

## Resume first

Before using any script here, read:

- `docs/PROJECT_ENTRYPOINTS.md`
- `docs/FUSION_V1_STATUS.md`
- `docs/FUSION_V1_EXECUTION_LOCK.md`
- `fusion/docs/OFFICIAL_SCRIPTS.md`

## Current V1 Protocol

The current official sequence is:

1. reproduce single-model results first
2. choose formal training length from reproduction evidence
3. run fusion with fixed segmented weights

The durable execution lock is stored at:

- `docs/FUSION_V1_EXECUTION_LOCK.md`
- `docs/FUSION_V1_STATUS.md`

If context is compressed or work resumes later, read that file first.

The official V1 learner is:

- fixed weights
- learned separately for `dayahead` and `realtime`
- learned separately for `1_8`, `9_16`, and `17_24`
- fit on validation-period prediction tables
- weights may be negative
- weights must sum to `1`
- each weight is bounded by lower and upper limits

This V1 does not use the ridge meta-learner as the default formal path.

## Standard Prediction Table

Every model should eventually be converted to the following long-format table:

| column | meaning |
| --- | --- |
| `task` | `dayahead` or `realtime` |
| `model_name` | stable model identifier |
| `target_day` | forecasted business day, `YYYY-MM-DD` |
| `ds` | timestamp of the predicted point |
| `period` | `1_8`, `9_16`, or `17_24` |
| `hour_business` | business hour `1..24` |
| `y_true` | ground truth |
| `y_pred` | model prediction |

## Official Model Pools

- `dayahead`: `LightGBM`, `TimesFM`, `TimeMixer`
- `realtime`: `RT916_SpikeFusionNet`, `SGDFNet`, `TimesFM`, `TimeMixer`

`SGDFNet` is formal realtime-only in fusion V1.

`TimeMixer` currently defaults to:

- `TimeMixer/candidate_configs/module_b_spike_residual_v1.json`

That default is already verified in both the standalone fusion probe and the full-suite no-override probe.

## Official Workflow

### Step 1. Reproduction-length selection

Run:

```bash
python fusion/run_repro_training_length_suite.py --work-dir fusion_runs/repro_training_length
```

This writes:

- `repro_training_length_summary.csv`
- `repro_training_length_decision.json`

### Step 2. Formal fusion

Run:

```bash
python fusion/run_full_fusion_suite.py \
  --task all \
  --target-start 2026-05-01 \
  --target-end 2026-05-31 \
  --train-length-decision fusion_runs/repro_training_length/repro_training_length_decision.json \
  --weight-lower-bound -0.5 \
  --weight-upper-bound 1.2
```

The formal fusion path now defaults to the current preferred TimeMixer candidate:

- `TimeMixer/candidate_configs/module_b_spike_residual_v1.json`

If you want to override that default explicitly, add:

```bash
  --timemixer-candidate-config TimeMixer/candidate_configs/module_b_spike_residual_v1.json
```

If you want to disable the preferred candidate and fall back to the stock enhanced TimeMixer config, add:

```bash
  --no-default-timemixer-candidate
```

This keeps the fusion learner unchanged and only swaps the TimeMixer leg selection.

Verified example artifacts:

- strict same-window suite comparison:
  - `fusion_runs/fusion_v1_formal/may2026_timemixer_module_b_9day`
- extended-window forward run:
  - `fusion_runs/fusion_v1_formal/may2026_timemixer_module_b`

This formal path:

1. runs the base models
2. builds validation-period long tables
3. learns fixed segmented weights
4. applies them to the formal prediction window
5. writes fused outputs and suite summaries

## Key Files

- `run_repro_training_length_suite.py`
  - official reproduction-length selection entry
- `run_full_fusion_suite.py`
  - official formal fusion entry
- `weights.py`
  - bounded fixed-weight learner
- `pipeline_common.py`
  - shared orchestration for model runs and fusion outputs
- `adapters/`
  - model-specific converters into the standard long table

For scripts that are still useful but should not be treated as the first control entry, see:

- `fusion/docs/NON_PRIMARY_SCRIPTS.md`

## Weight Constraints

Current defaults:

- lower bound: `-0.50`
- upper bound: `1.20`
- sum of weights: `1`

Regularization defaults:

- `1_8`: `0.50`
- `9_16`: `0.20`
- `17_24`: `0.30`

## Output Expectations

Formal runs should produce at least:

- `validation_predictions_long.csv`
- `formal_predictions_long.csv`
- `weights.csv`
- `fit_report.csv`
- `fused_predictions.csv`
- `metrics_summary.csv`
- `runtime_summary.json`

Full-suite runs should additionally produce:

- `suite_metrics_summary.csv`
- `suite_summary.json`
- `joint_report/final_truth_vs_fusion.csv`

## Current verified formal run

The current verified formal Fusion V1 suite is:

- `fusion_runs/fusion_v1_formal/may2026_full`

Important coverage note:

- the run was requested through `2026-05-31`
- the available project data is truncated near `2026-05-12`
- the suite therefore shrank the effective formal fusion window to the shared cross-model safe boundary
- current effective formal window: `2026-05-01 ~ 2026-05-09`

This is the correct behavior.
Do not manually force later May days into the formal fusion window unless the underlying model adapters all prove they can safely generate those days.

Resume priority for future work:

1. `docs/FUSION_V1_STATUS.md`
2. `docs/FUSION_V1_EXECUTION_LOCK.md`
3. the latest `fusion_runs/` artifact referenced there
