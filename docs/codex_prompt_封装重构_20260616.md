先读以下文档了解完整背景和执行方案：
1. `docs/项目封装与统一运行重构计划_20260616.md` — 整体架构设计和执行顺序
2. `docs/SGDFNet审计与收官修订_20260616.md` — 泄漏修复细节和代码位置

## 任务

对项目进行封装重构，目标是：
1. **修复数据泄漏**：SGDFNet 的 delta 特征泄漏（`shift(1)` 用了当日 RT）、RT916 的 history 窗口用了 actual 列（应改为 forecast 列）
2. **标准化模型目录**：每个模型统一为 `dataprocess.py` + `model.py` + `pipeline.py` 结构，只保留运行所需代码，实验产物和旧文档归档到 `_archive/`
3. **创建统一运行入口**：根目录 `main.py`，支持 `--pipeline predict/train/evaluate/fusion`，`--target dayahead/realtime/both`，`--models` 选择模型
4. **实现并行执行**：不同模型用 ProcessPoolExecutor 并行，CPU 模型（LightGBM、SGDFNet）和 GPU 模型（TimeMixer、TimesFM、RT916）分开调度

## 执行顺序

**阶段 1（可并行）**：
- 修复 SGDFNet 泄漏：`data_contract.py` 中 delta 特征改用 cutoff-safe 方案（逐小时动态 shift 或日级统计广播）
- 修复 RT916 泄漏：`dataprocess.py` 中 history 窗口改用 forecast 列，ramp 特征也改

**阶段 2（可并行）**：
- LightGBM：合并 DA/RT 的 train/infer 为统一 pipeline.py，添加 README
- TimeMixer：拆分为 dataprocess/model/pipeline，归档 outputs 和废弃文件，增加 delta-on-DA 训练模式
- TimesFM：拆分巨石脚本为 dataprocess + pipeline，文件名去掉中文
- SGDFNet/RT916：重命名文件对齐标准结构，归档废弃 config 和输出

**阶段 3**：
- 创建根目录 main.py + cli/ + configs/ + runners/ + pipelines/ + services/ + utils/
- 参照 `../epf/` 项目的架构模式
- 实现 ProcessPoolExecutor 并行调度

**阶段 4**：
- 每个模型在 2026-05 上做烟测，确认重构后结果与重构前差异 < 0.1%
- 全模型联合运行 `python main.py --pipeline predict --target both --models all --date 2026-05-01`

## 约束

- 每个模型的 `pipeline.py` 必须暴露 `ModelPipeline` 类，包含 `train()`、`predict()`、`predict_range()` 方法
- runner 输出统一为 `["时刻", "预测值"]` 列的 CSV
- 归档目录统一用 `_archive/`，不要删除任何文件
- conda 环境 `epf-2`，Python: `D:\computer_download\environment\conda\epf-2\python.exe`
- 中文路径有 GBK 问题，运行时传 `--conda-env ""` 直接用 Python
- 修复后跑 5 个代表月（2025-08, 2025-12, 2026-02, 2026-03, 2026-05），同时报 raw SMAPE 和 smape_clip50
- 结果记录到 `docs/TimeMixer冲刺日志.md`

## 卡壳时

- SGDFNet 修复太复杂 → 最小修复：delta.shift(1) 改 delta.shift(24)，训练和推理用同一套特征函数
- 模型拆分影响结果 → 先做最小改动拆分，验证结果一致后再优化
- GPU 内存不够 → 串行执行 GPU 模型，只并行 CPU 模型
- 不确定某特征是否泄漏 → 原则：D-1 15:00 时点能知道的才能用

## 使用 autonomous-research-loop skill

每完成一个阶段，用 skill 的核心循环自检：
- ASSESS：当前结果如何？
- DECIDE：继续当前方向还是 ESCAPE？
- ESCAPE 触发条件：连续 2 次改动改善 < 0.5pp
