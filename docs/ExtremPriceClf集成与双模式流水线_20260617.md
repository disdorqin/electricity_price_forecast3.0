# ExtremPriceClf 集成与流水线双模式设计（2026-06-17）

## 一、现状梳理

### 融合链路（Mode A，已有）

```
main.py --pipeline fusion --target {dayahead|realtime|both} --start D1 --end D2
   ↓
pipelines/fusion_pipeline.py → services/fusion_service.py
   ↓
fusion/run_full_fusion_suite.py（或 run_dayahead_pipeline.py / run_realtime_pipeline.py）
   ↓
fusion/pipeline_common.py::run_task_pipeline()
   ├── 收集各模型预测（adapters 标准化为 8 列长表）
   ├── 拟合分段权重（SLSQP 优化 SMAPE+正则）
   └── 加权融合 → fused_predictions.csv
```

DA 模型池：lightgbm、timesfm、timemixer（3 个）
RT 模型池：rt916、sgdfnet、timesfm、timemixer（4 个）

融合产出核心文件：
- `<task>/fused_predictions.csv`：列 ds, period, hour_business, y_true, y_fused, + 各模型 y_pred
- `joint_report/final_truth_vs_fusion.csv`：DA+RT 并排对比
- `weights.csv`：分段融合权重

### ExtremPriceClf 分类器（已有，独立运行）

```
cd ExtremPriceClf/
python merge_model_scripts/run_daily.py <start> <end> [--merge]
```

两级级联分类器：
- Stage 1：LightGBM（从 2024-01-01 训练），输出极端负电价概率 p1_prob
- Stage 2：XGBoost（从 2022-01-01 训练），仅对灰色地带样本精判
- 最终判定：final_pred = 1 表示该小时可能出现 ≤ -50 元/MWh 的极端负电价

合并规则（--merge 模式）：
- 若 final_pred == 1 且 预测实时电价 ≤ 100 → 输出电价强制修正为 **-80 元/MWh**
- 否则保留原预测值

输入要求：
- `data/260525.xlsx`：完整市场数据（20+ 列，从 2022-01-01 起）
- `data/融合模型预测电价数据.xlsx`：融合模型预测值（列：时刻, 预测实时电价）

输出：
- `results/{start}_{end}_clf.xlsx`：分类结果（final_pred 等）
- `results/{start}_{end}_merged_price.xlsx`：修正后电价

### 关键差异

| 维度 | 融合链路 | ExtremPriceClf |
|------|----------|----------------|
| 输入粒度 | 按日预测，多模型 | 按日滚动，全量历史 |
| 时间列名 | `ds` (datetime) | `时刻` (datetime) |
| 预测列名 | `y_fused` | `预测实时电价` |
| 真实值列名 | `y_true` | `实时电价` |
| 输出格式 | CSV | Excel (.xlsx) |
| 适用范围 | DA + RT | **仅 RT**（基于实时电价判定） |
| 数据依赖 | 各模型预测结果 | 完整市场数据文件（20+ 列） |

---

## 二、双模式设计

### CLI 接口

```bash
# Mode A：纯融合（不加分类器）
python main.py --pipeline fusion --target dayahead --start 2026-05-01 --end 2026-05-31
python main.py --pipeline fusion --target realtime --start 2026-05-01 --end 2026-05-31
python main.py --pipeline fusion --target both     --start 2026-05-01 --end 2026-05-31

# Mode B：融合 + 分类器后处理
python main.py --pipeline fusion --target realtime --start 2026-05-01 --end 2026-05-31 --use-classifier
python main.py --pipeline fusion --target both     --start 2026-05-01 --end 2026-05-31 --use-classifier

# 注意：--target dayahead + --use-classifier 会给出警告并忽略分类器
# 因为 ExtremPriceClf 当前只针对实时电价的极端负电价场景
```

### 模式矩阵

| --target | Mode A（纯融合） | Mode B（+ 分类器） |
|----------|-----------------|-------------------|
| dayahead | DA 融合结果 | DA 融合结果（分类器不适用，跳过） |
| realtime | RT 融合结果 | RT 融合 → 分类器修正 → 最终 RT 结果 |
| both | DA + RT 融合结果 + 套利分析 | DA 融合 + RT 融合 → RT 分类器修正 → 联合报告 |

### Mode B 完整流程

```
Step 1：运行标准融合（Mode A 全流程）
   ↓ 产出：
   ├── dayahead/fused_predictions.csv
   └── realtime/fused_predictions.csv

Step 2：格式转换（fusion → ExtremPriceClf 输入格式）
   ├── 读取 realtime/fused_predictions.csv
   ├── 列重映射：ds → 时刻, y_fused → 预测实时电价
   ├── 按日期排序
   └── 写出临时文件：fusion_rt_for_clf.xlsx

Step 3：运行 ExtremPriceClf
   ├── 输入 1：data/260525.xlsx（完整市场数据）
   ├── 输入 2：fusion_rt_for_clf.xlsx（融合预测）
   ├── 逐日滚动分类
   └── 产出：merged_price.xlsx（修正后电价）

Step 4：回写融合结果
   ├── 读取 merged_price.xlsx
   ├── 列重映射：时刻 → ds, 融合预测电价 → y_fused_corrected, final_pred
   ├── 追加 final_pred 列到原 fused_predictions.csv
   └── 输出：realtime/fused_predictions_corrected.csv
```

---

## 三、实现方案

### 3.1 新增文件

#### `fusion/classifier_bridge.py`（核心桥接模块）

职责：
1. `convert_fusion_to_clf_input(fused_csv_path, output_xlsx_path)` — 格式转换
2. `run_extreme_price_classifier(start_date, end_date, clf_input_path, data_path)` — 调用分类器
3. `merge_clf_results(fused_csv_path, clf_result_path, output_path)` — 合并修正结果
4. `run_classifier_pipeline(fusion_work_dir, start_date, end_date)` — 编排以上三步

```python
def convert_fusion_to_clf_input(fused_csv_path: Path, output_xlsx_path: Path):
    """将融合链路的 RT fused_predictions.csv 转换为分类器输入格式。"""
    df = pd.read_csv(fused_csv_path)
    # 列重映射
    rename_map = {
        "ds": "时刻",
        "y_fused": "预测实时电价",
    }
    df = df.rename(columns=rename_map)
    # 只保留分类器需要的两列（+ y_true 用于验证）
    keep_cols = ["时刻", "预测实时电价"]
    if "y_true" in df.columns:
        keep_cols.append("y_true")
    df = df[keep_cols].sort_values("时刻").reset_index(drop=True)
    df.to_excel(output_xlsx_path, index=False)
    return output_xlsx_path


def run_extreme_price_classifier(start_date: str, end_date: str,
                                  clf_input_path: Path,
                                  data_path: Path = None):
    """调用 ExtremPriceClf 的 run_daily 逻辑，返回分类结果路径。"""
    import sys
    ext_clf_root = PROJECT_ROOT / "ExtremPriceClf"
    # 将分类器的 merge_model_scripts/run_daily.py 核心逻辑作为函数调用
    # 或者 subprocess 调用
    from ExtremPriceClf.merge_model.core.cascade_daily import run_cascade_daily
    
    # 设置分类器的融合预测文件路径
    # ... 具体实现需要适配分类器的内部接口
    ...


def merge_clf_results(fused_csv_path: Path, clf_result_path: Path, 
                       output_path: Path):
    """将分类器结果合并回融合预测，生成修正后的输出。"""
    fused = pd.read_csv(fused_csv_path)
    clf = pd.read_excel(clf_result_path)
    
    # 对齐时间列
    clf = clf.rename(columns={"时刻": "ds"})
    merged = fused.merge(
        clf[["ds", "final_pred", "p1_prob", "p2_prob"]], 
        on="ds", how="left"
    )
    
    # 应用修正规则：final_pred==1 且 y_fused <= 100 → y_fused_corrected = -80
    merged["y_fused_corrected"] = merged["y_fused"].copy()
    mask = (merged["final_pred"] == 1) & (merged["y_fused"] <= 100)
    merged.loc[mask, "y_fused_corrected"] = -80.0
    
    merged.to_csv(output_path, index=False)
    return merged
```

#### `pipelines/classifier_pipeline.py`（流水线编排）

在融合完成后追加分类器步骤。

### 3.2 修改文件

#### `cli/parser.py`

新增参数：
```python
parser.add_argument("--use-classifier", action="store_true", default=False,
                    help="启用 ExtremPriceClf 极端负电价分类器后处理")
parser.add_argument("--clf-data", type=str, default=None,
                    help="分类器主数据文件路径（默认 ExtremPriceClf/data/260525.xlsx）")
```

#### `pipelines/fusion_pipeline.py`

在 `run_fusion_pipeline()` 末尾追加：
```python
if args.use_classifier:
    if args.target == "dayahead":
        logger.warning("ExtremPriceClf 仅适用于实时电价，--use-classifier 在 dayahead 模式下跳过")
    else:
        from pipelines.classifier_pipeline import run_classifier_postprocess
        run_classifier_postprocess(
            fusion_work_dir=args.fusion_work_dir,
            start_date=args.start or args.date,
            end_date=args.end or args.date,
            clf_data_path=args.clf_data,
        )
```

#### `services/fusion_service.py`

不需要改动。分类器后处理在 fusion_service 返回结果后执行。

### 3.3 ExtremPriceClf 侧适配

当前 `run_daily.py` 的设计是独立脚本，硬编码了数据路径。需要做：

1. **提取核心函数**：将 `run_daily.py` 的核心逻辑封装为可 import 的函数
   - 在 `ExtremPriceClf/merge_model/core/cascade_daily.py` 中确保 `run_cascade_daily()` 接受参数化的路径
   
2. **参数化融合预测路径**：当前 `--merge` 读取固定路径 `data/融合模型预测电价数据.xlsx`，需要改为接受任意路径

3. **输出路径可控**：当前写入 `results/` 目录，需要允许指定输出目录

这些改动在分类器内部完成，不影响融合链路。

### 3.4 输出结构

```
fusion_runs/
└── {run_name}/
    ├── dayahead/
    │   └── fused_predictions.csv          # Mode A & B 都产出
    ├── realtime/
    │   ├── fused_predictions.csv          # Mode A & B 都产出
    │   └── fused_predictions_corrected.csv # 仅 Mode B 产出
    ├── classifier/                         # 仅 Mode B
    │   ├── clf_input.xlsx                 # 融合→分类器的输入
    │   ├── clf_results.xlsx               # 分类器原始输出
    │   └── clf_merged.xlsx                # 分类器合并输出
    ├── joint_report/
    │   ├── final_truth_vs_fusion.csv      # Mode A
    │   └── final_truth_vs_fusion_corrected.csv  # Mode B（RT 使用修正值）
    ├── weights.csv
    └── suite_summary.json
```

---

## 四、注意事项

### 4.1 分类器仅适用于 RT

ExtremPriceClf 的判定逻辑是 `实时电价 < -50`，这是 RT 特有现象（DA 市场有价格上下限，不太可能出现极端负价）。如果用户对 `--target dayahead --use-classifier` 组合使用，应打印警告并跳过分类器。

### 4.2 分类器的数据依赖

分类器需要 `data/260525.xlsx`（从 2022-01-01 起的完整市场数据），这个文件与融合链路使用的 `data/shandong_pmos_hourly.xlsx` 是同一个数据源但可能列名和格式不同。需要确认两者的列兼容性，或者明确分类器使用自己的数据文件。

### 4.3 分类器的计算成本

分类器采用逐日滚动训练（每天重训 Stage 1 + Stage 2），对长日期范围（如一个月）可能较慢。建议：
- 单日预测：直接调用
- 月度范围：考虑分段并行或缓存 P1 概率（分类器已有 P1 缓存机制）

### 4.4 TimesFM 权重问题

当前 TimesFM 本地无权重，统一入口 `--models all` 会报错。临时方案：
- `--models lightgbm,timemixer,sgdfnet,rt916`（跳过 timesfm）
- 或在 runners/executor.py 中做 graceful fallback（模型不可用时跳过而非报错）

---

## 五、执行顺序

### 阶段 1：修复遗留问题（30 分钟）

1. 确认烟测产出路径问题（`outputs/unified_runs/` 不存在）
2. 删除根目录 `lgbm_predict_diag.log`
3. 处理 TimesFM 权重缺失（graceful fallback 或显式跳过）

### 阶段 2：ExtremPriceClf 接口适配（1 小时）

1. 将 `run_daily.py` 核心逻辑封装为 `run_cascade_daily()` 函数（参数化路径）
2. 确认 `data/260525.xlsx` 和 `data/shandong_pmos_hourly.xlsx` 的列兼容性
3. 测试分类器独立运行（不通过融合链路）

### 阶段 3：桥接模块实现（1 小时）

1. 创建 `fusion/classifier_bridge.py`
2. 创建 `pipelines/classifier_pipeline.py`
3. 修改 `cli/parser.py` 增加 `--use-classifier` 参数
4. 修改 `pipelines/fusion_pipeline.py` 追加分类器步骤

### 阶段 4：端到端验证（30 分钟）

```bash
# Mode A 验证
python main.py --pipeline fusion --target both --start 2026-05-01 --end 2026-05-07

# Mode B 验证
python main.py --pipeline fusion --target realtime --start 2026-05-01 --end 2026-05-07 --use-classifier

# Mode B 综合验证
python main.py --pipeline fusion --target both --start 2026-05-01 --end 2026-05-07 --use-classifier
```

确认产出：
- Mode A：fused_predictions.csv（DA + RT）
- Mode B：fused_predictions.csv + fused_predictions_corrected.csv（RT）+ classifier/ 目录
- Mode B both：DA fused + RT corrected + joint_report（RT 用修正值）
