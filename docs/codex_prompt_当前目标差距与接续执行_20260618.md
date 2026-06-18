# Codex 接续提示词：当前目标差距与接续执行（2026-06-18）

你现在要继续接手项目：

`D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0`

运行环境：

- Conda：`epf-2`
- Python：`D:\computer_download\environment\conda\epf-2\python.exe`
- Windows 中文路径

## 一、最终目标

把项目整理成“稳定可执行”的四阶段链路，并满足：

1. 日前和实时都能执行
2. `RT916` 的实时预测必须严格遵守：
   - 先预测日前 DA
   - 再把 DA 预测注入 RT
   - 再产出实时 RT 预测
3. 文件夹设计可以调整，但必须保证实际能跑
4. 运行方式采用四步骤：
   - `model_stage`
   - `learner_stage`
   - `fuse_stage`
   - `classifier_stage`

## 二、目前已经完成的事情

### 1. 四阶段框架已经落地

已存在并可调用：

- `main.py`
- `cli/parser.py`
- `pipelines/staged_pipeline.py`

可执行命令：

```powershell
python main.py --pipeline model_stage --target both --date 2026-06-18 --validation-days 1
python main.py --pipeline learner_stage --target both --date 2026-06-18
python main.py --pipeline fuse_stage --target both --date 2026-06-18
python main.py --pipeline classifier_stage --target realtime --date 2026-06-18
```

### 2. RT916 realtime 联动已修正

文件：

- `RT916_SpikeFusionNet/pipeline.py`

关键事实：

- `target == "realtime"` 时，不再走普通单独 RT 回测
- 已改为调用 `core.run_joint_da_rt_daily_backtest(...)`
- 即先 DA，再 RT，符合要求

### 3. learner/fuse 已不再是占位版

文件：

- `pipelines/staged_pipeline.py`
- `fusion/weights.py`
- `fusion/run_fixed_window_fusion.py`

当前行为：

- `model_stage` 产出验证集预测和当天预测
- `learner_stage` 会把验证集转成 contract long table
- `learner_stage` 会真实学习权重，不再是均匀占位
- `fuse_stage` 会按权重产出 `fused_predictions.csv`

### 4. classifier 链路不再硬崩

文件：

- `fusion/classifier_bridge.py`
- `pipelines/staged_pipeline.py`

当前行为：

- `target=dayahead` 时，分类器会直接 `skipped`
- 分类器数据不覆盖目标日期时，会返回 `skipped`
- 不再因日期不覆盖直接异常退出

### 5. 分类器环境依赖已补齐

已安装：

- `xgboost==3.2.0`
- `catboost==1.2.10`

且已写入：

- `requirements.txt`

## 三、当前已经验证通过的部分

### 已验证通过的主链路

以 `2026-06-18` 为例，下面这些文件已经成功产出：

- `daily_runs/2026-06-18/dayahead/model_outputs/rt916/val_predictions.csv`
- `daily_runs/2026-06-18/dayahead/model_outputs/rt916/forecast_predictions.csv`
- `daily_runs/2026-06-18/dayahead/final/fused_predictions.csv`
- `daily_runs/2026-06-18/realtime/model_outputs/rt916/val_predictions.csv`
- `daily_runs/2026-06-18/realtime/model_outputs/rt916/forecast_predictions.csv`
- `daily_runs/2026-06-18/realtime/final/fused_predictions.csv`

现状：

- 日前 `fused_predictions.csv`：24 行
- 实时 `fused_predictions.csv`：24 行
- `y_fused` 无空值

### 已验证的分类器行为

命令：

```powershell
python main.py --pipeline classifier_stage --target realtime --date 2026-06-18
```

当前返回：

- `skipped`

原因：

- 分类器数据 `ExtremPriceClf/data/260525.xlsx` 只覆盖到 `2026-05-26 00:00:00`
- 目标日 `2026-06-18` 超出分类器数据覆盖范围

这说明：

- 分类器入口逻辑可执行
- 当前不是代码错误
- 是分类器数据日期覆盖不足

## 四、目前距离最终目标还差什么

### 差距 1：默认正式模型集合还不能放入全部目标模型

用户想要的正式默认集合是：

- 日前：`lightgbm + timesfm + timemixer`
- 实时：`timesfm + timemixer + sgdfnet + rt916`

但当前为了保证链路稳定，默认正式集合被回退成：

- 日前：`lightgbm + timesfm + rt916`
- 实时：`timesfm + rt916`

原因不是不想加，而是加进去以后默认链路会失稳。

### 差距 2：TimeMixer 还不能稳定进入默认正式链路

已观察到的问题：

- `dayahead/timemixer` 报：
  - `Excel file format cannot be determined, you must specify an engine manually.`
- `realtime/timemixer` 报：
  - `没有可用样本`
  - 或同样的 Excel 格式问题

结论：

- `timemixer` 仍需继续修数据入口与样本构造
- 暂时不能作为“默认必跑模型”

### 差距 3：SGDFNet 还不能稳定进入默认正式链路

已观察到的问题：

- `realtime/sgdfnet` 报：
  - `Excel file format cannot be determined, you must specify an engine manually.`

结论：

- `SGDFNet` 当前输入数据路径/格式适配还没完全打通
- 暂时不能作为“默认必跑模型”

### 差距 4：TimesFM 仍受本地权重缺失影响

已观察到的问题：

- `Skipping TimesFM ... model weights are missing`

结论：

- `TimesFM` 代码入口保留了
- 但本地模型权重没准备好时只能跳过
- 这不是框架错误，是资源不齐

### 差距 5：LightGBM 对未来预测日仍不稳定

已观察到的问题：

- `dayahead/lightgbm` 在某些预测日返回：
  - `未找到对应日期数据`
  - `'NoneType' object has no attribute 'columns'`

结论：

- `lightgbm` 在验证日有时可产出
- 对未来日预测窗口仍不稳定
- 还不能作为唯一稳定日前模型

### 差距 6：分类器 B 链路尚未完成“目标日期可实跑”

当前状态：

- 入口逻辑已打通
- 依赖已补齐
- 但分类器数据只覆盖到 `2026-05-26`

结论：

- B 链路代码层面基本通了
- 真正跑 6 月后续日期，还需要更新分类器数据

## 五、当前最真实的项目状态

### 现在“可以说已经做到”的

1. 四阶段框架已成型
2. RT916 实时联动已符合要求
3. 日前和实时主链路都能产出融合结果
4. 分类器链路不会再因环境或日期问题硬崩

### 现在“还不能说已经做到”的

1. 不能说所有目标模型都已经稳定纳入默认正式集合
2. 不能说 TimeMixer、SGDFNet、TimesFM、LightGBM 都已达到默认可用状态
3. 不能说分类器 B 链路对 2026-06-18 这种日期已经实跑成功

## 六、下一步最优先任务

接下来不要再扩框架，直接修“默认目标模型集合”：

### 任务 A：修 TimeMixer

目标：

- 让 `timemixer` 能稳定读取当前 `data/shandong_pmos_hourly.xlsx`
- 消除 Excel engine / sample empty 问题
- 至少能在 `model_stage` 中稳定生成：
  - `val_predictions.csv`
  - `forecast_predictions.csv`

### 任务 B：修 SGDFNet

目标：

- 让 `sgdfnet` 的 pipeline 正确接收主项目数据文件
- 消除 Excel format 问题
- 至少能在 realtime `model_stage` 中产出验证集和预测集

### 任务 C：处理 TimesFM

目标：

- 如果能补齐权重，则纳入默认链路
- 如果暂时不能补权重，至少保留“可跳过但不影响默认链路”的机制

### 任务 D：处理 LightGBM

目标：

- 修复未来预测日下返回 `None` 的问题
- 保证日前至少能稳定产出当天预测

### 任务 E：最终恢复用户指定默认正式集合

恢复为：

- 日前：`lightgbm + timesfm + timemixer`
- 实时：`timesfm + timemixer + sgdfnet + rt916`

前提：

- 这些模型加入默认后，四步骤链路不能整体断

## 七、执行原则

1. 不能只“加进名单”，必须保证链路可执行
2. 如果某模型还不稳定，不要让它拖死整个默认链路
3. 对 realtime，始终保持 `RT916` 先 DA 后 RT
4. 所有修改都直接落在主项目目录，不要另起新项目
5. 优先做能提升“默认链路可执行性”的修复，而不是继续做架构美化

## 八、建议接续命令

### 查看当前稳定主链路

```powershell
python main.py --pipeline model_stage --target both --date 2026-06-18 --validation-days 1
python main.py --pipeline learner_stage --target both --date 2026-06-18
python main.py --pipeline fuse_stage --target both --date 2026-06-18
```

### 查看分类器跳过行为

```powershell
python main.py --pipeline classifier_stage --target realtime --date 2026-06-18
```

### 继续修默认目标模型集合

重点文件：

- `pipelines/staged_pipeline.py`
- `TimeMixer/pipeline.py`
- `TimeMixer/repro_pipeline.py`
- `SGDFNet/pipeline.py`
- `lightGBM/pipeline.py`
- `TimesFM/pipeline.py`

