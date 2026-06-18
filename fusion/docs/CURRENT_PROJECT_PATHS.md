# Current Project Paths

This note records the real project-adjacent locations that were discovered around this repository.

## Discovered data

- Main hourly dataset (xlsx):
  - `D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\epf\data\shandong_pmos_hourly.xlsx`
- Main hourly dataset (csv):
  - `D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\epf\data\shandong_pmos_hourly.csv`

## Discovered external model assets

- External model root:
  - `D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\models`
- Existing LightGBM realtime model:
  - `D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\models\LightGBM\best_model_实时电价.pkl`

## Expected model output roots in this unified repo

- LightGBM:
  - `lightGBM/outputs/`
- TimesFM:
  - `TimesFM/output/`
- TimeMixer:
  - `TimeMixer/outputs/`
- SGDFNet:
  - `SGDFNet/outputs/`
- RT916:
  - `outputs/RT916_SpikeMarketLab/model_packages/RT916_SpikeFusionNet/`

## Practical note

Not every expected output file exists yet. The fusion pipeline is ready to consume them after the corresponding model scripts produce CSV outputs.
