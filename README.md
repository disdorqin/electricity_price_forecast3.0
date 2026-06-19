# electricity_forecast_model2.0

本仓库聚合了多个电力负荷/电价时序预测模型的实现。包含四块内容:

- [`RT916_SpikeFusionNet/`](./RT916_SpikeFusionNet/) — RT916 脉冲融合网络(年度切片 + 双通道脉冲残差 + 域自适应)
- [`SGDFNet/`](./SGDFNet/) — SGDFNet(分段门控 + 误差/方向门控融合 + 区间与概率校准)
- [`TimeMixer/`](./TimeMixer/) — TimeMixer 日前/实时电价预测 pipeline(PyTorch 单文件实现,合作者 [Jonathan-ysy](https://github.com/Jonathan-ysy) 贡献,commit `e76bd45`)
- [`TimesFM/`](./TimesFM/) — TimesFM 时间序列基础模型(本地可编辑安装,依赖 `epf` 项目下的 `TF` 包)

> 四个模型独立运行、互相独立,共享相同的输入字段约定(参见 [`docs/metrics_calculation.md`](./docs/metrics_calculation.md))。

## 目录结构

```
.
├── README.md                ← 本文件
├── main.py                  ← 统一入口（四阶段链路 + 单阶段运行）
├── cli/                     ← CLI 参数解析
├── pipelines/               ← 各阶段 pipeline 实现
├── fusion/                  ← 融合引擎（权重拟合 + 加权融合 + 分类器桥接）
├── runners/                 ← 模型注册与调度
├── utils/                   ← 公共工具
├── data/                    ← 数据文件（shandong_pmos_hourly.xlsx）
├── requirements.txt
│
├── lightGBM/                ← LightGBM 日前/实时 pipeline
├── RT916_SpikeFusionNet/    ← RT916 脉冲融合网络
├── SGDFNet/                 ← SGDFNet 分段门控融合
├── TimeMixer/               ← TimeMixer（PyTorch）
├── TimesFM/                 ← TimesFM 时序基础模型
├── ExtremPriceClf/          ← 极端电价分类器
│
└── docs/                    ← 跨模型共享文档
    ├── metrics_calculation.md
    ├── 实验运行约定.md
    └── 项目执行逻辑与陪跑步骤对齐.md
```

## 快速上手

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

依赖包括:`numpy` / `pandas` / `scikit-learn` / `torch` / `pyyaml` / `joblib` /
`python-dotenv` / `openpyxl` / `chinese-calendar` / `borax` / `huggingface_hub[cli]` /
`safetensors`。

若需使用 TimesFM,还需在对应环境中本地安装 `epf` 项目下的 `TF` 包:

```bash
pip install -e <epf_project_path>/TF[xreg]
```

其中 `<epf_project_path>` 为 `epf` 项目主目录(例如 `D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\epf`),`[xreg]` 为 `scikit-learn` 等额外依赖。

### 2. 准备数据

`RT916_SpikeFusionNet` 需要山东省 PMOS 小时级电力数据(`.xlsx` 格式),
参见 `RT916_SpikeFusionNet/configs/default_cli.json` 中的 `RAW_DF_PATH` 字段。

`SGDFNet` 通过 `data_contract.py` 定义的契约读取数据。

`TimeMixer` 接受 GBK / UTF-8 / UTF-8-SIG 编码的 CSV,需包含以下列:
`ds` / `day_ahead_clearing_price` / `realtime_price` / `load` / `wind` / `solar` /
`interconnect` / `bidding_space`。

### 3. 运行各模型

```bash
# RT916
cd RT916_SpikeFusionNet && python run.py

# SGDFNet(以 protocol B 为例)
cd SGDFNet && python scripts/run_protocol_b.py

# TimeMixer
python TimeMixer/pipeline_timemixer.py --help

# TimesFM
python TimesFM/pipeline.py --help
```

详细使用方式见各子项目 README:
- RT916:[`RT916_SpikeFusionNet/README_RT916.md`](./RT916_SpikeFusionNet/README_RT916.md)
- SGDFNet:[`SGDFNet/README.md`](./SGDFNet/README.md)

## 端到端四阶段运行(推荐)

项目当前的标准运行链路为 **同步数据 → 模型训练/预测 → 学习器学习权重 → 加权融合 → 负电价分类器校正**。四个阶段必须按顺序执行,且统一使用 [`main.py`](./main.py) 入口。

```bash
# 阶段 0: 同步云端/数据库最新数据
#   - 优先尝试从 MySQL 数据库拉取(需配置 DB_HOST/DB/DB_USER/DB_PWD,见 .env 说明)
#   - 数据库失败时自动回退到七牛云 HTTP 下载最新 Excel
python main.py --pipeline sync_dataset

# 阶段 1: 模型训练与预测(日前 + 实时)
#   --date 为要预测的目标日期,例如明天
python main.py --pipeline model_stage --target both --date 2026-06-19 \
    --data-path data/shandong_pmos_hourly.xlsx \
    --training-months 12 --max-gpu-workers 1 --max-cpu-workers 2

# 阶段 2: 学习器在验证集上学习分时段权重(日前 + 实时)
#   必须在 model_stage 完成后执行
python main.py --pipeline learner_stage --target both --date 2026-06-19 \
    --data-path data/shandong_pmos_hourly.xlsx --training-months 12

# 阶段 3: 使用学习到的权重对预测结果进行加权融合
#   必须在 learner_stage 完成后执行
python main.py --pipeline fuse_stage --target both --date 2026-06-19

# 阶段 4: 实时预测结果输入负电价/极值分类器做后处理校正
#   仅支持 realtime,必须在 fuse_stage 完成后执行
python main.py --pipeline classifier_stage --target realtime --date 2026-06-19 \
    --data-path data/shandong_pmos_hourly.xlsx
```

最终输出目录结构示例(`daily_runs/2026-06-19/`):

```
daily_runs/2026-06-19/
├── dayahead/
│   ├── model_outputs/{lightgbm,timemixer,timesfm}/
│   ├── learner_outputs/weights.csv
│   └── final/fused_predictions.csv
└── realtime/
    ├── model_outputs/{rt916,sgdfnet,timemixer,timesfm}/
    ├── learner_outputs/weights.csv
    ├── final/fused_predictions.csv
    └── compat_fusion/
        ├── realtime/fused_predictions_corrected.csv
        └── classifier/2026-06-19_2026-06-19_clf.xlsx
```

### 数据库同步配置

`--pipeline sync_dataset` 优先读取 MySQL。需在项目根目录或相邻 `epf/` 目录的 `.env` 文件中配置:

```env
DB_HOST=your_db_host
DB=your_database
DB_USER=your_user
DB_PWD=your_password
```

若数据库不可达,会自动回退到七牛云 HTTP 下载 (`http://qiniu.dirx.com.cn/workspace/eprice_forecast/shandong_pmos_hourly_20220101_YYYYMMDD.xlsx`)。

## 输出文件与最终预测值

每次运行以 `daily_runs/{日期}/{目标}/` 为根目录，四个阶段的输出如下：

| 阶段 | 文件路径 | 关键列 | 说明 |
|------|---------|--------|------|
| model_stage | `model_outputs/{模型名}/forecast_predictions.csv` | `时刻`, `预测值` | 各模型独立预测结果 |
| learner_stage | `learner_outputs/weights.csv` | `period`, `model_name`, `weight` | SLSQP 拟合的分时段融合权重 |
| fuse_stage | `final/fused_predictions.csv` | `y_fused` | **融合预测值（校正前）** |
| classifier_stage | `compat_fusion/realtime/fused_predictions_corrected.csv` | `y_fused_corrected` | **实时最终预测值（经极端电价校正）** |

**最终预测值取法：**

- **日前 (DA)**：以 `final/fused_predictions.csv` 的 `y_fused` 列为准（日前无分类器校正）。
- **实时 (RT)**：以 `compat_fusion/realtime/fused_predictions_corrected.csv` 的 `y_fused_corrected` 列为准。该列在 `y_fused` 基础上，对分类器判定为极端负电价的时段（`final_pred=1` 且 `y_fused ≤ 100`）修正为 `-80.0` 元/MWh。

## SMAPE 计算口径

项目统一使用 **SMAPE-floor50** 作为业务评估指标，计算公式见 [`docs/metrics_calculation.md`](./docs/metrics_calculation.md)，核心规则：

1. **clip50 裁剪**：计算前对 `y_true` 和 `y_pred` 均做 `max(value, 50)` 处理，避免低价区间对误差的放大效应。负电价和接近零的价格都会被裁剪到 50。
2. **计算公式**：`SMAPE = mean(|pred_clip - true_clip| / ((|pred_clip| + |true_clip|) / 2)) × 100%`
3. **代码位置**：`fusion/metrics.py` → `smape_floor50(y_true, y_pred)`
4. **计算基准**：
   - 评估**融合效果**时，使用 `fused_predictions.csv` 的 `y_fused` 列（校正前）
   - 评估**最终业务效果**时，使用 `fused_predictions_corrected.csv` 的 `y_fused_corrected` 列（校正后，仅 RT）
   - 注意：极端电价校正可能改善也可能劣化 SMAPE，取决于分类器精度

## CLI 参数说明

以下为 `main.py` 的完整参数列表。标注 **[可调]** 的参数可根据实际场景调整，其余参数建议使用默认值。

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--pipeline` | (必填) | 运行阶段：`sync_dataset` / `model_stage` / `learner_stage` / `fuse_stage` / `classifier_stage` / `predict` / `train` / `evaluate` / `fusion` |
| `--date` | None | **[可调]** 目标预测日期（YYYY-MM-DD），即"D日"。通常为明天或指定日期 |
| `--start` / `--end` | None | **[可调]** 批量预测的起止日期，与 `--date` 二选一 |
| `--target` | `both` | **[可调]** 预测目标：`dayahead`（仅日前）、`realtime`（仅实时）、`both`（两者） |
| `--data-path` | `data/shandong_pmos_hourly.xlsx` | **[可调]** 数据文件路径，甲方环境需改为实际路径 |
| `--training-months` | `12` | **[可调]** 训练数据回溯月数。增大可提升训练数据量但增加训练时间，12个月为推荐值 |
| `--validation-days` | `30` | **[可调]** 验证窗口天数。影响 SLSQP 权重拟合的样本量，30天（720行）为推荐值。减小可加速但权重可能不稳定 |
| `--stage-models` | `formal` | **[可调]** 模型集合：`formal`（正式模型集）、`all`（全部）、或逗号分隔的模型名 |
| `--max-cpu-workers` | `2` | **[可调]** CPU 并行数。LightGBM/SGDFNet 使用 CPU，建议设为 CPU 核心数的一半 |
| `--max-gpu-workers` | `1` | **[可调]** GPU 并行数。RT916/TimeMixer/TimesFM 使用 GPU，单卡建议保持 1 |
| `--weight-lower-bound` | `-0.5` | 融合权重下界。允许负权重（对冲），一般不需调整 |
| `--weight-upper-bound` | `1.2` | 融合权重上界。允许单模型权重 >1（配合负权重对冲），一般不需调整 |
| `--output-root` | `outputs/unified_runs` | 模型输出根目录 |
| `--daily-run-root` | `daily_runs` | 四阶段链路输出根目录 |
| `--use-classifier` | `false` | 是否启用极端电价分类器（仅 RT） |
| `--clf-data` | `ExtremPriceClf/data/260525.xlsx` | **[可调]** 分类器训练数据路径。默认文件仅覆盖到 2026-05-26，更晚日期需指定更新的数据文件 |
| `--use-predicted-temp` | `false` | 是否使用预测温度（而非实际温度），用于未来实际场景 |
| `--segment-count` | `3` | 时段分段数（1_8 / 9_16 / 17_24），不建议修改 |
| `--seed` | `42` | 随机种子 |
| `--conda-env` | `""` | Conda 环境名。留空使用当前 Python 环境 |

### 常用调参建议

- **加速运行**：减小 `--validation-days`（如 14），减少 `--training-months`（如 6），但会损失权重拟合质量和训练数据量
- **提升稳定性**：增大 `--validation-days`（如 60），但 DA 模型训练数据会相应减少
- **减少模型数**：`--stage-models lightgbm,timemixer` 只跑指定模型，适合快速调试
- **甲方部署**：必须修改 `--data-path` 为实际数据路径，确认 `--clf-data` 数据覆盖到目标日期

## 跨模型文档

- [`docs/metrics_calculation.md`](./docs/metrics_calculation.md) — 三个模型统一使用的评估指标计算口径(SMAPE、MAPE、MAE、RMSE、方向准确率等)
- [`docs/实验运行约定.md`](./docs/实验运行约定.md) — 实验运行流程约定
- [`docs/项目执行逻辑与陪跑步骤对齐.md`](./docs/项目执行逻辑与陪跑步骤对齐.md) — 项目执行逻辑与陪跑步骤说明

## 许可

本仓库以 [MIT License](./LICENSE) 发布。
`TimeMixer/pipeline_timemixer.py` 版权归原作者(commit `e76bd45` 作者)所有,采用同样的 MIT 许可。

## 贡献

- `RT916_SpikeFusionNet` / `SGDFNet` / 顶层文档:[disdorqin](https://github.com/disdorqin)
- `TimeMixer/pipeline_timemixer.py`:[Jonathan-ysy](https://github.com/Jonathan-ysy)(commit `e76bd45`)
