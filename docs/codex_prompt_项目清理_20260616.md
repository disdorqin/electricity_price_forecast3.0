# Codex 执行提示词：项目清理与补全

## GOAL

按 `docs/项目清理与补全计划_20260616v2.md` 执行项目清理，让项目根目录只剩核心结构。

## 约束

- 用 `_archive/` 归档，**不删除**任何文件（用 `mv` 或 PowerShell `SendToRecycleBin`）
- Windows 环境，`mv` 可能需要处理中文路径
- LightGBM / TimesFM 内部不动，只验证 pipeline.py 接口
- RT916 可以改内部结构（创建根目录薄包装）
- Conda 环境：`epf-2`，Python 路径 `D:\computer_download\environment\conda\epf-2\python.exe`
- 项目根目录：`D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0`

## 执行步骤

### 阶段 1：根目录清理

把以下文件/目录移入 `_archive/`：

| 来源 | 目标 |
|------|------|
| `START_HERE.md` | `_archive/root_cleanup/START_HERE.md` |
| `MEMORY.md` | `docs/MEMORY.md`（移入 docs，不是归档） |
| `outputs/` | `_archive/outputs_root/` |
| `output/` | `_archive/output_root/`（注意和 outputs 是两个不同目录） |
| `TF/` | `_archive/TF/` |
| `scripts/` | `_archive/scripts/` |
| `run_monthly_repro_suite.py` | `_archive/root_cleanup/` |
| `compute_feb_benchmarks.py` | `_archive/root_cleanup/` |
| `compute_monthly_historical_benchmarks.py` | `_archive/root_cleanup/` |
| `run_feb_single_model_audit.py` | `_archive/root_cleanup/` |
| `lgbm_predict_diag.log` | `_archive/root_cleanup/` |
| `tmp_rt916_realtime_smoke.csv` | `_archive/root_cleanup/` |

清理后根目录应只剩：main.py, README.md, requirements.txt, LICENSE, .gitignore, cli/, pipelines/, runners/, services/, utils/, configs/, data/, docs/, fusion/, fusion_runs/, models/, TimeMixer/, SGDFNet/, RT916_SpikeFusionNet/, lightGBM/, TimesFM/, ExtremPriceClf/, _archive/

### 阶段 2：各模型内部清理

**TimeMixer/**：
- 移入 `_archive/timemixer_cleanup/`：`outputs/`、`outputs_v2/`、`candidate_configs/`、`enhanced_pipeline.py`、`enhanced_model.py`、`enhanced_config.py`、`enhanced_loss.py`
- 保留不动：`dataprocess.py`、`model.py`、`pipeline.py`、`backbones.py`、`repro_pipeline.py`、`pipeline_timemixer.py`、`__init__.py`

**SGDFNet/**：
- 移入 `_archive/sgdfnet_cleanup/`：42 个废弃 config（只留 `cutoff_recovery` 和 `production` 两个 config）、`outputs/`、`research_control/`、`docs/PACKAGING_CHANGELOG.md`
- 保留不动：`src/sgdfnet/`、`scripts/run_protocol_b_cutoff.py`、2 个核心 config、`README.md`、`dataprocess.py`、`model.py`、`pipeline.py`

**RT916_SpikeFusionNet/**：
- 移入 `_archive/rt916_cleanup/`：`FINAL_PACKAGING_SUMMARY.md`、`README_RT916.md`、`train.py`（硬编码日期的开发脚本）
- 保留不动：`pipeline.py`、`run.py`、`src/rt916_spikefusionnet/`、`configs/`、`README.md`、`docs/`

**lightGBM/**：
- 移入 `_archive/lightgbm_cleanup/`：`outputs/`（旧预测输出）、`lightGBM_oneday.py`（standalone 开发脚本）
- 保留不动：`pipeline.py`、`main_fix.py`、`train_fix.py`、`train_da_fix.py`、`infer_fix.py`、`infer_da_fix.py`、`__init__.py`、`requirements.txt`

**TimesFM/**：
- 移入 `_archive/timesfm_cleanup/`：`output/`（旧预测输出）、`price_forecast_copy_分时段预测.py`
- 保留不动：`pipeline.py`、`infer.py`、`src/timesfm/`、`__init__.py`、`pyproject.toml`、`requirements.txt`、`README.md`

### 阶段 3：RT916 根目录薄包装

在 RT916_SpikeFusionNet/ 根目录创建：

```python
# RT916_SpikeFusionNet/dataprocess.py
"""RT916 data processing - re-exports from internal package."""
from src.rt916_spikefusionnet.dataprocess import *
```

```python
# RT916_SpikeFusionNet/model.py
"""RT916 model definitions - re-exports from internal package."""
from src.rt916_spikefusionnet.model import *
from src.rt916_spikefusionnet.annual_model import *
from src.rt916_spikefusionnet.annual_model_da_timemixer import *
```

（先检查 src/rt916_spikefusionnet/ 下实际有哪些模块，确保 import 路径正确）

### 阶段 4：验证 pipeline.py 接口

检查 `lightGBM/pipeline.py` 和 `TimesFM/pipeline.py` 是否暴露了 `ModelPipeline` 类，且有 `train()`、`predict()`、`predict_range()` 方法。如果缺少，补上。不需要改模型内部逻辑。

### 阶段 5：端到端烟测

```bash
cd "D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0"
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline predict --target both --models all --date 2026-05-01
```

确认产出 DA 和 RT 预测 CSV。如果报错，修复到能跑通为止。

## 注意事项

- 每移一个文件前先确认它存在，不存在就跳过
- `_archive/` 下按来源建子目录，保持可追溯
- `__pycache__/` 可以安全删除（不是归档）
- 如果某个要归档的文件/目录不存在，跳过并记录
- 移动操作优先用 `mv`（Git Bash），失败再用 PowerShell

## 交付

1. 清理后的根目录 `ls` 截图
2. 各模型目录的 `ls` 截图
3. RT916 薄包装文件内容
4. 烟测运行结果（成功/失败 + 错误信息）
5. 归档清单（记录哪些文件移到了哪里）
