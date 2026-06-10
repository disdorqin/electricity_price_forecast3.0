# 极端负电价两阶段级联分类器

## 概述

对电力市场实时电价进行逐日滚动预测，识别 **价格 ≤ -50 元/MWh** 的极端负电价时刻。采用两阶段级联架构：

- **阶段1（ExtremePriceRadar）**：基于特征工程的轻量级分类器，输出全量概率 `p1_prob`
- **阶段2（灰度区精细化）**：对阶段1置信度不足的"灰度区"样本，用 XGBoost/LightGBM/CatBoost 重新训练并预测，输出 `p2_prob`
- **最终决策**：`p1_prob > gray_high → 正例`，`p1_prob < gray_low → 负例`，`其余 → 取 p2 结果`

支持动态灰度阈值选择：基于历史窗口的 Precision/Recall 搜索，自动回退。

## 项目结构

```
├── data/
│   ├── 260525.xlsx                     # 训练数据集
│   ├── 融合模型预测电价数据.xlsx         # 外部电价预测模型结果（用于融合）
│   └── p1交叉概率/                      # 阶段1 OOF 概率缓存目录
├── merge_model/
│   ├── __init__.py
│   └── core/
│       ├── cascade_daily.py            # 核心编排：数据准备、滚动预测、评估
│       ├── stage2_model/
│       │   ├── lightgbm_model.py       # LightGBM 封装
│       │   ├── xgboost_model.py        # XGBoost 封装
│       │   └── catboost_model.py       # CatBoost 封装
│       └── extreme_price_radar/
│           ├── pipeline.py             # 阶段1 训练/推理管线
│           ├── features.py             # 特征工程
│           ├── classifier.py           # ExtremePriceClassifier
│           ├── data_builder.py         # 数据集构建 + 标签生成
│           └── generate_oof_prob_feature.py  # OOF 概率生成
├── merge_model_scripts/
│   └── run_daily.py                    # CLI 入口脚本
├── results/                            # 预测结果输出目录
└── readme.md
```

## 依赖

- Python >= 3.9
- numpy, pandas, openpyxl
- lightgbm, xgboost, catboost
- scikit-learn, matplotlib
- chinese_calendar

## 用法

### CLI 方式

```bash
python merge_model_scripts/run_daily.py <start_date> <end_date> [options]
```

**参数：**

| 参数 | 说明                              |
|------|---------------------------------|
| `start_date` | 预测起始日期（包括起始日期当天），如 `2026-01-06`          |
| `end_date` | 预测结束日期（包括结束日期当天），如 `2026-05-27` |
| `--output, -o` | 输出目录（默认: `results`）             |
| `--data, -d` | 输入数据文件（默认: `data/260525.xlsx`）  |
| `--merge` | 与外部电价预测模型融合输出最终电价               |

**示例：**

```bash
# 仅输出分类器预测结果
python merge_model_scripts/run_daily.py 2026-01-06 2026-05-27

# 分类器 + 电价融合（需 data/融合模型预测电价数据.xlsx 存在）
python merge_model_scripts/run_daily.py 2026-01-06 2026-05-27 --merge
```

## 输出说明

### 分类器输出

| 列名 | 说明 |
|------|------|
| 时刻 | 时间点 |
| 实时电价 | 真实电价 |
| 真实极值标签 | 是否 ≤ -50（1/0） |
| p1_prob | 阶段1 预测概率 |
| p1_pred | 阶段1 预测标签 |
| p2_prob | 阶段2 预测概率（灰度区有效） |
| p2_pred | 阶段2 预测标签（灰度区有效） |
| gray_low / gray_high | 当前灰度阈值 |
| final_pred | 最终预测标签（1 = 极端负电价） |

### 融合电价输出（--merge-price）

基于分类器 `final_pred` 与外部电价预测模型结果融合：

- `final_pred == 1` 且 `预测实时电价 ≤ 100` → 输出 `-80`
- 否则保留原电价预测值

## 核心参数说明

### Stage2Config

| 参数 | 默认值        | 说明 |
|------|------------|------|
| feature_type | 预测值        | 二阶段特征类型（预测值/实际值） |
| model_name | lightgbm   | 二阶段模型 |
| threshold | 0.5        | 二阶段固定阈值 |
| gray_low / gray_high | 0.13 / 0.68 | 灰度区边界 |
| dynamic_gray_enabled | False      | 是否开启动态灰度 |
| dynamic_window_days | 90         | 动态灰度历史窗口长度 |
| dynamic_min_samples | 720        | 窗口最小样本数 |
| dynamic_min_positives | 80         | 窗口最小正样本数 |
| dynamic_recall_min | 0.95       | 动态灰度最小召回率约束 |
| dynamic_precision_min | 0.80       | 动态灰度最小精确率约束 |
