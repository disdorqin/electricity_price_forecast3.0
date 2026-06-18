# Fusion V1 Execution Lock

This file is the durable execution lock for the current `electricity_forecast_model2.0` fusion project.

If conversation context is compressed, resumed in a new thread, or handed to another agent, execution must resume from this document plus the latest artifacts it references.

## 1. Current official objective

The current official objective is:

1. reproduce the base models first
2. choose the formal training length from reproduction evidence
3. run Fusion V1 with fixed segmented weights

Do not skip Step 1.
Do not go back to ad hoc train-length guessing.

## 2. Official model pools

### Dayahead pool

- `LightGBM`
- `TimesFM`
- `TimeMixer`

### Realtime pool

- `RT916_SpikeFusionNet`
- `SGDFNet`
- `TimesFM`
- `TimeMixer`

`SGDFNet` is realtime-only in Fusion V1.

## 3. Official metric discipline

All model comparison and fusion fitting must follow:

- `docs/metrics_calculation.md`

The primary fitting and reporting metric is floor-50 `SMAPE`.

That means both `y_true` and `y_pred` are floored by `50` before SMAPE calculation.

## 4. Locked execution protocol

### Phase A. Reproduction first

The reproduction phase must:

- run monthly single-model reproductions
- compare `6 / 9 / 12 months`
- evaluate the core months:
  - `2026-02`
  - `2026-03`
  - `2026-04`

Required outputs:

- `fusion_runs/repro_training_length_probe/repro_training_length_summary.csv`
- `fusion_runs/repro_training_length_probe/repro_training_length_decision.json`

### Phase B. Fusion V1

Only after Phase A is locked:

- use the chosen train length
- split the internal training window chronologically `8:2`
- use the last `20%` validation segment to fit fixed weights
- fit weights separately by:
  - `task`
  - `1_8`
  - `9_16`
  - `17_24`

## 5. Locked learner constraints

Fusion V1 is a bounded fixed-weight learner.

Hard constraints:

- `sum(weights) = 1`
- negative weights are allowed
- lower bound = `-0.50`
- upper bound = `1.20`

Default regularization:

- `1_8 = 0.50`
- `9_16 = 0.20`
- `17_24 = 0.30`

This is the official path.
Do not silently fall back to the old positive-simplex-only learner.
Do not silently switch back to ridge meta-learner as the formal default.

## 6. Current locked train-length decision

Authoritative artifact:

- `fusion_runs/repro_training_length_probe/repro_training_length_decision.json`

Current locked decision:

- `dayahead_train_months = 12`
- `realtime_train_months = 12`
- `unified_train_months = 12`
- `use_task_specific_train_months = false`

Observed reproduction months:

- `2026-02`
- `2026-03`
- `2026-04`

Therefore, unless a future documented rerun replaces this decision, Fusion V1 should run with a unified `12-month` training length.

## 7. Official entrypoints

### Reproduction lock entry

```bash
python fusion/run_repro_training_length_suite.py \
  --work-dir fusion_runs/repro_training_length_probe \
  --conda-env epf-2 \
  --months 2026-02,2026-03,2026-04 \
  --train-months-list 6,9,12 \
  --tasks dayahead,realtime \
  --models lightgbm,timesfm,timemixer,rt916,sgdfnet \
  --skip-existing
```

### Formal fusion entry

```bash
python fusion/run_full_fusion_suite.py \
  --target-start 2026-05-01 \
  --target-end 2026-05-31 \
  --work-dir fusion_runs/fusion_v1_formal \
  --conda-env epf-2 \
  --train-length-decision fusion_runs/repro_training_length_probe/repro_training_length_decision.json \
  --weight-lower-bound -0.5 \
  --weight-upper-bound 1.2
```

## 8. Minimum required artifacts for each formal fusion run

At minimum, a valid Fusion V1 run should produce:

- `validation_predictions_long.csv`
- `weights.csv`
- `fit_report.csv`
- `formal_predictions_long.csv`
- `dayahead/fused_predictions.csv`
- `realtime/fused_predictions.csv`
- `dayahead/metrics_smape.csv`
- `realtime/metrics_smape.csv`
- `joint_report/final_truth_vs_fusion.csv`
- `suite_metrics_summary.csv`
- `suite_summary.json`

If the requested target window exceeds actual data coverage, the formal suite must shrink to the latest complete target day and record:

- `requested_target_end`
- `latest_complete_target_day`
- `latest_cross_model_safe_target_day`
- effective `target_end`

It must not silently pretend the missing tail of the month exists.
If model families disagree on the last valid target day near a truncated file boundary, the suite must use the shared cross-model safe day.

## 9. Resume rule

If work resumes after interruption:

1. read this file
2. read `fusion/README.md`
3. read `fusion_runs/repro_training_length_probe/repro_training_length_decision.json`
4. inspect the latest `fusion_runs/` artifacts
5. continue from the latest incomplete formal fusion stage

Do not restart from scratch unless the artifacts are proven invalid.

## 10. Current stage after this lock

The project is now in:

- `Phase A complete`
- `Phase B active`

That means:

- reproduction is locked
- training length is locked
- the next official action is to run and verify Fusion V1 outputs

## 11. Verified formal Fusion V1 status

The current verified formal Fusion V1 artifact is:

- `fusion_runs/fusion_v1_formal/may2026_full`

This formal run was requested for:

- `target_start = 2026-05-01`
- `requested_target_end = 2026-05-31`

But the currently available project data ends near the middle of May.
Therefore the suite correctly shrank the formal window to the shared cross-model safe range:

- `latest_complete_target_day = 2026-05-11`
- `latest_cross_model_safe_target_day = 2026-05-09`
- effective `target_end = 2026-05-09`

This shrink is intentional and correct.
It is not a failed run.
It is the required behavior when one or more model families cannot safely form labels or features at the truncated file boundary.

The verified formal metrics for the effective window `2026-05-01 ~ 2026-05-09` are:

- `dayahead overall SMAPE = 21.0022`
- `dayahead 9_16 SMAPE = 10.6090`
- `realtime overall SMAPE = 18.1957`
- `realtime 9_16 SMAPE = 9.2398`

The artifact completeness check passed for:

- `dayahead_run/validation_predictions_long.csv`
- `dayahead_run/weights.csv`
- `dayahead_run/fit_report.csv`
- `dayahead_run/formal_predictions_long.csv`
- `dayahead_run/dayahead/fused_predictions.csv`
- `dayahead_run/dayahead/metrics_smape.csv`
- `realtime_run/validation_predictions_long.csv`
- `realtime_run/weights.csv`
- `realtime_run/fit_report.csv`
- `realtime_run/formal_predictions_long.csv`
- `realtime_run/realtime/fused_predictions.csv`
- `realtime_run/realtime/metrics_smape.csv`
- `joint_report/final_truth_vs_fusion.csv`
- `joint_report/metrics_arbitrage.csv`
- `suite_metrics_summary.csv`
- `suite_summary.json`
