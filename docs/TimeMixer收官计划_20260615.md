# TimeMixer 收官计划（2026-06-15 v3）

## 1. 目标

| 指标 | 硬目标 | 理想值 |
|------|--------|--------|
| DA 代表月均值 | ≤ 15% | ≤ 13% |
| RT 代表月均值 | ≤ 25% | ≤ 20% |

不达标不停。手段不限——改架构、加模块、换范式、搜 GitHub、读论文，都行。

## 2. 代表月（先跑这几个，达标后再扩）

从全部可用月份中选 5 个覆盖四季的代表月：

| 月份 | 季节特征 | 已有数据 |
|------|---------|---------|
| 2025-08 | 盛夏，高光伏+高负荷 | 无 |
| 2025-12 | 冬季，供暖负荷+低光伏 | 无 |
| 2026-03 | 春季过渡，已知最难月 | 有（RT 31%） |
| 2026-05 | 晚春，direct_24 突破月 | 有（RT 21%） |
| 2026-02 | 冬末，仅 5 天数据 | 有（RT 26%） |

**数据已确认全部可用**（`epf/data/shandong_pmos_hourly.csv` 覆盖 2022-01 至 2026-06，无缺失，训练窗充裕）。

**流程**：先在代表月上达标 → 再扩到 2025-09/10/11 和 2026-04 做全量验证。

## 3. 当前已知

### 唯一有效突破：direct_24

关掉分段训练（segment_training=false），5 月 RT 从 27.68% 降到 21.42%（-6.26pp）。这是 25+ 轮实验中唯一的多点改善。

direct_24 目前只跑过 3/5 月，其他月未验证。

### 两套未充分利用的代码资产

`enhanced_pipeline.py` 系列拥有 repro_pipeline 没有的东西：33 维特征（含 lag/holiday/solar_term/gap 特征）、EnhancedTimeMixerModel（MultiScaleMixHead + FutureGate + spike residual）。这些从未和 direct_24 组合过。

### 已排除的路线

超参微调、TimesNet backbone、frozen weights、multi-seed、VMD 分解、spike_day_affine、peak_regime_affine、normal-focus、future_residual head、DA residual_blend、增强特征集（在 segment 上试过无效，但在 direct_24 上未测试）。

完整排除清单见 `docs/TimeMixer冲刺日志.md`。

## 4. 方法论

**使用 `autonomous-research-loop` skill 作为你的工作方法论。**

核心循环：评估 → 选最大杠杆 → 实施 → 测量 → 决策 → 循环或逃逸。

### 逃逸机制（最重要）

当连续 2 次实验改善 < 0.5pp，**停下来，换方向**。不要在同一个思路上死磕。

逃逸阶梯（逐级爬升）：
1. 换特征（增强特征集、跨域特征、外部数据）
2. 换模型结构（EnhancedTimeMixerModel、PatchTST、iTransformer、混合模型）
3. 换范式（回归→分类+回归、端到端→模块化、点预测→概率预测）
4. 换数据（增强、重采样、迁移学习）
5. 换目标（DA/RT 分离、不同时段不同策略）
6. 搜 GitHub/arxiv 找全新方案

### 你可以调用的 skill

| 需求 | skill |
|------|-------|
| 系统调研技术方案 | `deep-research` |
| 搜索相关论文 | `arxiv` / `research-lit` |
| 自主参数搜索 | `dse-loop` |
| 规划实验矩阵 | `experiment-plan` |
| 发散式生成方案 | `idea-creator` |
| 精炼模糊方向 | `research-refine` |

## 5. 已知线索（供你参考，不是指令）

- direct_24 解决了"分段割裂"问题，但还没用上增强特征和增强模型
- DA 和 RT 最优结构可能不同，允许分离
- 5 月 1_8 段是已知薄弱点（37.67% vs 历史 30.67%）
- 3 月 RT 9_16 是所有模型都打不过去的硬骨头（43-48%）
- GitHub 上电价预测的前沿方向：regime-aware、two-stage、foundation model + LoRA
- 项目里有 RT916/SGDFNet/TimesFM 等模型，可以借鉴其模块

这些是线索，不是必须走的路。你自己判断什么最有可能带来多点改善。

## 6. 成功标准

| 等级 | 代表月 DA mean | 代表月 RT mean | 动作 |
|------|---------------|---------------|------|
| S | ≤ 13% | ≤ 20% | 超额达标 |
| A | ≤ 15% | ≤ 25% | 达标，扩月到全量验证 |
| B | ≤ 16% | ≤ 26% | 接近，可进入融合补齐 |
| C | > 16% | > 26% | 冻结最佳，进入融合 |

## 7. 交付物

1. 代表月完整预测产物（predictions_raw.csv / metrics_by_period.csv / run_manifest.json）
2. 最终配置记录
3. 冲刺日志追加最终结论
4. Leaderboard 更新
5. 更新 docs/TimeMixer当前冻结结论.md

## 8. 文件位置

| 资源 | 路径 |
|------|------|
| 复现链（基础版） | TimeMixer/repro_pipeline.py |
| 复现链（增强版） | TimeMixer/enhanced_pipeline.py + enhanced_config.py + enhanced_model.py |
| 骨干定义 | TimeMixer/backbones.py |
| direct_24 runner | fusion/runners/run_timemixer_direct24_batch.py |
| safe fusion runner | fusion/runners/run_timemixer_safe_fusion_batch.py |
| 历史基准 | fusion_runs/historical_monthly_benchmarks/monthly_historical_benchmarks.csv |
| 冲刺日志 | docs/TimeMixer冲刺日志.md |
| 冻结结论 | docs/TimeMixer当前冻结结论.md |
| 尖峰调研 | docs/TimeMixer尖峰调研摘要_20260615.md |
| 主数据 | epf/data/shandong_pmos_hourly.csv |
| conda 环境 | epf-2 |

## 9. 约束

1. **目标驱动**：代表月 DA ≤ 15%、RT ≤ 25%
2. **自主决策**：根据中间结果自行决定，不需要每步问
3. **快速迭代**：优先跑实验，少写文档
4. **大胆尝试**：架构、模块、范式都可以改
5. **及时换方向**：连续 2 次改善 < 0.5pp 就换
6. **代表月全跑**：最终配置必须覆盖 5 个代表月
7. **记录到冲刺日志**
8. **完成后更新冻结结论和 leaderboard**
