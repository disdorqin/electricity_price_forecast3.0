# TimeMixer Execution Lock

This file is the durable execution lock for the standalone TimeMixer enhancement track.

If context is compressed or the work is resumed in a new thread, read this file first.

## 1. Current objective

Current objective:

- improve `TimeMixer` standalone `dayahead`
- use serial keep/drop execution
- do not run a full ablation matrix
- only feed the best standalone candidate into fusion after it proves stable

## 2. Locked metric and window

- metric protocol: `docs/metrics_calculation.md`
- primary metric: floor-50 `SMAPE`
- current formal standalone comparison window: `2026-05-01 ~ 2026-05-09`
- command-side window uses:
  - `test-start = 2026-05-01`
  - `test-end-exclusive = 2026-05-10`

## 3. Current baseline

Current verified baseline artifact source:

- `TimeMixer/outputs_v2/may`

Current baseline metrics:

- overall `22.6163`
- `1_8` `34.1534`
- `9_16` `13.3001`
- `17_24` `20.3955`

Interpretation:

- current weakest area is `1_8`
- `9_16` is no longer the dominant failure
- Stage 1 should prioritize training-fix recovery before heavier architecture changes

## 4. Locked execution order

Execution order is fixed:

1. baseline lock
2. Stage 1 training-fix candidates
3. Module A hierarchical residual decoder
4. Module B spike residual branch
5. Module C adaptive frequency decomposition
6. Module D segment-aware loss

Do not reorder these unless a later result is explicitly recorded here.

## 5. Keep / drop rule

Candidate is `KEEP` if:

- overall improves materially

Candidate may also be `KEEP` if:

- overall is roughly flat
- `9_16` improves clearly
- `1_8` and `17_24` do not show material harm

Otherwise:

- mark `DROP`
- roll back to the last kept candidate
- continue with the next module family

## 6. Artifact discipline

All serial keep/drop artifacts must live under:

- `TimeMixer/outputs_v2/serial_keepdrop/`

Each candidate must include:

- `predictions_raw.csv`
- `metrics_by_period.csv`
- `run_manifest.json`
- `decision.json`

Global ledger:

- `TimeMixer/outputs_v2/serial_keepdrop/leaderboard.csv`

## 7. Current best candidate

Current best candidate:

- `module_b_spike_residual_v1`

Current best metrics:

- overall `23.7813`
- `1_8` `32.9438`
- `9_16` `13.5446`
- `17_24` `24.8553`

Recorded outcome so far:

- `baseline_v1` locked at overall `25.6568`
- `stage1_trainfix_v1` kept with overall improvement `-1.2497pp`
- this means Stage 1 has already produced a valid keep candidate
- `module_a_hier_residual_v1` was tried and dropped
- `module_a_hier_residual_v1` improved `9_16` from `14.9166` to `14.2883`
- but overall worsened from `24.4071` to `24.5876`
- and `1_8` / `17_24` both became worse, so it failed the keep rule
- `module_b_spike_residual_v1` was tried and kept
- `module_b_spike_residual_v1` improved overall from `24.4071` to `23.7813`
- it also improved `1_8` from `34.4801` to `32.9438`
- and improved `9_16` from `14.9166` to `13.5446`
- `17_24` worsened from `23.8246` to `24.8553`, but the overall gain remained strong enough to keep the module

## 8. Fusion handoff status

A small `dayahead` fusion verification has now been completed with the current best standalone candidate wired into the official fusion path.

Probe artifact:

- `fusion_runs/timemixer_candidate_probe/module_b_spike_residual_v1_dayahead`

Probe config:

- `TimeMixer/candidate_configs/module_b_spike_residual_v1.json`

Probe rule:

- keep the official fusion workflow unchanged
- only replace the TimeMixer leg with the current best standalone candidate
- compare against the verified fusion baseline on the same formal window `2026-05-01 ~ 2026-05-09`

Observed fusion result vs verified fusion baseline:

- fused overall: `21.0022 -> 20.2063`
- fused `1_8`: `31.2647 -> 30.7005`
- fused `9_16`: `10.6090 -> 10.4834`
- fused `17_24`: `21.1329 -> 19.4349`

Interpretation:

- the current best standalone TimeMixer candidate transfers positively into `dayahead` fusion
- this is not just a standalone-only gain
- the gain is broad rather than isolated, because all three business segments improved in the probe

Current fusion-facing conclusion:

- `module_b_spike_residual_v1` is now the preferred TimeMixer candidate for small-window fusion verification
- do not discard it
- before trying heavier standalone modules, it is reasonable to treat this candidate as fusion-ready for the current `dayahead` path
- the fusion pipeline default has now been updated to point to this candidate
- explicit override is still preserved through:
  - `--timemixer-candidate-config ...`
  - `--no-default-timemixer-candidate`

Default-path verification:

- artifact:
  - `fusion_runs/timemixer_default_probe/dayahead_default_no_override`
- verification goal:
  - prove that the formal fusion runner now picks `module_b_spike_residual_v1` even when no candidate flag is passed
- evidence:
  - `runtime_summary.json` records:
    - `timemixer_candidate_source = "default"`
    - `timemixer_candidate_config = TimeMixer/candidate_configs/module_b_spike_residual_v1.json`
- observed default-path metrics:
  - overall `20.3372`
  - `1_8` `30.6141`
  - `9_16` `10.5618`
  - `17_24` `19.8357`

Interpretation:

- the default fusion path is no longer only documented
- it is now verified by an explicit no-override artifact
- this closes the loop between code default, runtime manifest, and observed metrics

Full-suite default-path verification:

- artifact:
  - `fusion_runs/timemixer_default_probe/full_suite_default_no_override_9day`
- verification goal:
  - prove that the official full suite uses `module_b_spike_residual_v1` by default for both tasks when no override flag is passed
- evidence:
  - `dayahead_run/runtime_summary.json` records:
    - `timemixer_candidate_source = "default"`
    - `timemixer_candidate_config = TimeMixer/candidate_configs/module_b_spike_residual_v1.json`
  - `realtime_run/runtime_summary.json` records:
    - `timemixer_candidate_source = "default"`
    - `timemixer_candidate_config = TimeMixer/candidate_configs/module_b_spike_residual_v1.json`
- observed same-window metrics:
  - `dayahead overall 20.3372`
  - `dayahead 1_8 30.6141`
  - `dayahead 9_16 10.5618`
  - `dayahead 17_24 19.8357`
  - `realtime overall 15.6658`
  - `realtime 1_8 20.1284`
  - `realtime 9_16 6.2542`
  - `realtime 17_24 20.6149`

Interpretation:

- the default candidate is now verified in the official full-suite path, not only in standalone or one-side probe runs
- both `dayahead` and `realtime` carry the same default candidate source in their runtime summaries
- this is the strongest evidence so far that `module_b_spike_residual_v1` is the right fusion-facing TimeMixer anchor

Official suite follow-up:

- a strict same-window official fusion suite rerun has now also been completed:
  - `fusion_runs/fusion_v1_formal/may2026_timemixer_module_b_9day`
- this rerun uses the same effective formal window as the old verified baseline:
  - `2026-05-01 ~ 2026-05-09`
- it also records the candidate path in both task runtime summaries:
  - `dayahead_run/runtime_summary.json`
  - `realtime_run/runtime_summary.json`

Strict same-window official suite comparison vs old verified baseline `may2026_full`:

- `dayahead overall`: `21.0022 -> 20.2063`
- `dayahead 1_8`: `31.2647 -> 30.7005`
- `dayahead 9_16`: `10.6090 -> 10.4834`
- `dayahead 17_24`: `21.1329 -> 19.4349`
- `realtime overall`: `18.1957 -> 15.6823`
- `realtime 1_8`: `21.2901 -> 20.1648`
- `realtime 9_16`: `9.2398 -> 6.2270`
- `realtime 17_24`: `24.0571 -> 20.6550`

Interpretation of the strict rerun:

- the kept TimeMixer candidate survives the official fusion suite path, not just the standalone runner
- the same-window suite result is strongly positive for `dayahead`
- under the current shared suite path, `realtime` also improves materially rather than being harmed
- this makes `module_b_spike_residual_v1` the strongest TimeMixer candidate we have verified so far

Extended-window evidence:

- a second suite run also exists at:
  - `fusion_runs/fusion_v1_formal/may2026_timemixer_module_b`
- because project data now extends beyond mid-May, that run covers the full requested month and is not directly comparable to the old 9-day baseline
- treat it as forward evidence only, not as the primary baseline comparison artifact

Module C result:

- candidate:
  - `module_c_adaptive_freq_v1`
- artifact:
  - `TimeMixer/outputs_v2/serial_keepdrop/module_c_adaptive_freq_v1`
- decision:
  - `DROP`

Observed comparison vs current kept candidate `module_b_spike_residual_v1`:

- overall: `23.7813 -> 24.9078`
- `1_8`: `32.9438 -> 33.0044`
- `9_16`: `13.5446 -> 14.3707`
- `17_24`: `24.8553 -> 27.3483`

Interpretation:

- the first adaptive frequency decomposition attempt did not help
- it degraded the locked formal window overall
- it also harmed `9_16`
- and it materially worsened `17_24`
- therefore this specific Module C variant should not be retained
- current best candidate remains `module_b_spike_residual_v1`

Module D result:

- candidate:
  - `module_d_segment_loss_v1`
- artifact:
  - `TimeMixer/outputs_v2/serial_keepdrop/module_d_segment_loss_v1`
- decision:
  - `DROP`

Observed comparison vs current kept candidate `module_b_spike_residual_v1`:

- overall: `23.7813 -> 23.8375`
- `1_8`: `32.9438 -> 33.1993`
- `9_16`: `13.5446 -> 13.2834`
- `17_24`: `24.8553 -> 25.0299`

Interpretation:

- this segment-aware loss attempt did improve `9_16` a little
- but the overall score still became worse
- `1_8` and `17_24` also became slightly worse
- under the locked keep/drop rule, that is still a `DROP`
- current best candidate remains `module_b_spike_residual_v1`

Next preferred action:

- keep `Module B`
- skip retained use of `Module A`
- prefer consolidating `module_b_spike_residual_v1` as the default fusion-facing TimeMixer candidate before investing in heavier `Module C`
- only return to `Module C` if we design a meaningfully different low/high-frequency mechanism
- only return to `Module D` if we design a materially stronger no-harm / segment-loss scheme
- otherwise TimeMixer standalone serial exploration is currently plateaued at `Module B`

## 9. Resume order

When resuming:

1. read this file
2. inspect `TimeMixer/outputs_v2/serial_keepdrop/leaderboard.csv`
3. inspect `fusion_runs/timemixer_candidate_probe/module_b_spike_residual_v1_dayahead`
4. open the latest kept candidate directory
5. continue from the next untried module family or the next fusion validation step
