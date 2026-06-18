先读 `docs/Codex执行文档_泄漏修复与TimeMixer互补_20260616.md`，那是完整的审计结果和修复方案。

## 目标

修复项目里所有模型的数据泄漏，然后在 5 个代表月（2025-08、2025-12、2026-02、2026-03、2026-05）上跑出可信结果，同时借鉴 SGDFNet 的架构优势增强 TimeMixer。

最终交付：5 代表月全模型统一 SMAPE 对比表 + TimeMixer 增强版（delta-on-DA）结果。

## 背景

审计发现 SGDFNet 和 RT916 都有数据泄漏（详见文档第一/三部分）。LightGBM 和 TimesFM 是 1.0 基准，无泄漏，不用动。

协议规则：D 日的**预测值**（负荷/光伏/风电/联络线等 forecast 列）在 D-1 就公布了，用作特征完全合法。唯一不合法的是用了 D 日的 **RT 实际价格**。

## 方向

**阶段 1**：修 SGDFNet（delta 特征泄漏）→ 5 代表月重跑
**阶段 2**：修 RT916（history 窗口 actual→forecast）→ 5 代表月重跑
**阶段 3**：TimeMixer 增加 delta-on-DA 训练模式 → 先在 2026-05 烟测 → 有效则扩到 5 月
**阶段 4**：全模型公平对比 + 融合设计

每阶段跑完给我对比表，我看结果再定下一步。

## 卡壳时怎么办

- SGDFNet 修复太复杂 → 最小修复：`delta.shift(1)` 改 `delta.shift(24)`，训练/推理对齐用同一特征函数
- RT916 改 forecast 后性能暴跌 → 正常，记录真实水平，不要回退
- TimeMixer delta-on-DA 没改善 → 放弃这条线，试 DA 交互特征（DA×净负荷、DA×光伏）或 delta 历史特征
- 连续 2 次改善 < 0.5pp → 停当前方向，换方向或冻结结果
- 不确定某个特征是否泄漏 → 原则：在 D-1 15:00 这个时点，你能知道什么就用什么

## 约束

1. 目标驱动，自主决策
2. 快速迭代，优先跑实验
3. 5 个代表月全跑，不能只跑部分
4. 统一口径：smape_clip50（floor-50）
5. 记录到 `docs/TimeMixer冲刺日志.md`
6. 用 conda 环境 epf-2（Python: `D:\computer_download\environment\conda\epf-2\python.exe`）
7. 中文路径有 GBK 问题，传 `--conda-env ""` 跳过 conda run 直接用 Python 可执行文件
