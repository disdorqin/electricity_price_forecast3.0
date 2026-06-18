# Codex 执行提示词：ExtremPriceClf 集成 + 双模式流水线

## GOAL

按 `docs/ExtremPriceClf集成与双模式流水线_20260617.md` 实现融合链路 + ExtremPriceClf 分类器的双模式集成。

## 约束

- Windows 环境，中文路径
- Conda 环境：`epf-2`，Python：`D:\computer_download\environment\conda\epf-2\python.exe`
- 项目根目录：`D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0`
- LightGBM / TimesFM 内部不改，只改接口层
- ExtremPriceClf 内部可以适配（提取函数、参数化路径）
- 融合链路（fusion/）可以新增文件和修改现有文件
- SMAPE clip50 是业务逻辑，不动
- 分类器合并规则（final_pred==1 且 pred<=100 → -80）是业务逻辑，不动

## 阶段 1：修复遗留问题

### 1.1 烟测产出路径

上一轮 Codex 说产出了 `outputs/unified_runs/` 下的 8 个 CSV，但该目录实际不存在。请检查：
- `pipelines/predict_pipeline.py` 中 `outputs/unified_runs/` 的输出路径配置
- `services/predict_service.py` 中实际写入逻辑
- 如果路径逻辑正确但文件不存在，说明烟测实际没有成功运行——重新运行一次并确认产出

### 1.2 根目录残留

- 移除 `lgbm_predict_diag.log`（移到 `_archive/root_cleanup/`）

### 1.3 TimesFM graceful fallback

当前 `TimesFM/pipeline.py` 在无权重时 raise RuntimeError。修改为：
- 如果无权重且无缓存预测 → log warning + 返回 None（不阻断其他模型）
- 在 `runners/executor.py` 中处理 None 返回值（跳过该模型的预测）

## 阶段 2：ExtremPriceClf 接口适配

### 2.1 提取核心函数

检查 `ExtremPriceClf/merge_model_scripts/run_daily.py`，确认核心逻辑是否可以直接作为函数调用。如果路径硬编码太多，做以下适配：

1. 在 `ExtremPriceClf/merge_model/core/cascade_daily.py` 中确保 `run_cascade_daily()` 或类似函数接受：
   - `data_path`：主数据文件路径（默认 `ExtremPriceClf/data/260525.xlsx`）
   - `fusion_pred_path`：融合预测文件路径（替代硬编码的 `data/融合模型预测电价数据.xlsx`）
   - `output_dir`：结果输出目录（替代硬编码的 `results/`）
   - `start_date`、`end_date`：日期范围

2. 如果改动量太大，可以写一个 wrapper 函数，在调用前把文件复制到分类器期望的路径，调用后再移回来。

### 2.2 确认数据兼容性

对比 `data/260525.xlsx` 和 `data/shandong_pmos_hourly.xlsx` 的列名。如果列名一致，分类器可以共用主数据文件。如果不一致，保持分类器使用自己的数据文件。

## 阶段 3：桥接模块实现

### 3.1 创建 `fusion/classifier_bridge.py`

```python
"""Bridge between fusion pipeline and ExtremPriceClf classifier."""
from pathlib import Path
import pandas as pd
import logging

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def convert_fusion_to_clf_input(fused_csv_path: Path, output_xlsx_path: Path) -> Path:
    """将融合链路的 RT fused_predictions.csv 转换为分类器输入格式。"""
    df = pd.read_csv(fused_csv_path, parse_dates=["ds"])
    rename_map = {"ds": "时刻", "y_fused": "预测实时电价"}
    df = df.rename(columns=rename_map)
    keep_cols = ["时刻", "预测实时电价"]
    if "y_true" in df.columns:
        keep_cols.append("y_true")
    df = df[keep_cols].sort_values("时刻").reset_index(drop=True)
    df.to_excel(output_xlsx_path, index=False)
    logger.info(f"Converted fusion output to classifier input: {output_xlsx_path}")
    return output_xlsx_path


def run_extreme_price_classifier(start_date: str, end_date: str,
                                  fusion_pred_path: Path,
                                  output_dir: Path,
                                  data_path: Path = None) -> Path:
    """调用 ExtremPriceClf 分类器，返回合并结果路径。"""
    # 导入分类器核心
    # 根据实际适配情况调整 import 路径和调用方式
    ...


def merge_clf_results(fused_csv_path: Path, clf_result_path: Path,
                       output_path: Path) -> pd.DataFrame:
    """将分类器结果合并回融合预测，生成修正后的输出。"""
    fused = pd.read_csv(fused_csv_path, parse_dates=["ds"])
    clf = pd.read_excel(clf_result_path)
    
    # 对齐时间列
    clf = clf.rename(columns={"时刻": "ds"})
    clf["ds"] = pd.to_datetime(clf["ds"])
    merged = fused.merge(
        clf[["ds", "final_pred", "p1_prob"]], 
        on="ds", how="left"
    )
    
    # 应用修正规则
    merged["y_fused_corrected"] = merged["y_fused"].copy()
    mask = (merged["final_pred"] == 1) & (merged["y_fused"] <= 100)
    merged.loc[mask, "y_fused_corrected"] = -80.0
    
    merged.to_csv(output_path, index=False)
    logger.info(f"Merged classifier results: {output_path}")
    return merged


def run_classifier_pipeline(fusion_work_dir: Path, start_date: str, end_date: str,
                              clf_data_path: Path = None) -> dict:
    """完整分类器后处理流程。"""
    rt_fused = fusion_work_dir / "realtime" / "fused_predictions.csv"
    if not rt_fused.exists():
        logger.warning(f"RT fused predictions not found: {rt_fused}, skipping classifier")
        return {"status": "skipped", "reason": "no_rt_fused"}
    
    clf_dir = fusion_work_dir / "classifier"
    clf_dir.mkdir(parents=True, exist_ok=True)
    
    # Step 1: 转换格式
    clf_input = clf_dir / "clf_input.xlsx"
    convert_fusion_to_clf_input(rt_fused, clf_input)
    
    # Step 2: 运行分类器
    clf_result = run_extreme_price_classifier(
        start_date, end_date, clf_input, clf_dir, clf_data_path
    )
    
    # Step 3: 合并结果
    corrected_path = fusion_work_dir / "realtime" / "fused_predictions_corrected.csv"
    merged = merge_clf_results(rt_fused, clf_result, corrected_path)
    
    # 统计修正数量
    n_corrected = (merged["final_pred"] == 1).sum()
    logger.info(f"Classifier corrected {n_corrected} hours to -80 CNY/MWh")
    
    return {
        "status": "completed",
        "corrected_hours": int(n_corrected),
        "output_path": str(corrected_path),
    }
```

### 3.2 创建 `pipelines/classifier_pipeline.py`

```python
"""Classifier post-processing pipeline."""
import logging
from pathlib import Path
from fusion.classifier_bridge import run_classifier_pipeline

logger = logging.getLogger(__name__)


def run_classifier_postprocess(fusion_work_dir, start_date, end_date, clf_data_path=None):
    """在融合完成后运行分类器后处理。"""
    logger.info(f"Starting classifier post-processing for {start_date} to {end_date}")
    result = run_classifier_pipeline(
        fusion_work_dir=Path(fusion_work_dir),
        start_date=start_date,
        end_date=end_date,
        clf_data_path=Path(clf_data_path) if clf_data_path else None,
    )
    if result["status"] == "completed":
        logger.info(f"Classifier corrected {result['corrected_hours']} hours")
    return result
```

### 3.3 修改 `cli/parser.py`

在现有 parser 中添加：
```python
parser.add_argument("--use-classifier", action="store_true", default=False,
                    help="启用 ExtremPriceClf 极端负电价分类器后处理（仅适用于 realtime 和 both）")
parser.add_argument("--clf-data", type=str, default=None,
                    help="分类器主数据文件路径（默认使用 ExtremPriceClf/data/260525.xlsx）")
```

### 3.4 修改 `pipelines/fusion_pipeline.py`

在 `run_fusion_pipeline()` 函数末尾，融合完成后追加：
```python
if getattr(args, 'use_classifier', False):
    if args.target == "dayahead":
        logger.warning("ExtremPriceClf 仅适用于实时电价场景，--use-classifier 在 dayahead 模式下跳过")
    else:
        from pipelines.classifier_pipeline import run_classifier_postprocess
        clf_result = run_classifier_postprocess(
            fusion_work_dir=args.fusion_work_dir,
            start_date=start_date,
            end_date=end_date,
            clf_data_path=getattr(args, 'clf_data', None),
        )
        logger.info(f"Classifier result: {clf_result}")
```

## 阶段 4：端到端验证

### 4.1 Mode A 验证（纯融合）

```bash
cd "D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0"
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline fusion --target both --start 2026-05-01 --end 2026-05-07
```

确认产出 `fused_predictions.csv`（DA + RT）。

### 4.2 Mode B 验证（融合 + 分类器）

```bash
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline fusion --target realtime --start 2026-05-01 --end 2026-05-07 --use-classifier
```

确认产出：
- `realtime/fused_predictions.csv`（原始融合）
- `realtime/fused_predictions_corrected.csv`（分类器修正）
- `classifier/clf_input.xlsx`
- `classifier/clf_results.xlsx`

### 4.3 Mode B 综合验证

```bash
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline fusion --target both --start 2026-05-01 --end 2026-05-07 --use-classifier
```

确认 DA + RT corrected + joint_report 都产出。

### 4.4 Dayahead + classifier 警告验证

```bash
D:\computer_download\environment\conda\epf-2\python.exe main.py --pipeline fusion --target dayahead --start 2026-05-01 --end 2026-05-07 --use-classifier
```

确认打印警告并跳过分类器，DA 融合正常产出。

## 交付

1. 所有新增/修改文件清单
2. 四个验证的运行结果（成功/失败 + 错误信息）
3. 分类器修正了哪些小时（日志输出）
4. 如果分类器运行失败，详细错误信息

## 注意事项

- 分类器需要 `lightgbm`、`xgboost`、`catboost`、`chinese_calendar`、`borax` 依赖，确认 epf-2 环境已安装
- 分类器的 P1 概率缓存机制（`data/p1交叉概率/p1.xlsx`）会加速重复运行
- 如果 `data/260525.xlsx` 数据截止到某个日期，分类器只能预测到该日期范围
- 先确认分类器独立运行正常，再做集成
