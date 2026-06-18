# TimeMixer 当前冻结结论

## 状态
- 状态：`best_deliverable_with_gap`
- 目标：先修复复现链，再冲刺历史对标值与架构突破目标。
- 当前最高优先级：若立即交付，交付 `safe_rt9_16_fusion`；若继续冲分，优先继续处理 `2026-03 RT 9_16`。

## 真源
- 复现专项计划：`D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0\docs\模型复现专项计划.md`
- 架构级突破计划：`D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0\docs\TimeMixer架构级突破计划.md`
- 历史总表：`D:\作业\science\大创科研时序\代码\elec\outputs\reports\模型结果汇总.xlsx`

## 当前默认协议
- 任务粒度：按月训练一次，整月逐日滚动推理。
- 训练窗：默认 `12` 个月。
- 验证切分：训练天序列尾部 `20%`。
- 默认训练范式：`segment_training = true`，按 `1_8 / 9_16 / 17_24` 分段独立训练再拼接。
- 默认 target mode：`direct`
- 实时 cutoff：`D-1 15:00`
- 日前 cutoff：默认与实时一致，统一 `D-1 15:00`
- 评估口径：`clip50 sMAPE`，按 [docs/metrics_calculation.md](/C:/Users/37813/.codex/worktrees/b03b/electricity_forecast_model2.0/docs/metrics_calculation.md)

## 当前默认数据
- 主数据：`D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\epf\data\shandong_pmos_hourly.csv`
- 编码：优先 `gbk`，失败后回退 `utf-8-sig`、`utf-8`

## 当前工程入口
- 正式 TimeMixer runner：`fusion/runners/run_timemixer_export.py`
- 2 月单模型审计：`run_feb_single_model_audit.py`
- 兼容主入口：`TimeMixer/pipeline_timemixer.py`

## 历史对标值
- 日前 overall `17.550921`
- 实时 overall `26.039687`

## 当前冻结主链
- 运行窗口：`2026-02-24 ~ 2026-06-01`
- pipeline mode：`historical_joint`
- backbone：`timemixer`
- segment training：`true`
- target mode：`direct`
- rt loss mode：`risk_peak_weighted`
- rt risk profile：`baseline`
- rt peak weight multiplier：`1.4`
- rt calibration mode：`rt_916_regime_affine`
- regime 阈值：
  - `solar_ratio >= 0.28`
  - 或 `bidding_ratio <= 0.08`
  - 或 `bidding_space <= 4000`
- peak-like 训练权重条件：
  - 非 stress
  - `DA >= 300`
  - `bidding_space >= 22000`
  - `solar_ratio <= 0.22`
- 2 月冻结结果：
  - `DA overall = 19.195740839458153`
  - `RT overall = 26.346731519397495`
  - `RT 9_16 = 31.210932007725437`
  - `RT 17_24 = 20.095605178100507`
- 输出目录：
  - `fusion_runs/timemixer_truth_window_peakloss/2026-02_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine`

## 跨月验证
- `2026-02`
  - `RT overall: 26.7282 -> 26.3467`
  - `RT 9_16: 32.7626 -> 31.2109`
- `2026-03`
  - `RT overall: 32.1861 -> 31.6930`
  - `RT 9_16: 46.3822 -> 44.9808`
- `2026-04`
  - `RT overall: 25.3170 -> 25.3976`
  - `RT 9_16: 25.4765 -> 25.2795`
- 三个月均值：
  - `risk_hour_weighted`
    - `RT overall mean = 28.0771`
    - `RT 9_16 mean = 34.8738`
  - `risk_peak_weighted`
    - `RT overall mean = 27.8125`
    - `RT 9_16 mean = 33.8238`

## 当前判断
- 当前 TimeMixer 单链已经从明显失真恢复到接近历史真源区间。
- 当前最有效的进一步改进来自：
  - `risk_peak_weighted` 训练
  - `RT 9_16` 的 leakage-safe regime 仿射修正
- 当前距离历史对标：
  - `DA`: `19.20 vs 17.55`，仍需继续压
  - `RT`: `26.35 vs 26.04`，已经更接近
- 扩月判断：
  - `2026-03` 仍主要卡在 `RT 9_16`
  - `risk_peak_weighted` 在 `2026-02/03` 明显改善
  - `2026-04 overall` 有极小回退，但 `RT 9_16` 与 `RT 17_24` 仍改善
  - 从三个月均值看，`risk_peak_weighted` 已优于 `risk_hour_weighted`

## 当前对照结论
- 真源窗口、`historical_joint`、`direct`、`risk_hour_weighted(baseline)`、无 RT 仿射修正：`DA≈19.20 / RT≈27.79`
- 真源窗口、`historical_joint`、`direct`、`risk_hour_weighted(baseline)` + `rt_916_affine`：`DA≈19.20 / RT≈27.51`
- 真源窗口、`historical_joint`、`direct`、普通 `L1`：`DA≈20.98 / RT≈28.27`
- 真源窗口、`historical_joint`、`direct`、`risk_peak_weighted(baseline)` + `rt_916_regime_affine`：
  - `2026-02`: `RT≈26.35`
  - `2026-03`: `RT≈31.69`
  - `2026-04`: `RT≈25.40`
- 整月 `2026-02`、`single_task`、整日 24 点：`DA≈34.23 / RT≈42.26`
- 整月 `2026-02`、`single_task`、分段训练：`DA≈28.83 / RT≈37.26`
- 真源窗口、`historical_joint`、`TimesNet` backbone：已生成对照，暂未优于当前 best
- 真源窗口、`historical_joint`、增强特征 + `TimeMixer`：已生成对照，整体回退
- 真源窗口、`historical_joint`、`residual_blend`：`DA` 小幅变好，但 `RT` 明显变差
- 真源窗口、`historical_joint`、`segment_bias calibration`：`DA` 接近历史值，但 `RT` 退化到约 `29.98`
- 真源窗口、`historical_joint`、`DA=segment_bias + RT=hour_bias`：未解决 RT 结构性误差
- 真源窗口、`historical_joint`、`risk_hour_weighted + rt segment_bias_shrink(0.35)`：接近 best 但未胜出
- 真源窗口、`historical_joint`、`risk_hour_weighted + peak_focus`：未胜出
- 真源窗口、`historical_joint`、`risk_hour_weighted + solar_focus`：未胜出

## auto 选择说明
- 已新增 `rt_calibration_mode = rt_916_auto`
- 会在验证集上自动比较：
  - `none`
  - `rt_916_affine`
  - `rt_916_regime_affine`
- 当前 `2026-02` 与 `2026-03` 的 auto 都自动选择了 `rt_916_regime_affine`
- 因此当前推荐默认仍是固定 `rt_916_regime_affine`，`rt_916_auto` 作为更稳妥的工程入口保留
- `run_manifest.json` 已补齐：
  - `da_segment_bias`
  - `rt_segment_bias`
  - `rt_segment_affine`
  - auto 模式下的 `selected_mode` 与验证候选分数

## 3 月专项补充
- `2026-03` 的主要残余问题已定位为：
  - `RT 9_16`
  - 尤其是 normal bucket 内部的高价尖峰低估
- 已验证但当前未胜出的路线：
  - `rt_916_peak_regime_affine`
  - `rt_916_peak_regime_bias`
- 结论：
  - `peak` 路线证明诊断方向是对的
  - 但当前后校准实现仍不如 `rt_916_regime_affine`
  - 因此不替换当前冻结主链

## 已冻结排除项
- 不先做融合
- 不先做 `hidden_dim`、`dropout`、`scheduler` 微调
- 不混用 `single_task` 与 `historical_joint` 结果
- 不再把“整月 2026-02”当作唯一对标口径，历史总表对齐优先使用 `2026-02-24 ~ 2026-06-01` 真源窗口
- `rt_916_spike_day_affine`：排除
  - `2026-03` 有极小改善
  - 但明显伤害 `2026-02` 与 `2026-04`
  - 三个月 `RT overall mean` 从 `27.4541` 恶化到 `28.6763`
- `rt_916_peak_regime_affine`：排除
- `rt_916_peak_regime_bias`：保留为思路正确但当前未胜出的备选
- `DA-only segment_bias calibration`：能压 DA，但会伤当前 RT 主链，不并入冻结主链
- `DA-only segment_bias_shrink calibration`：同样能压 DA，但仍有 RT 代价，不并入冻结主链
- `DA residual_blend + RT direct`：在当前正式复现链中明确失败，不再继续

## 下一优先级
- 第一优先：若继续打 RT，优先尝试 `gate-controlled safe fusion`
- 第二优先：若继续深挖单模，优先考虑更独立的 `9_16` 分支，而不是继续做 affine 阈值细抠
- 第三优先：做更长区间汇总验证，再决定是否进入更大融合框架
## DA 当前候选增量
- 候选路线：`da_loss_mode = asymmetric_under`
- 其余配置保持当前 RT 冻结主链不变：
  - `rt_loss_mode = risk_peak_weighted`
  - `rt_calibration_mode = rt_916_regime_affine`
  - `da_under_weight_multiplier = 1.25`
- `2026-02` 首轮结果：
  - `DA overall: 19.1957 -> 19.1058`
  - `RT overall: 26.3467 -> 26.2201`
  - `DA 9_16: 23.7730 -> 22.6403`
  - `DA 17_24: 15.5368 -> 16.6008`
- 当前状态：
  - 尚未并入冻结主链
  - 原因：还需要 `2026-03/2026-04` 扩月验证，确认不是仅 2 月窗口有效
## 当前升级候选主链
- 在原 `risk_peak_weighted + rt_916_regime_affine` 主链上加入：
  - `da_loss_mode = asymmetric_under`
  - `da_under_weight_multiplier = 1.25`
  - 仅 `DA 9_16` 分段启用 hour weights，其余 DA 分段只保留 under-prediction 惩罚
- 对应输出：
  - `fusion_runs/timemixer_truth_window_da_asym_segaware/2026-02_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine`
  - `fusion_runs/timemixer_truth_window_da_asym_segaware/2026-03_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine`
  - `fusion_runs/timemixer_truth_window_da_asym_segaware/2026-04_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine`
- 三个月对旧冻结主链的 overall 均值对比：
  - `DA: 18.2957 -> 17.7305`
  - `RT: 27.8125 -> 27.4541`
- 当前判断：
  - 这版已经优于旧冻结主链
  - 建议把它作为新的默认候选主链继续向后冲
  - 现阶段主要剩余硬骨头是 `2026-03 RT 9_16`

## 当前最强兜底方案
- 方案名：`safe_rt9_16_fusion`
- 规则：
  - `DA` 全部使用当前 seg-aware 单模冻结主链
  - `RT 1_8 / 17_24` 使用当前 seg-aware 单模冻结主链
  - 仅 `RT 9_16` 使用 `rt916splitbackbone` 输出
- 输出目录：
  - `fusion_runs/timemixer_safe_fusion/2026-02_safe_rt9_16_fusion`
  - `fusion_runs/timemixer_safe_fusion/2026-03_safe_rt9_16_fusion`
  - `fusion_runs/timemixer_safe_fusion/2026-04_safe_rt9_16_fusion`
- 三个月均值对当前单模冻结主链：
  - `DA overall mean: 17.7305 -> 17.7305`
  - `RT overall mean: 27.4541 -> 27.2947`
- 当前判断：
  - 这是当前最强且 leakage-safe 的可落地兜底版
  - 若必须交付更稳结果，应优先交付该 safe fusion，而非只交付单模
  - 当前已工程化完成：
    - `fusion/runners/run_timemixer_safe_fusion.py`
    - `fusion/runners/run_timemixer_safe_fusion_batch.py`
    - 标准导出齐全：`predictions_raw.csv / metrics_by_period.csv / protocol_audit.csv / run_manifest.json / 图表`
    - 已支持 leaderboard 记录
    - 已支持批量月份统一运行与汇总：
      - `fusion_runs/timemixer_safe_fusion_batch/monthly_summary.csv`
      - `fusion_runs/timemixer_safe_fusion_batch/aggregate_summary.csv`

## 最终验收结论
- 协议侧：已恢复正式无泄漏复现链，关键边界为 `D-1 15:00`，并保留标准审计产物。
- 结果侧：已从故障期追回到历史可解释区间，但尚未完全达到冲刺目标。
- 单模型最佳三个月均值：
  - `DA overall mean = 17.7305`
  - `RT overall mean = 27.4541`
- safe fusion 最佳三个月均值：
  - `DA overall mean = 17.7305`
  - `RT overall mean = 27.2947`
- 对历史对标：
  - `DA: 17.7305 vs 17.550921`
  - `RT: 27.2947 vs 26.039687`
- 正式判定：
  - 可以交付
  - 但应明确标注为“当前最佳可交付版本，仍有 RT 目标缺口”
- 详细验收见：
  - [docs/TimeMixer最终验收审计.md](/C:/Users/37813/.codex/worktrees/b03b/electricity_forecast_model2.0/docs/TimeMixer最终验收审计.md)
## 已排除路线
- `rt_916_regime_affine_hourbias`
  - 只对 `2026-03 RT 9_16` 有极小改善
  - 明显伤害 `2026-02` 和整体 `RT overall`
  - 不进入冻结主链
- `RT normal-focus` 训练加权
  - 对 `2026-03/04 RT 9_16` 有局部改善
  - 明显伤害 `RT 17_24` 与整体 `RT overall`
  - 不进入冻结主链
- `rt_segment_head_mode = future_residual`
  - 三个月 `RT overall` 与 `RT 9_16` 均回退
  - 不进入冻结主链
- `rt_916_backbone = timesnet`
  - `RT 9_16` 有局部改善
  - 但 `2026-03 RT overall` 与 `RT 17_24` 副作用明显
  - 不进入冻结主链
