# Fusion Runbook

This runbook focuses on getting model outputs into the fusion pipeline with the least manual work.

## 1. Generate raw model outputs

### LightGBM

Realtime example:

```bash
python fusion/runners/run_lightgbm_export.py --task realtime --forecast-start 2026-02-01 --forecast-end 2026-02-03
```

Day-ahead example:

```bash
python fusion/runners/run_lightgbm_export.py --task dayahead --forecast-start 2026-02-01 --forecast-end 2026-02-03
```

Default outputs:

- `lightGBM/outputs/lightgbm_realtime.csv`
- `lightGBM/outputs/lightgbm_dayahead.csv`

### TimesFM

Realtime example:

```bash
python fusion/runners/run_timesfm_export.py --task realtime --start-date 2026-02-01 --end-date 2026-02-03
```

Day-ahead example:

```bash
python fusion/runners/run_timesfm_export.py --task dayahead --start-date 2026-02-01 --end-date 2026-02-03
```

Default outputs:

- `TimesFM/output/forecast_realtime.csv`
- `TimesFM/output/forecast_dayahead.csv`

## 2. Prepare manifest

Generate default manifests:

```bash
python fusion/prepare_manifest.py --task realtime --output fusion_runs/realtime_manifest.csv
python fusion/prepare_manifest.py --task dayahead --output fusion_runs/dayahead_manifest.csv
```

Then edit the generated CSV paths if a model wrote to a different location or filename.

## 3. Run fusion pipeline

```bash
python fusion/run_pipeline.py --manifest fusion_runs/realtime_manifest.csv --work-dir fusion_runs/realtime
python fusion/run_pipeline.py --manifest fusion_runs/dayahead_manifest.csv --work-dir fusion_runs/dayahead
```

## 4. Outputs

Each `work-dir` will contain:

- `normalized_predictions.csv`
- `weights/weights.csv`
- `weights/fit_report.csv`
