# Fusion V1 Current Status

This file is the compact resume card for the current Fusion V1 execution state.

If work resumes in a new thread, after context compression, or by another agent, read this file first.

## 1. Current official state

- project: `electricity_forecast_model2.0`
- stage: `Fusion V1 formal run verified`
- protocol source of truth: `docs/FUSION_V1_EXECUTION_LOCK.md`
- current status: `reproduction complete, train length locked, formal fusion completed and verified`

## 2. Locked protocol

The official execution order is fixed:

1. reproduce single-model results first
2. choose formal train length from reproduction evidence
3. run Fusion V1 with fixed segmented weights

Do not skip reproduction.
Do not guess the train length manually.
Do not switch the formal learner back to the old positive-simplex-only path.

## 3. Locked model pools

### Dayahead

- `LightGBM`
- `TimesFM`
- `TimeMixer`

### Realtime

- `RT916_SpikeFusionNet`
- `SGDFNet`
- `TimesFM`
- `TimeMixer`

## 4. Locked metric discipline

The unified metric contract is:

- `docs/metrics_calculation.md`

Primary reporting and fitting metric:

- floor-50 `SMAPE`

That means both prediction and truth are floored to `50` before SMAPE scoring.

## 5. Locked training-length decision

Authoritative artifact:

- `fusion_runs/repro_training_length_probe/repro_training_length_decision.json`

Current locked result:

- `dayahead_train_months = 12`
- `realtime_train_months = 12`
- `unified_train_months = 12`
- `use_task_specific_train_months = false`

Observed reproduction months:

- `2026-02`
- `2026-03`
- `2026-04`

## 6. Verified formal artifact

The current verified formal Fusion V1 run is:

- `fusion_runs/fusion_v1_formal/may2026_full`

Important coverage fact:

- requested formal window: `2026-05-01 ~ 2026-05-31`
- current data file ends near `2026-05-12`
- latest complete target day: `2026-05-11`
- latest shared cross-model safe target day: `2026-05-09`
- effective verified formal window: `2026-05-01 ~ 2026-05-09`

This shrink is correct and intentional.
Do not treat it as a failure.
Do not force later May dates into the formal fusion window unless every model adapter proves those dates are safe.

## 7. Verified formal metrics

For the effective formal window `2026-05-01 ~ 2026-05-09`:

- `dayahead overall SMAPE = 21.0022`
- `dayahead 1_8 SMAPE = 31.2647`
- `dayahead 9_16 SMAPE = 10.6090`
- `dayahead 17_24 SMAPE = 21.1329`
- `realtime overall SMAPE = 18.1957`
- `realtime 1_8 SMAPE = 21.2901`
- `realtime 9_16 SMAPE = 9.2398`
- `realtime 17_24 SMAPE = 24.0571`

Metric source:

- `fusion_runs/fusion_v1_formal/may2026_full/suite_metrics_summary.csv`

## 8. Required artifact checklist

The verified formal run already contains:

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
- `joint_report/joined_for_arbitrage.csv`
- `joint_report/metrics_arbitrage.csv`
- `suite_summary.json`
- `suite_metrics_summary.csv`

## 9. Current next-action rule

If we continue from here, the next action must be one of:

1. run a new formal month or a longer target window only after confirming cross-model safe coverage
2. compare a new fusion learner or adapter change against this verified formal artifact
3. extend data coverage first, then rerun the formal suite

## 9A. TimeMixer candidate follow-up

A TimeMixer-enhanced follow-up has now also been verified through the official suite path.

Strict same-window comparison artifact:

- `fusion_runs/fusion_v1_formal/may2026_timemixer_module_b_9day`

This artifact keeps the same effective formal window as `may2026_full`:

- `2026-05-01 ~ 2026-05-09`

And explicitly records:

- `timemixer_candidate_config = TimeMixer/candidate_configs/module_b_spike_residual_v1.json`

Observed same-window suite deltas vs `may2026_full`:

- `dayahead overall`: `21.0022 -> 20.2063`
- `dayahead 9_16`: `10.6090 -> 10.4834`
- `realtime overall`: `18.1957 -> 15.6823`
- `realtime 9_16`: `9.2398 -> 6.2270`

Interpretation:

- the current kept TimeMixer candidate is no longer just a standalone win
- it transfers positively through the official fusion suite
- it is now the preferred fusion-facing TimeMixer candidate unless a later documented run beats it
- the fusion pipeline default now points to this preferred candidate
- fallback to the stock enhanced TimeMixer path remains available through `--no-default-timemixer-candidate`

Default-path verification artifact:

- `fusion_runs/timemixer_default_probe/dayahead_default_no_override`

This run intentionally omitted `--timemixer-candidate-config` and still recorded:

- `timemixer_candidate_source = default`
- `timemixer_candidate_config = TimeMixer/candidate_configs/module_b_spike_residual_v1.json`

So the current preferred TimeMixer candidate is now verified as a real runtime default, not just a code-level intention.

Full-suite default-path verification artifact:

- `fusion_runs/timemixer_default_probe/full_suite_default_no_override_9day`

This run also intentionally omitted `--timemixer-candidate-config` and records for both tasks:

- `dayahead_run/runtime_summary.json`
  - `timemixer_candidate_source = default`
  - `timemixer_candidate_config = TimeMixer/candidate_configs/module_b_spike_residual_v1.json`
- `realtime_run/runtime_summary.json`
  - `timemixer_candidate_source = default`
  - `timemixer_candidate_config = TimeMixer/candidate_configs/module_b_spike_residual_v1.json`

Observed suite metrics for the same 9-day window:

- `dayahead overall = 20.3372`
- `dayahead 1_8 = 30.6141`
- `dayahead 9_16 = 10.5618`
- `dayahead 17_24 = 19.8357`
- `realtime overall = 15.6658`
- `realtime 1_8 = 20.1284`
- `realtime 9_16 = 6.2542`
- `realtime 17_24 = 20.6149`

Interpretation:

- the preferred TimeMixer candidate is now verified as the default in the official two-task suite path
- this closes the last gap between default wiring, runtime manifests, and observable suite metrics
- unless a later documented run beats it, `module_b_spike_residual_v1` should be treated as the active TimeMixer default for Fusion V1

There is also an extended-window artifact:

- `fusion_runs/fusion_v1_formal/may2026_timemixer_module_b`

Because data coverage has since expanded, that run covers full May and should be treated as forward evidence rather than a direct replacement for `may2026_full`.

The next action must not be:

- restarting train-length selection from scratch
- silently changing the metric formula
- silently switching learner constraints
- manually stretching the May formal window past `2026-05-09`

## 10. Read order for future resume

When resuming, use this exact order:

1. `docs/REPRO_FIX_STATUS.md` (latest code changes and pending reruns from 2026-06-13)
2. `docs/FUSION_V1_STATUS.md` (this file)
2. `docs/FUSION_V1_EXECUTION_LOCK.md`
3. `fusion/README.md`
4. `fusion_runs/repro_training_length_probe/repro_training_length_decision.json`
5. `fusion_runs/fusion_v1_formal/may2026_full/suite_summary.json`
6. `fusion_runs/fusion_v1_formal/may2026_full/suite_metrics_summary.csv`

That should be enough to restart work without depending on old chat context.
