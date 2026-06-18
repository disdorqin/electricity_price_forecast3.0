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
├── LICENSE                  ← MIT 许可
├── requirements.txt         ← 三个模型共用的依赖清单
├── .gitignore
│
├── RT916_SpikeFusionNet/    ← 模型 1:RT916 脉冲融合网络
│   ├── README.md
│   ├── README_RT916.md
│   ├── FINAL_PACKAGING_SUMMARY.md
│   ├── run.py
│   ├── configs/
│   ├── docs/                ← RT916 自身架构/边界说明
│   └── src/rt916_spikefusionnet/
│
├── SGDFNet/                 ← 模型 2:SGDFNet
│   ├── README.md
│   ├── configs/
│   ├── docs/                ← MODEL_CARD、USAGE、CHANGELOG
│   ├── research_control/    ← 实验 ledger / freeze 状态 / best model 注册
│   ├── scripts/             ← 4 个运行入口
│   └── src/sgdfnet/
│
├── TimeMixer/               ← 模型 3:TimeMixer(合作者贡献)
│   └── pipeline_timemixer.py
│
├── TimesFM/                 ← 模型 4:TimesFM(本地可编辑安装)
│   ├── pipeline.py
│   ├── infer.py
│   └── requirements.txt
│
└── docs/                    ← 跨模型共享文档
    ├── metrics_calculation.md                  ← 指标计算口径约定
    ├── 实验运行约定.md                              ← 实验运行流程约定
    └── 项目执行逻辑与陪跑步骤对齐.md                  ← 项目执行与陪跑步骤说明
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
