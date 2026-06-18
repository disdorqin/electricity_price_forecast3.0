# TimeMixer 冲刺日志

## 2026-06-15

### 真源固定

- 复现主协议：`D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0\docs\模型复现专项计划.md`
- 架构突破协议：`D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0\docs\TimeMixer架构级突破计划.md`
- 历史总表：`D:\作业\science\大创科研时序\代码\elec\outputs\reports\模型结果汇总.xlsx`

### 历史对标值

- 日前 `TimeMixer`：
  - `SMAPE overall = 17.550921`
  - `SMAPE 1_8 = 18.079511`
  - `SMAPE 9_16 = 19.581885`
  - `SMAPE 17_24 = 14.991367`
- 实时 `TimeMixer`：
  - `SMAPE overall = 26.039687`
  - `SMAPE 1_8 = 24.301097`
  - `SMAPE 9_16 = 31.069848`
  - `SMAPE 17_24 = 22.748115`

### 当前审计结论

- 当前 worktree 只有 `TimeMixer/pipeline_timemixer.py` 单文件脚本。
- 计划中提到的 `fusion`、`enhanced_*`、`leaderboard`、审计脚本在当前 worktree 不存在，需要补建。
- 原脚本可以通过语法编译，但工程形态脆弱，缺少：
  - 月度复现入口
  - `single_task` / `historical_joint` 隔离
  - manifest
  - 审计输出
  - leaderboard 追踪
- 原脚本的日前 cutoff 默认是 `D-1 23:59:59`，不符合当前统一的 `D-1 15:00` 无泄漏口径。

### 当日实施目标

1. 补建双文档、正式 runner、审计与 manifest 输出。
2. 用 `epf\data\shandong_pmos_hourly.csv` 建立 2026-02 月度复现链。
3. 先追回到历史可解释区间，再考虑 `TimesNet` 风格 backbone。

### 下一步

- 跑首个 2 月 smoke/audit。
- 记录当前 `single_task` 基线。
- 若偏差仍大，优先检查 cutoff、训练窗、未来特征和 DA->RT 注入。

## 2026-06-15 当前进展补充

### 已完成的工程落地

- 新建正式复现链：
  - `TimeMixer/repro_pipeline.py`
  - `TimeMixer/backbones.py`
  - `fusion/runners/run_timemixer_export.py`
  - `run_feb_single_model_audit.py`
- `TimeMixer/pipeline_timemixer.py` 已改为兼容入口，实际逻辑转发到正式复现链。
- 已固定导出：
  - `predictions_raw.csv`
  - `metrics_by_period.csv`
  - `protocol_audit.csv`
  - `run_manifest.json`
  - 预测图
  - `TimeMixer/outputs_v2/serial_keepdrop/leaderboard.csv`

### 当前实验结论

- 整月 2026-02、整日 24 点训练：
  - `DA ≈ 34.23`
  - `RT ≈ 42.26`
  - 明显失真
- 整月 2026-02、分段 8 小时训练：
  - `DA ≈ 28.83`
  - `RT ≈ 37.26`
  - 说明分段训练是有效修复方向
- 真源窗口 `2026-02-24 ~ 2026-06-01`、分段训练、TimeMixer backbone：
  - `DA = 20.9839`
  - `RT = 28.2748`
  - 已显著接近历史总表 `DA 17.55 / RT 26.04`
- 真源窗口 `2026-02-24 ~ 2026-06-01`、分段训练、TimesNet backbone：
  - 已跑通，但当前未证明比 TimeMixer backbone 更强

### 当前判断

- 造成历史失真的主因不是单一 cutoff 开关，也不是纯粹入口路径问题。
- 最关键修复是：
  - 对齐真源测试窗口
  - 采用分段训练再拼接
  - 固化正式复现链与审计输出
- 当前已经从“协议级失真”进入“最后 2-3 个点优化”阶段。

### 下一步

1. 在真源窗口 + 分段训练基线上补更贴近专项文档的特征。
2. 优先压 `9_16` 和 `17_24`。
3. 若特征增强不足，再继续强化 backbone 或补 segment head。

## 2026-06-15 特征/骨干第二轮结论

### 对照结果

- 真源窗口、分段训练、`TimeMixer` backbone：
  - `DA = 20.9839`
  - `RT = 28.2748`
- 真源窗口、分段训练、`TimesNet` backbone：
  - `DA = 21.0701`
  - `RT = 29.1939`
- 真源窗口、分段训练、增强特征 + `TimeMixer` backbone：
  - `DA = 21.7631`
  - `RT = 30.4170`

### 当前结论

- 当前最好仍然是：
  - 真源窗口
  - `historical_joint`
  - `segment_training=true`
  - `TimeMixer` backbone
- 简单增加动量/滚动统计/分段标记特征没有继续提升整体分数，说明当前差距不再主要来自“少几个普通特征”。
- `TimesNet` 也没有直接替换成功，因此短期内不应默认切换 backbone。

### 下一步

1. 继续保留当前 best run 作为冻结基线。
2. 下一轮优先考虑更强的 segment-specific head 或更贴近历史明细的训练策略。
3. 若需要继续冲刺，再围绕 `9_16` 和 `17_24` 做更定向的结构改造，而不是泛加特征。

## 2026-06-15 残差学习结论

### 实验

- 真源窗口、分段训练、`historical_joint`
- `target_mode = residual_blend`
- 输出目录：`fusion_runs/timemixer_truth_window_residual/2026-02_historical_joint_timemixer`

### 结果

- `DA = 20.4845`
- `RT = 33.0452`

### 结论

- 残差学习对 `DA` 有小幅帮助。
- 但它明显伤害了 `RT`，尤其是 `9_16`。
- 因此它不能替换当前最佳默认链。
- 默认 `target_mode` 已恢复为 `direct`，残差模式仅保留为可选对照。

## 2026-06-15 后校准结论

### 实验

- 真源窗口、分段训练、`historical_joint`
- `target_mode = direct`
- `calibration_mode = segment_bias`
- 输出目录：`fusion_runs/timemixer_truth_window_calib/2026-02_historical_joint_timemixer`

### 结果

- `DA = 18.6098`
- `RT = 29.9770`

### 结论

- 轻量验证集后校准对 `DA` 很有效，已经逼近历史对标值。
- 但它对 `RT` 仍有伤害，暂时不能作为 DA/RT 共用默认链。
- 这说明“DA 与 RT 可能需要不同的后处理策略”，后续如果继续冲刺，可以考虑只给 `DA` 开校准，而 `RT` 保持未校准 best。

## 2026-06-15 DA/RT 分离校准结论

### 实验

- `DA = segment_bias`
- `RT = hour_bias`
- 真源窗口、分段训练、`historical_joint`
- 输出目录：`fusion_runs/timemixer_truth_window_combo_calib/2026-02_historical_joint_timemixer`

### 结果

- `DA = 18.6098`
- `RT = 29.9770`

### 结论

- 在当前分段独立训练实现下，`RT hour_bias` 没有明显优于 `RT segment_bias`。
- 这说明当前 `RT` 问题已经不再是简单稳定偏差，而更像结构性误差或 hard regime 建模不足。
- 结论上可以保留：
  - `DA` 推荐用后校准版本
  - `RT` 推荐保留未校准 best

## 2026-06-15 RT 风险小时加权结论

### 实验

- 真源窗口、分段训练、`historical_joint`
- `target_mode = direct`
- `rt_loss_mode = risk_hour_weighted`
- 输出目录：`fusion_runs/timemixer_truth_window_rtloss/2026-02_historical_joint_timemixer`

### 结果

- `DA = 19.1957`
- `RT = 27.7946`
- `RT 17_24 = 20.7642`

### 结论

- 这是目前最好的“统一单链”结果。
- 相比此前未加权 best：
  - `DA: 20.98 -> 19.20`
  - `RT: 28.27 -> 27.79`
- 说明当前最有效的方向不是后校准，而是直接在 `RT` 训练目标中提升 hard hour 权重。

## 2026-06-15 RT 收缩校准结论

### 实验

- 基线：`rt_loss_mode = risk_hour_weighted`
- 增量：`rt_calibration_mode = segment_bias_shrink`
- `calibration_shrink = 0.35`
- 输出目录：`fusion_runs/timemixer_truth_window_rtloss_shrinkcal/2026-02_historical_joint_timemixer`

### 结果

- `DA = 19.1957`
- `RT = 28.0897`

### 结论

- 收缩校准比“完整 segment_bias”稳定得多，说明这条方向不是完全没用。
- 但它仍然没有超过当前统一单链 best 的 `RT = 27.7946`。
- 因此当前把它记为“近似有效但未胜出”的备选路线，不替换 best。

## 2026-06-15 RT 风险权重 profile 结论

### 实验

- 基线：`rt_loss_mode = risk_hour_weighted`
- 对照 profile：
  - `baseline`
  - `solar_focus`
  - `peak_focus`

### 结果

- `baseline`: `RT = 27.7946`
- `peak_focus`: `RT = 28.2933`
- `solar_focus`: `RT = 28.8490`

### 结论

- 当前最优 profile 仍然是默认 `baseline`。
- `peak_focus` 和 `solar_focus` 都没有超过当前 best。
- 说明这条线的主要增益已经被默认权重拿到，后续不再优先继续手工调权重 profile。

## 2026-06-15 RT 9_16 仿射修正结论

### 实验

- 基线：`rt_loss_mode = risk_hour_weighted`, `rt_risk_profile = baseline`
- 增量：`rt_calibration_mode = rt_916_affine`
- 仅对 `RT 9_16` 段做验证集拟合的仿射修正
- 输出目录：`fusion_runs/timemixer_truth_window_rt916affine/2026-02_historical_joint_timemixer_risk_hour_weighted_rt-rt_916_affine`

### 结果

- `DA = 19.1957`
- `RT = 27.5128`
- `RT 9_16 = 34.4719`
- `RT 17_24 = 20.7642`

### 结论

- 这是当前最好的统一单链结果。
- 相比此前 best：
  - `RT: 27.7946 -> 27.5128`
- 说明当前最有效的进一步优化，不是全局后校准，而是只对 `RT 9_16` 做轻量结构化修正。

## 2026-06-15 RT 9_16 regime 仿射修正结论

### 观察

- 对当前 best `risk_hour_weighted + rt_916_affine` 明细复盘后，`RT 9_16` 的主要残余误差不是整段统一偏差。
- 高误差主要集中在可由当日已知 future 特征识别的 stress regime：
  - `solar/load` 很高
  - `bidding_space/load` 很低
  - 或 `bidding_space` 极低
- 这类 regime 下出现了大量中午低价/负价与 16 点反转失真，继续用整段单一 affine 已经不够细。

### 实施

- 在 `TimeMixer/repro_pipeline.py` 与 `fusion/runners/run_timemixer_export.py` 中新增：
  - `rt_calibration_mode = rt_916_regime_affine`
  - 仅对 `RT 9_16` 使用两桶 leakage-safe affine
- regime 划分仅使用预测日已知 future 特征，不使用任何未来真值：
  - `solar_ratio >= 0.28`
  - 或 `bidding_ratio <= 0.08`
  - 或 `bidding_space <= 4000`
- 训练与协议保持不变：
  - `historical_joint`
  - `segment_training = true`
  - `target_mode = direct`
  - `rt_loss_mode = risk_hour_weighted`
  - `rt_risk_profile = baseline`
  - `cutoff = D-1 15:00`

### 结果

- 输出目录：
  - `fusion_runs/timemixer_truth_window_rt916regime/2026-02_historical_joint_timemixer_risk_hour_weighted_rt-rt_916_regime_affine`
- 指标：
  - `DA overall = 19.195740839458153`
  - `RT overall = 26.728181769008458`
  - `RT 9_16 = 32.762592587481656`
  - `RT 17_24 = 20.764154682442342`

### 结论

- 这次是当前最佳统一单链结果，优于此前 `rt_916_affine` best：
  - `RT overall: 27.5128 -> 26.7282`
  - `RT 9_16: 34.4719 -> 32.7626`
- 提升主要来自对 `9_16` 内部 hard regime 的更细分修正，而不是继续调 loss profile 或全局 bias。
- 当前距离历史对标 `RT 26.039687` 只差约 `0.69`，RT 已明显逼近目标线。

### 下一步

1. 继续保留当前 `rt_916_regime_affine` 为冻结 best。
2. 若继续压 RT，优先围绕 `9_16` 的 16 点反转与 normal bucket 残差做更细但保守的结构修正。
3. 下一阶段可以开始评估 `2026-03/2026-04` 扩月稳定性，同时寻找不伤 RT 的 DA 改进。

## 2026-06-15 扩月验证与 auto 选择结论

### 扩月验证

- 按冻结 best 配置直接扩到：
  - `2026-03_historical_joint_timemixer_risk_hour_weighted_rt-rt_916_regime_affine`
  - `2026-04_historical_joint_timemixer_risk_hour_weighted_rt-rt_916_regime_affine`
- 结果：
  - `2026-03`: `DA = 18.2984`, `RT = 32.1861`, `RT 9_16 = 46.3822`
  - `2026-04`: `DA = 17.3931`, `RT = 25.3170`, `RT 9_16 = 25.4765`

### 扩月判断

- `2026-04` 表现稳定且很强，说明当前链路不是普遍失效。
- `2026-03` 主要崩在 `RT 9_16`，不是全链路崩溃，而是特定月份的中午 regime 更难。
- 三个月均值：
  - `DA overall mean = 18.2957`
  - `RT overall mean = 28.0771`

### 3 月对照消融

- 对 `2026-03` 额外做了：
  - `risk_hour_weighted + none`
  - `risk_hour_weighted + rt_916_affine`
  - `risk_hour_weighted + rt_916_regime_affine`
- 结果：
  - `none`: `RT = 34.9696`, `RT 9_16 = 52.0926`
  - `rt_916_affine`: `RT = 34.3261`, `RT 9_16 = 51.2965`
  - `rt_916_regime_affine`: `RT = 32.1861`, `RT 9_16 = 46.3822`

### 结论

- `regime_affine` 在 3 月虽然不够理想，但依然显著优于 `none` 和旧的整段 `affine`。
- 因此 3 月问题并不是“新校准过拟合导致更差”，而是 3 月 `RT 9_16` 本身存在更强分布漂移。

### auto 选择实验

- 新增 `rt_calibration_mode = rt_916_auto`：
  - 候选只在验证集内比较 `none / rt_916_affine / rt_916_regime_affine`
  - 再把验证最优者应用到测试，保持无泄漏
- 同时修复 manifest 追溯链：
  - `run_manifest.json` 现在会记录
    - `da_segment_bias`
    - `rt_segment_bias`
    - `rt_segment_affine`
    - auto 模式下的 `selected_mode` 与各候选 `scores`

### auto 结果

- `2026-02` auto 选择到：`rt_916_regime_affine`
- `2026-03` auto 选择到：`rt_916_regime_affine`
- 自动选择结果与固定 `regime_affine` 指标完全一致，说明：
  - 选择逻辑是正常的
  - 当前验证集也明确支持 `regime_affine`
  - 3 月问题仍然来自任务本身难度，而不是校准器被错选

### 下一步

1. 当前推荐默认链仍然是 `risk_hour_weighted + rt_916_regime_affine`。
2. `rt_916_auto` 可以保留为工程上的稳妥入口，因为它现在已经具备完整 manifest 追溯能力。
3. 后续若继续冲 RT，优先改 `2026-03` 的 `RT 9_16` 结构问题，尤其是 16 点与 normal bucket，而不是回到全局 bias 或纯超参微调。

## 2026-06-15 3 月 RT 9_16 结构诊断与 peak 路线结论

### 结构诊断

- 对 `2026-03` 的当前 best `rt_916_regime_affine` 做了细拆。
- 关键发现：
  - 最差的不只是 `stress` bucket。
  - `normal bucket` 在 `9-14` 点存在系统性低估高价尖峰。
  - `stress bucket` 主要问题仍在 `16` 点偏高。
- `normal bucket` 内部最差样本通常满足：
  - `DA` 较高
  - `bidding_space` 较高
  - `solar_ratio` 较低
- 这说明 `stress / normal` 两桶仍然太粗，normal 内部混入了“高价尖峰 regime”。

### 第一次尝试：`rt_916_peak_regime_affine`

- 实施：
  - 在 `stress` 之外再切出 `peak` 桶
  - 条件默认：
    - `DA >= 300`
    - `bidding_space >= 22000`
    - `solar_ratio <= 0.22`
  - 对 `stress / peak / normal` 三桶分别做 affine
- 结果：
  - `2026-02`: `RT 26.7282 -> 27.4224`，变差
  - `2026-03`: `RT 32.1861 -> 33.6370`，变差
- 结论：
  - 方向判断对，但 `peak` 桶直接做 affine 过度修正，导致整体抬过头。

### 第二次尝试：`rt_916_peak_regime_bias`

- 实施：
  - 保留 `stress / peak / normal` 三桶识别
  - 仅对 `peak` 桶做保守 bias 修正
  - `calibration_shrink = 0.35`
- 结果：
  - `2026-02`: `RT = 26.7698`
  - `2026-03`: `RT = 32.7246`
- 对比：
  - 明显好于激进的 `peak_regime_affine`
  - 但仍劣于当前 best `rt_916_regime_affine`

### 结论

- `peak` 路线有诊断价值，证明了 3 月的核心难点确实在 normal 内部的高价尖峰。
- 但当前两种实现都未能超越：
  - `rt_916_regime_affine`
- 因此当前冻结结论保持不变：
  - 默认 best 仍是 `risk_hour_weighted + rt_916_regime_affine`
- 新增结论：
  - `peak_regime_affine`：排除
  - `peak_regime_bias`：保留为“思路正确但当前未胜出”的备选

### 下一步

1. 如果继续打 `2026-03 RT 9_16`，优先考虑把 `peak` 判断直接前移到训练目标或样本加权，而不是继续做更复杂的后校准。
2. 另一条值得试的路线是让 `9_16` 段模型显式利用更强的 `DA / bidding_space` 尖峰信号，而不是只靠后处理补救。

## 2026-06-15 训练侧 peak 权重结论

### 动机

- 后校准侧的 `peak_regime_affine / peak_regime_bias` 都未能超越当前 best。
- 因此前移到训练侧，直接让 `RT 9_16` 对 peak-like 样本更敏感。

### 实施

- 在 `train_model()` 中新增：
  - `rt_loss_mode = risk_peak_weighted`
- 机制：
  - 保留原有 `risk_hour_weighted` 的小时权重
  - 再叠加基于 future 已知特征的 peak 样本权重
- peak-like 条件沿用前一轮诊断：
  - 非 stress
  - `DA >= 300`
  - `bidding_space >= 22000`
  - `solar_ratio <= 0.22`
- 当前测试权重：
  - `rt_peak_weight_multiplier = 1.4`
- 仍然保持：
  - `historical_joint`
  - `segment_training = true`
  - `target_mode = direct`
  - `rt_916_regime_affine`
  - `D-1 15:00`

### 结果

- `2026-02`：
  - 旧：`RT overall = 26.7282`, `RT 9_16 = 32.7626`, `RT 17_24 = 20.7642`
  - 新：`RT overall = 26.3467`, `RT 9_16 = 31.2109`, `RT 17_24 = 20.0956`
- `2026-03`：
  - 旧：`RT overall = 32.1861`, `RT 9_16 = 46.3822`, `RT 17_24 = 19.9867`
  - 新：`RT overall = 31.6930`, `RT 9_16 = 44.9808`, `RT 17_24 = 22.3860`

### 判断

- 这是当前第一次在训练侧拿到“2 月和 3 月同时改善 `RT overall / RT 9_16`”的结果。
- 说明前一轮的诊断是对的：
  - 问题不只是后校准形式
  - 训练时就应该更重视 peak-like 样本
- 代价：
  - `2026-03` 的 `RT 17_24` 有所回退
- 但总体上：
  - `RT overall` 仍优于此前 best
  - `RT 9_16` 也明显下降

### 结论

- `risk_peak_weighted + rt_916_regime_affine` 是当前更强的 RT 候选主链。
- 它比单纯 `risk_hour_weighted` 更接近“针对 3 月硬问题做结构修复”，而不是只在 2 月窗口好看。

### 下一步

1. 当前应把 `risk_peak_weighted` 视为新的默认 RT 候选，继续扩到 `2026-04` 验证有没有副作用。
2. 若 `2026-04` 不崩，则可以升级冻结默认链。
3. 之后再决定是否继续回头追 DA。

## 2026-06-15 DA-only calibration 结论

### 实施

- 在新的 `risk_peak_weighted + rt_916_regime_affine` 主链上，单独测试：
  - `da_calibration_mode = segment_bias`
  - `da_calibration_mode = segment_bias_shrink`
- 目的：
  - 只压 `DA`
  - 不动当前 RT 主链

### 结果

- `2026-02`
  - `segment_bias`: `DA 19.1957 -> 18.6098`, `RT 26.3467 -> 26.6732`
  - `segment_bias_shrink`: `DA 19.1957 -> 18.8644`, `RT 26.3467 -> 26.4368`
- `2026-03`
  - `segment_bias_shrink`: `DA 18.2984 -> 17.8991`, `RT 31.6930 -> 31.8043`

### 判断

- DA-only calibration 确实能继续压 `DA`。
- 但只要 DA 下降得明显，RT 就会被带坏一些。
- 因此这条路不能并入当前冻结主链。

### 结论

- `DA-only calibration`：排除为当前主链
- `risk_peak_weighted + rt_916_regime_affine`：继续作为当前 RT 主链
- 若后续继续追 DA，优先考虑：
  - DA 专属结构
  - 或者后续融合阶段再单独处理

## 2026-06-15 peak 权重 4 月验证结论

### 4 月结果

- `2026-04`：
  - 旧 `risk_hour_weighted`: `RT overall = 25.3170`, `RT 9_16 = 25.4765`, `RT 17_24 = 24.4414`
  - 新 `risk_peak_weighted`: `RT overall = 25.3976`, `RT 9_16 = 25.2795`, `RT 17_24 = 23.7940`

### 跨月汇总

- `2026-02`
  - `RT overall`: `26.7282 -> 26.3467`
  - `RT 9_16`: `32.7626 -> 31.2109`
- `2026-03`
  - `RT overall`: `32.1861 -> 31.6930`
  - `RT 9_16`: `46.3822 -> 44.9808`
- `2026-04`
  - `RT overall`: `25.3170 -> 25.3976`
  - `RT 9_16`: `25.4765 -> 25.2795`

### 三个月均值

- `risk_hour_weighted`
  - `RT overall mean = 28.0771`
  - `RT 9_16 mean = 34.8738`
- `risk_peak_weighted`
  - `RT overall mean = 27.8125`
  - `RT 9_16 mean = 33.8238`

### 判断

- `risk_peak_weighted` 没有在 4 月崩掉，只是 `RT overall` 小幅回退约 `0.08`。
- 但它：
  - 明显改善了 2 月和 3 月
  - 4 月仍改善了 `RT 9_16`
  - 三个月均值整体优于旧链
- 因此从“跨月平均效果”看，它已经比单纯 `risk_hour_weighted` 更强。

### 当前建议

- 将 `risk_peak_weighted + rt_916_regime_affine` 升级为新的 RT 候选主链。
- 但冻结说明里要明确：
  - 它是“跨月均值更优”的主链
  - `2026-04 overall` 有极小回退
  - 若之后更重视 4 月单月极致分数，可以保留旧链作为对照

### 下一步

1. RT 主链暂时切到 `risk_peak_weighted + rt_916_regime_affine`。
2. 后续优先回到 DA 提升，或再做更长区间汇总验证。
## 2026-06-15 DA split target 结论

### 实施

- 新增 `da_target_mode` / `rt_target_mode`，允许 DA 与 RT 分别选择目标形式。
- 首轮测试：
  - `DA = residual_blend`
  - `RT = direct`
  - 其余保持当前 RT 主链：
    - `risk_peak_weighted + rt_916_regime_affine`

### 结果

- `2026-02`
  - 基线：`DA = 19.1957`, `RT = 26.3467`
  - split target：`DA = 21.0094`, `RT = 29.6735`

### 结论

- 这条最小 DA 结构改动是明确失败的：
  - DA 没有改善，反而明显变差
  - RT 也同步明显退化
- 因此：
  - `DA residual_blend + RT direct`：排除
- 说明之前 `residual_blend` 对 DA 的局部帮助并不具备在当前正式复现链中的可迁移性。
## 2026-06-15 DA asymmetric under loss 首轮结论

### 实施

- 在当前 RT 主链保持不变的前提下单独增强 DA 训练损失：
  - `rt_loss_mode = risk_peak_weighted`
  - `rt_calibration_mode = rt_916_regime_affine`
  - `da_loss_mode = asymmetric_under`
  - `da_under_weight_multiplier = 1.25`
- 输出目录：
  - `fusion_runs/timemixer_truth_window_da_asym/2026-02_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine`

### 结果

- `2026-02`
  - 旧主链：`DA = 19.1957`, `RT = 26.3467`
  - 新结果：`DA = 19.1058`, `RT = 26.2201`
  - 分段变化：
    - `DA 1_8`: `18.2774 -> 18.0764`
    - `DA 9_16`: `23.7730 -> 22.6403`
    - `DA 17_24`: `15.5368 -> 16.6008`
    - `RT 9_16`: `31.2109 -> 31.1840`

### 结论

- 这是当前第一条在不伤 RT 的前提下继续压低 `DA overall` 的有效路线。
- 提升主要来自 `DA 1_8` 与 `DA 9_16`，但 `DA 17_24` 有一定回退。
- 由于 `RT overall` 也同步小幅改善，这条路线值得继续做 `2026-03 / 2026-04` 扩月验证。

### 下一步

1. 复用同配置跑 `2026-03`。
2. 再跑 `2026-04`。
3. 若三个月均值优于当前冻结主链，则把 `DA asymmetric_under + RT risk_peak_weighted + rt_916_regime_affine` 升级为新冻结主链；否则记录为仅 `2026-02` 有效的候选。
## 2026-06-15 DA asymmetric under loss 第二轮修正结论

### 问题定位

- 第一版 `asymmetric_under` 虽然在 `2026-02/03` 有收益，但 `2026-04` 明显回退。
- 复盘后确认根因不在“低估惩罚”本身，而在实现细节：
  - 原实现把同一组 8 维小时权重同时施加到了所有 DA 分段
  - 这会把本来只想强调的 `9_16` 尾部权重，错误地扩散到 `1_8` 与 `17_24`
- 因此第二轮修正为：
  - 保留 `da_loss_mode = asymmetric_under`
  - 仅在 `9_16` 分段模型中启用小时权重
  - `1_8 / 17_24` 只保留轻量 under-prediction 惩罚，不再叠加这组 hour weights

### 输出目录

- `2026-02`：
  - `fusion_runs/timemixer_truth_window_da_asym_segaware/2026-02_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine`
- `2026-03`：
  - `fusion_runs/timemixer_truth_window_da_asym_segaware/2026-03_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine`
- `2026-04`：
  - `fusion_runs/timemixer_truth_window_da_asym_segaware/2026-04_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine`

### 对旧冻结主链的三个月对比

- `2026-02`
  - `DA overall: 19.1957 -> 18.7226`
  - `RT overall: 26.3467 -> 26.2333`
  - 主要增益来自 `DA 9_16: 23.7730 -> 21.9358`
- `2026-03`
  - `DA overall: 18.2984 -> 17.9346`
  - `RT overall: 31.6930 -> 31.2980`
  - `DA 1_8` 大幅改善，但 `DA 9_16` 与 `RT 9_16` 有小幅回退
- `2026-04`
  - `DA overall: 17.3931 -> 16.5343`
  - `RT overall: 25.3976 -> 24.8309`
  - 说明第二轮修正确实消除了第一版在 4 月的副作用

### 三个月均值

- 旧冻结主链：
  - `DA overall mean = 18.2957`
  - `RT overall mean = 27.8125`
- 第二轮 seg-aware 版本：
  - `DA overall mean = 17.7305`
  - `RT overall mean = 27.4541`

### 结论

- 这轮是当前第一条同时改善三个月 `DA overall` 与 `RT overall` 均值的 DA 定向路线。
- 当前最合理的解释是：
  - `asymmetric_under` 本身是有效的
  - 失败点来自“hour weight 作用范围过宽”，不是方向本身错误
- 从当前证据看，这一版本已经优于此前冻结主链，应该升级为新的默认候选主链。

### 下一步

1. 在冻结结论中把 seg-aware `asymmetric_under` 升级为当前默认 DA+RT 主链。
2. 后续若继续冲刺，优先围绕 `2026-03 9_16` 做更细结构优化，而不是回到泛化超参微调。
## 2026-06-15 RT 9_16 normal-hour bias 路线结论

### 诊断

- 基于当前 seg-aware 主链复盘 `2026-03 RT 9_16` 后，确认最难部分主要不是 `stress bucket`，而是 `normal bucket`：
  - `9-13` 点系统性低估
  - `14-15` 点局部转成高估
  - `16` 点再切回偏差
- 这说明 `normal bucket` 的误差更像“小时形状问题”，而不只是整桶统一 affine 能解决的偏差。

### 实施

- 新增 `rt_calibration_mode = rt_916_regime_affine_hourbias`
- 规则：
  - `stress bucket` 保持现有 affine
  - `normal bucket` 改为验证集拟合的逐小时 median bias
  - 仍然只使用 future-known 特征分桶，保持 leakage-safe
- 输出目录：
  - `fusion_runs/timemixer_truth_window_rt916hourbias/2026-02_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine_hourbias`
  - `fusion_runs/timemixer_truth_window_rt916hourbias/2026-03_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine_hourbias`
  - `fusion_runs/timemixer_truth_window_rt916hourbias/2026-04_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine_hourbias`

### 结果

- `2026-03`
  - `RT overall: 31.2980 -> 32.0300`
  - `RT 9_16: 46.1419 -> 45.9769`
- `2026-02`
  - `RT overall: 26.2333 -> 27.2844`
  - `RT 9_16: 31.1427 -> 33.6274`
- `2026-04`
  - `RT overall: 24.8309 -> 24.9899`
  - `RT 9_16: 25.2517 -> 25.7774`

### 结论

- 这条路线确实触到了 `2026-03 RT 9_16` 的局部问题，但收益极小。
- 同时它明显伤害了 `2026-02`，并且整体 `RT overall` 三个月均值变差。
- 因此结论是：
  - `rt_916_regime_affine_hourbias`：排除
  - 保持当前 seg-aware `DA asymmetric_under + risk_peak_weighted + rt_916_regime_affine` 主链不变

### 补充判断

- `2026-03 RT 9_16` 的难点更像模型预测形状本身不足，而不是后处理再细分一个 bias 桶就能补救。
- 后续若继续冲这块，优先顺序应转向：
  1. 训练侧 sample weighting / target emphasis
  2. `9_16` 分段显式 head/结构
  3. 更强的 DA / bidding-space 峰值信息利用
## 2026-06-15 RT normal-focus 训练加权路线结论

### 动机

- 在排除更复杂后处理后，转向训练侧继续打 `2026-03 RT 9_16`。
- 复盘当前 seg-aware 主链发现：
  - 大误差尾部主要集中在 `normal bucket`
  - 风险小时重点落在 `9/10/11/12/13/16`
- 因此新增一个更保守的训练加权：
  - 仍保持 `rt_loss_mode = risk_peak_weighted`
  - 只对 `normal bucket` 且位于 `9/10/11/12/13/16` 的样本叠加额外权重

### 实施

- 新增参数：
  - `rt_normal_focus_multiplier = 1.2`
- 输出目录：
  - `fusion_runs/timemixer_truth_window_rtnormalfocus/2026-02_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine`
  - `fusion_runs/timemixer_truth_window_rtnormalfocus/2026-03_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine`
  - `fusion_runs/timemixer_truth_window_rtnormalfocus/2026-04_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine`

### 结果

- `2026-03`
  - `RT overall: 31.2980 -> 32.0552`
  - `RT 9_16: 46.1419 -> 45.4610`
  - `RT 17_24: 21.2063 -> 24.1473`
- `2026-02`
  - `RT overall: 26.2333 -> 27.9865`
  - `RT 17_24: 19.8972 -> 24.7397`
- `2026-04`
  - `RT overall: 24.8309 -> 25.1673`
  - `RT 9_16: 25.2517 -> 24.2632`
  - `RT 17_24: 23.7884 -> 25.7171`

### 结论

- 这条路线说明训练侧确实能触到 `RT 9_16` 的局部问题。
- 但它把 `17_24` 明显带坏，导致 `RT overall` 三个月均值明显回退。
- 因此结论是：
  - `normal-focus` 训练加权：排除
  - 当前 seg-aware `DA asymmetric_under + risk_peak_weighted + rt_916_regime_affine` 主链保持不变

### 补充判断

- `2026-03 RT 9_16` 继续单点加强时，很容易把其他时段一并扭坏。
- 说明后续若继续冲，应优先考虑：
  1. 真正 segment-specific 的 `9_16` head / 结构
  2. 更显式利用 `DA / bidding_space` 的高价尖峰信息
  3. 而不是继续对共享 RT 头做更激进的 loss reweight
## 2026-06-15 RT 9_16 future residual head 路线结论

### 动机

- 在排除更细后处理和更激进 loss reweight 后，尝试更结构化但仍最小侵入的 `9_16` 专属能力。
- 思路是：
  - 保持当前按 segment 分开训练的工程框架不变
  - 仅对 `RT 9_16` 分段模型加入一个 future-driven residual head
  - 让模型更直接利用 `DA / bidding_space / solar` 等未来已知特征

### 实施

- 新增：
  - `rt_segment_head_mode = future_residual`
- 机制：
  - 共享原 TimeMixer 主干与主 head
  - 额外增加一个基于 future token 的逐时 residual head
  - 再用 gate 把 residual 注入到最终 8 小时输出
- 输出目录：
  - `fusion_runs/timemixer_truth_window_rtseghead/2026-02_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine`
  - `fusion_runs/timemixer_truth_window_rtseghead/2026-03_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine`
  - `fusion_runs/timemixer_truth_window_rtseghead/2026-04_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine`

### 结果

- `2026-02`
  - `RT overall: 26.2333 -> 26.6471`
  - `RT 9_16: 31.1427 -> 32.7137`
- `2026-03`
  - `RT overall: 31.2980 -> 32.9585`
  - `RT 9_16: 46.1419 -> 46.6014`
  - `RT 17_24: 21.2063 -> 25.9868`
- `2026-04`
  - `RT overall: 24.8309 -> 25.2902`
  - `RT 9_16: 25.2517 -> 27.6392`

### 结论

- 这条 `future_residual head` 路线没有解决 `2026-03 RT 9_16`，反而在三个月上都出现回退。
- 说明问题并不只是“再多喂一个 future residual 通道”就能解决，当前轻量 head 设计不够。
- 因此结论是：
  - `rt_segment_head_mode = future_residual`：排除
  - 当前 seg-aware 主链保持不变

### 补充判断

- 继续在现有 TimeMixer 主干上做轻量 `9_16` residual/head 微改，边际收益已经很可疑。
- 若后续还要继续冲 `2026-03 RT 9_16`，更值得考虑的是：
  1. 真正独立的 `9_16` 专用模型分支
  2. 更强 backbone 替换并保留当前无泄漏 pipeline
  3. 或进入 leakage-safe 融合作为兜底
## 2026-06-15 RT 9_16 独立 backbone 路线结论

### 动机

- 在轻量后处理、训练加权、轻量 residual head 都未奏效后，尝试更进一步的 `RT 9_16` 独立分支。
- 但仍保持最小侵入：
  - 默认 backbone 继续用 `TimeMixer`
  - 仅 `RT 9_16` 分段模型单独切成 `TimesNet`
  - 其他分段、导出结构、manifest、无泄漏协议全部不变

### 实施

- 新增：
  - `rt_916_backbone = timesnet`
- 输出目录：
  - `fusion_runs/timemixer_truth_window_rt916splitbackbone/2026-02_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine`
  - `fusion_runs/timemixer_truth_window_rt916splitbackbone/2026-03_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine`
  - `fusion_runs/timemixer_truth_window_rt916splitbackbone/2026-04_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine`

### 结果

- `2026-02`
  - `RT overall: 26.2333 -> 25.5601`
  - `RT 9_16: 31.1427 -> 30.5941`
- `2026-03`
  - `RT overall: 31.2980 -> 32.5832`
  - `RT 9_16: 46.1419 -> 45.2972`
  - `RT 17_24: 21.2063 -> 26.1557`
- `2026-04`
  - `RT overall: 24.8309 -> 24.8180`
  - `RT 9_16: 25.2517 -> 25.2108`

### 结论

- 这条路线说明“`RT 9_16` 独立 backbone”方向并非完全没价值：
  - `2026-02/03/04` 的 `RT 9_16` 都有小幅改善
- 但它的问题同样很明显：
  - `2026-03 RT overall` 仍被明显拖坏
  - `RT 17_24` 出现明显副作用
- 因此在当前最小侵入实现下，结论是：
  - `rt_916_backbone = timesnet`：排除，不升级主链

### 补充判断

- 这轮结果说明真正独立分支比“轻量 head”更接近正确方向，但还不够干净。
- 如果继续深挖这条线，下一步就不应再是“最小侵入小改”，而是：
  1. 真正隔离 `RT 9_16` 的独立模型分支与调参
  2. 或直接进入 leakage-safe 融合兜底
## 2026-06-15 leakage-safe 融合兜底结论

### 动机

- 到此前为止，单模侧已经系统验证了：
  - 轻量后处理
  - 轻量 loss reweight
  - 轻量 `9_16` residual head
  - 轻量 `9_16` 独立 backbone
- 这些路线都未能稳定解决 `2026-03 RT 9_16`，因此按计划进入 leakage-safe 融合作为兜底。

### 实施

- 新建 runner：
  - `fusion/runners/run_timemixer_safe_fusion.py`
- 当前最小可用融合规则：
  - `DA` 全部沿用当前最佳主链 `segaware`
  - `RT 1_8 / 17_24` 沿用当前最佳主链 `segaware`
  - 仅 `RT 9_16` 切换为 `rt916splitbackbone` 的输出
- 这条规则完全基于已生成的单模预测结果做拼接：
  - 不重新训练
  - 不访问未来真值
  - 不改变现有字段和导出格式
- 输出目录：
  - `fusion_runs/timemixer_safe_fusion/2026-02_safe_rt9_16_fusion`
  - `fusion_runs/timemixer_safe_fusion/2026-03_safe_rt9_16_fusion`
  - `fusion_runs/timemixer_safe_fusion/2026-04_safe_rt9_16_fusion`

### 结果

- `2026-02`
  - `RT overall: 26.2333 -> 26.0505`
  - `RT 9_16: 31.1427 -> 30.5941`
- `2026-03`
  - `RT overall: 31.2980 -> 31.0164`
  - `RT 9_16: 46.1419 -> 45.2972`
- `2026-04`
  - `RT overall: 24.8309 -> 24.8173`
  - `RT 9_16: 25.2517 -> 25.2108`

### 三个月均值

- 当前单模冻结主链：
  - `DA overall mean = 17.7305`
  - `RT overall mean = 27.4541`
- safe fusion 兜底版：
  - `DA overall mean = 17.7305`
  - `RT overall mean = 27.2947`

### 结论

- 这是当前第一条在不改变 `DA`、不引入泄漏风险、也不破坏其他时段的前提下，稳定改善三个月 `RT overall` 的兜底方案。
- 改善幅度不算激进，但方向稳定、工程简单、可解释性强。
- 因此当前结论应升级为：
  - 单模冻结主链仍保留：`seg-aware DA asymmetric_under + risk_peak_weighted + rt_916_regime_affine`
  - 同时新增一个更强的 leakage-safe 融合兜底版：
    - `RT 9_16` 使用 `rt916splitbackbone`
    - 其余全部沿用当前单模冻结主链

### 下一步

1. 在冻结结论中把这条 safe fusion 标记为当前最强可落地兜底方案。
2. 若后续还要继续冲分，优先在此融合框架上继续替换 `RT 9_16` 候选，而不是反复改整条单模主链。

## 2026-06-15 safe fusion 工程化收口

### 已完成

- 已将 `safe_rt9_16_fusion` 从“实验拼接脚本”补齐为正式交付入口：
  - runner：`fusion/runners/run_timemixer_safe_fusion.py`
  - 标准导出：
    - `predictions_raw.csv`
    - `metrics_by_period.csv`
    - `protocol_audit.csv`
    - `run_manifest.json`
    - `da_prediction_vs_actual.png`
    - `rt_prediction_vs_actual.png`
- 已接入 leaderboard：
  - `TimeMixer/outputs_v2/serial_keepdrop/leaderboard.csv`
  - 当前可见记录：
    - `month = 2026-03`
    - `pipeline_mode = fusion`
    - `backbone = safe_rt9_16_fusion`

### 结论

- 当前 `safe_rt9_16_fusion` 已不再只是分析脚本，而是可以按项目现有格式直接沉淀结果的正式兜底入口。
- 因此现阶段可交付结论应明确区分：
  - 单模最佳冻结主链
  - 可直接交付的最强 leakage-safe 融合兜底版

## 2026-06-15 safe fusion 批量入口完成

### 新增

- 批量 runner：
  - `fusion/runners/run_timemixer_safe_fusion_batch.py`
- 默认内置批量配置：
  - `2026-02`
  - `2026-03`
  - `2026-04`

### 批量产物

- 输出目录：
  - `fusion_runs/timemixer_safe_fusion_batch`
- 已生成：
  - `2026-02_safe_rt9_16_fusion/`
  - `2026-03_safe_rt9_16_fusion/`
  - `2026-04_safe_rt9_16_fusion/`
  - `monthly_summary.csv`
  - `metrics_all_months.csv`
  - `aggregate_summary.csv`
  - `batch_manifest.json`

### 汇总结果

- `DA overall mean = 17.7305`
- `RT overall mean = 27.2947`
- `RT 9_16 mean = 33.7007`

### 结论

- 到这一步，`safe_rt9_16_fusion` 已具备：
  - 单月标准产物
  - 批量月份统一运行
  - 汇总表自动生成
- 因此它已经满足“正式可交付兜底版”的工程要求。

## 2026-06-15 最终验收审计补录

### 补录目的

- 把“已经修复到什么程度”和“距离目标还差多少”冻结成最终审计结论，供后续 goal 模式与上下文压缩直接恢复。

### 审计结论

- 协议层面：
  - 已建立正式月度复现链
  - 已固定 `historical_joint` 主结论口径
  - 已固定 `D-1 15:00` 决策边界
  - 已保留 `predictions_raw.csv / metrics_by_period.csv / protocol_audit.csv / run_manifest.json / 图表`
- 结果层面：
  - 最佳单模型三个月均值：`DA 17.7305 / RT 27.4541`
  - 最佳 safe fusion 三个月均值：`DA 17.7305 / RT 27.2947`
  - 已明显优于最初故障状态，但未完全达到 `DA≈15 / RT≈25`
- 交付层面：
  - 当前最强正式交付入口应为 `fusion/runners/run_timemixer_safe_fusion_batch.py`
  - 当前最强单模型主链仍保留为后续继续冲分基础

### 冻结判断

- 当前项目状态应从“继续盲冲”切换为：
  - `best_deliverable_with_gap`
- 含义：
  - 已有可以交付的 leakage-safe 最佳版本
  - 若继续优化，主攻点应集中在 `2026-03 RT 9_16`

## 2026-06-15 按《TimeMixer 下一步计划》继续执行

### 1. worktree 合并

- 已按文档要求，将以下文件从 worktree 同步回主项目 `D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0`：
  - `TimeMixer/repro_pipeline.py`
  - `TimeMixer/backbones.py`
  - `TimeMixer/pipeline_timemixer.py`
  - `fusion/runners/run_timemixer_export.py`
  - `fusion/runners/run_timemixer_safe_fusion.py`
  - `fusion/runners/run_timemixer_safe_fusion_batch.py`
  - `docs/TimeMixer当前冻结结论.md`
  - `docs/TimeMixer最终验收审计.md`
  - `docs/TimeMixer冲刺日志.md`
- 已核对主项目内关键入口文件已落位。

### 2. 2026-03 RT 9_16 诊断

- 新增诊断脚本：
  - `fusion/runners/analyze_rt916_spikes.py`
- 已基于 `safe_rt9_16_fusion` 结果提取 3 月与 4 月 `RT 9_16` 的逐日诊断与尖峰小时表：
  - `fusion_runs/diagnostics/rt916_daily_diagnostic.csv`
  - `fusion_runs/diagnostics/rt916_spike_hours.csv`

### 3. 当前诊断结论

- `2026-03 RT 9_16` 最差日期集中在：
  - `03-08`
  - `03-22`
  - `03-28`
  - `03-29`
  - `03-04`
- 这些日期的共同特征非常明显：
  - 光伏均值高，且段内波动大
  - 竞价空间均值很低，甚至转负
  - `DA` 均值很低，模型容易顺着低 `DA` 走
  - 但 `RT` 实际会突然冲高，形成严重低估
- 结论：
  - 当前瓶颈更像 `regime switching / spike day detection` 问题
  - 不像普通超参不足
  - 文档要求“不要再做小改”是对的

### 4. 4 月对照

- `2026-04 RT 9_16` 虽也有尖峰日，但数量和连续性弱于 3 月。
- 4 月最差天同样偏向：
  - 高光伏
  - 低或负竞价空间
  - 低 `DA` 下的异常 `RT` 冲高
- 但整体没有 3 月那么集中，因此总体 sMAPE 更低。

### 5. GitHub 调研方向冻结

- 已按外部计划启动 GitHub 调研，当前优先关注两类方案：
  - 尖峰两步法：先分类“是否 spike day / spike hour”，再回归价格
  - regime-aware 方案：用供需紧张度、可再生出力占比、竞价空间等信号做切换
- 基于当前诊断，下一轮实验最值得做的一条单变量路线是：
  - 只改 `RT 9_16` 的校准范式
  - 加一个“高风险日 / 高风险小时”两步法校准候选
  - 不动 `DA`、`1_8`、`17_24`、也不回到小超参微调

## 2026-06-15 GitHub / 文献调研补录

### 已完成

- 已形成稳定调研摘要：
  - [docs/TimeMixer尖峰调研摘要_20260615.md](/C:/Users/37813/.codex/worktrees/b03b/electricity_forecast_model2.0/docs/TimeMixer尖峰调研摘要_20260615.md)

### 核心结论

- GitHub 上真正“专门做电价尖峰 + regime switching”的现成项目不多。
- 但公开工程与文献给出的方向高度一致：
  - 先做 spike occurrence / regime 判断
  - 再做价格回归或条件切换
- 当前对我们最贴近的工程模式是：
  - 树模型或规则层做 gate
  - 主预测链保持不变
  - 只在 `RT 9_16` 触发条件切换
- 新补充的文献也进一步支持：
  - 不平衡 spike 样本适合单独分类建模
  - regime 的 transition probability 应由 load / reserve / renewable stress 一类变量驱动
  - 尾部问题适合单独建 gate，而不是继续用统一回归硬吃

### 下一轮实验建议

- 新增单变量候选：
  - `rt_916_spike_day_affine`
- 只改：
  - `RT 9_16` 校准逻辑
- 不改：
  - `DA`
  - 主 backbone
  - 其他时段
- 候选优先级：
  - 第一优先：`spike-day gate + affine`
  - 第二优先：`gate-controlled safe fusion`
  - 第三优先：`spike-hour gate`

## 2026-06-15 第四步执行：`rt_916_spike_day_affine`

### 实现

- 已在 [TimeMixer/repro_pipeline.py](/C:/Users/37813/.codex/worktrees/b03b/electricity_forecast_model2.0/TimeMixer/repro_pipeline.py) 中新增：
  - `compute_rt_916_day_feature_table`
  - `fit_spike_day_affine_calibrator`
  - `apply_spike_day_affine_calibrator`
- 新增校准模式：
  - `rt_916_spike_day_affine`
- 新增批量 runner：
  - [fusion/runners/run_timemixer_rt916_spike_day_batch.py](/C:/Users/37813/.codex/worktrees/b03b/electricity_forecast_model2.0/fusion/runners/run_timemixer_rt916_spike_day_batch.py)
- 设计原则：
  - 保留当前 `rt_916_regime_affine` 作为 base
  - 只在 `RT 9_16` 的日级 gate 命中时，再叠加一层 spike affine
  - gate 只用 `D-1 15:00` 可见特征：
    - `solar_mean`
    - `bidding_min`
    - `da_mean`

### 三个月结果

- 当前单模型最佳主链：
  - `DA overall mean = 17.7305`
  - `RT overall mean = 27.4541`
  - `RT 9_16 mean = 34.1788`
- 新路线 `rt_916_spike_day_affine`：
  - `DA overall mean = 18.1396`
  - `RT overall mean = 28.6763`
  - `RT 9_16 mean = 35.6196`
- 当前最强 safe fusion：
  - `DA overall mean = 17.7305`
  - `RT overall mean = 27.2947`
  - `RT 9_16 mean = 33.7007`

### 分月对比

- `2026-02`
  - `RT overall: 26.2333 -> 27.8121`
  - `RT 9_16: 31.1427 -> 33.0001`
- `2026-03`
  - `RT overall: 31.2980 -> 30.9951`
  - `RT 9_16: 46.1419 -> 45.3632`
- `2026-04`
  - `RT overall: 24.8309 -> 27.2217`
  - `RT 9_16: 25.2517 -> 28.4955`

### 结论

- 这条路线只在 `2026-03` 有很小幅改善，但：
  - 明显伤害 `2026-02`
  - 明显伤害 `2026-04`
  - 三个月 `RT overall` 均值显著变差
- 因此正式结论是：
  - `rt_916_spike_day_affine`：排除
- 这也说明：
  - 单纯“日级规则 gate + affine”还不够干净
  - 如果继续沿 gate 思路走，更值得尝试的是：
    - `gate-controlled safe fusion`
    - 而不是继续在同一条 affine 校准线上细抠阈值

## 2026-06-15 用户指定对照实验 A：2026-05 当前 safe fusion 主链验证

### 配置

- `pipeline_mode = historical_joint`
- `backbone = timemixer`
- `segment_training = true`
- `da_loss_mode = asymmetric_under`
- `da_under_weight_multiplier = 1.25`
- `rt_loss_mode = risk_peak_weighted`
- `rt_peak_weight_multiplier = 1.4`
- `rt_calibration_mode = rt_916_regime_affine`
- safe fusion 规则：
  - `RT 9_16` 使用 `rt916splitbackbone`
  - 其余使用当前主链

### 2026-05 结果

- `DA overall = 21.0705`
- `RT overall = 27.6800`
- `RT 1_8 = 37.6680`
- `RT 9_16 = 21.1299`
- `RT 17_24 = 24.2421`

### 4 个月（Feb-May）汇总

- `DA mean = 18.5655`
- `RT mean = 27.3910`
- `RT 9_16 mean = 30.5580`

### 与历史基准公平对比

- 基准来源：
  - `fusion_runs/historical_monthly_benchmarks/monthly_historical_benchmarks.csv`
- 对比口径：
  - `DA` 对 `TimeMixer_DA_hist`
  - `RT` 对 `TimeMixer_RT_hist`

| month | current_DA | hist_DA | delta_DA | current_RT | hist_RT | delta_RT | current_RT_9_16 | hist_RT_9_16 | delta_RT_9_16 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-02 | 18.7226 | 19.0621 | -0.3395 | 26.0505 | 30.7957 | -4.7452 | 30.5941 | 62.1500 | -31.5558 |
| 2026-03 | 17.9346 | 14.9169 | 3.0177 | 31.0164 | 28.8654 | 2.1510 | 45.2972 | 43.5825 | 1.7147 |
| 2026-04 | 16.5343 | 16.0266 | 0.5076 | 24.8173 | 24.7047 | 0.1126 | 25.2108 | 26.3759 | -1.1651 |
| 2026-05 | 21.0705 | 21.2688 | -0.1983 | 27.6800 | 23.6096 | 4.0704 | 21.1299 | 17.6326 | 3.4973 |

## 2026-06-15 用户指定对照实验 B：纯 L1 无 calibration

### 配置

- `pipeline_mode = historical_joint`
- `backbone = timemixer`
- `segment_training = true`
- `target_mode = direct`
- `da_loss_mode = l1`
- `rt_loss_mode = l1`
- `rt_calibration_mode = none`
- 其余超参保持 `repro_pipeline.py` 默认值

### 结果

- `2026-03`
  - `DA overall = 18.2879`
  - `RT overall = 33.6470`
  - `RT 9_16 = 49.2249`
- `2026-04`
  - `DA overall = 21.4268`
  - `RT overall = 27.7009`
  - `RT 9_16 = 29.0694`

### 与当前加权主链同月对比

| month | l1_DA | current_DA | delta_DA | l1_RT | current_RT | delta_RT | l1_RT_9_16 | current_RT_9_16 | delta_RT_9_16 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-03 | 18.2879 | 17.9346 | 0.3533 | 33.6470 | 31.2980 | 2.3491 | 49.2248 | 46.1419 | 3.0829 |
| 2026-04 | 21.4268 | 16.5343 | 4.8925 | 27.7009 | 24.8309 | 2.8700 | 29.0694 | 25.2517 | 3.8177 |

### 结论

- 在用户指定的这组极简对照里：
  - 纯 `L1 + no calibration` 在 `2026-03` 和 `2026-04` 都显著差于当前加权主链
  - 当前加权主链优势在 `RT overall` 与 `RT 9_16` 上都保留

## 2026-06-15 最终冲刺计划 v2 第一轮

### 1A. 2026-05 RT 1_8 诊断

- 使用文件：
  - `fusion_runs/timemixer_safe_fusion/2026-05_safe_rt9_16_fusion/predictions_raw.csv`
  - `D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\模型复现与测试\总计\timemixer_实时.csv`
- 诊断产物：
  - `fusion_runs/diagnostics/may_rt_1_8_daily_diagnosis.csv`
  - `fusion_runs/diagnostics/may_rt_1_8_hourly_safe_vs_hist.csv`

#### 关键事实

- 当前 safe fusion `2026-05 RT 1_8 = 37.6680%`
- 月内 `RT 1_8` 平均误差：
  - `mean_err = -74.54`
  - `MAE = 122.61`
- 高误差日数量：
  - `SMAPE > 45%` 的天数为 `8 / 31`
- 最差 5 天只贡献了月度日级 MAE 的 `21.58%`

#### 最差 5 天

| date | SMAPE | load_change | wind_change | holiday | weekday | mean_rt | max_rt | mean_pred | safe_mean_err |
|---|---:|---:|---:|---|---|---:|---:|---:|---:|
| 2026-05-31 | 96.18 | -8413.71 | -787.30 | False | Sunday | 58.30 | 338.37 | 221.90 | +163.60 |
| 2026-05-05 | 68.84 | -3875.65 | -1357.40 | True | Tuesday | 341.30 | 460.49 | 182.21 | -159.09 |
| 2026-05-10 | 61.86 | -5115.77 | -3394.96 | False | Sunday | 284.49 | 433.76 | 179.12 | -105.37 |
| 2026-05-06 | 60.35 | -2464.81 | -2287.77 | False | Wednesday | 239.73 | 356.41 | 169.58 | -70.14 |
| 2026-05-09 | 53.91 | -2671.95 | +3892.06 | False | Saturday | 319.99 | 542.95 | 268.60 | -51.39 |

#### 诊断结论

- 不是“仅 2-3 个极端日拖垮整月”。
- 更像是：
  - 月内存在一批明显高误差日
  - 其中周末/节假日与低价日问题更突出
  - 同时也存在系统性负偏差（整月 `mean_err = -74.54`）
- 历史预测文件在当前路径下只覆盖到 `2026-04-27`，没有 5 月逐小时记录，因此无法完成“同一天同一小时”的直接逐点对比。
- 但从历史月度基准看，`2026-05 TimeMixer_RT_hist 1_8 = 30.67%`，说明 5 月 `RT 1_8` 确实是当前 safe fusion 相对历史的新增薄弱点。

### 1B. frozen weights 实验

#### 实现

- 在 [TimeMixer/repro_pipeline.py](/C:/Users/37813/.codex/worktrees/b03b/electricity_forecast_model2.0/TimeMixer/repro_pipeline.py) 新增：
  - `training_mode = rolling|frozen`
  - `frozen_train_start`
  - `frozen_train_end_exclusive`
- frozen 配置：
  - 固定训练窗：`2026-01-01 ~ 2026-02-01`
  - 其余保持当前最优加权：
    - `segment_training = true`
    - `da_loss_mode = asymmetric_under`
    - `rt_loss_mode = risk_peak_weighted`
    - `rt_calibration_mode = rt_916_regime_affine`
- 批量 runner：
  - [fusion/runners/run_timemixer_frozen_batch.py](/C:/Users/37813/.codex/worktrees/b03b/electricity_forecast_model2.0/fusion/runners/run_timemixer_frozen_batch.py)

#### 结果

| month | DA overall | RT overall | RT 1_8 | RT 9_16 | RT 17_24 |
|---|---:|---:|---:|---:|---:|
| 2026-02 | 40.7393 | 53.0307 | 50.9696 | 74.4617 | 33.6607 |
| 2026-03 | 38.7851 | 45.6390 | 47.4177 | 65.5033 | 23.9959 |
| 2026-04 | 45.2683 | 57.6606 | 66.1579 | 78.4618 | 28.3620 |
| 2026-05 | 54.4840 | 59.7406 | 75.7172 | 70.3350 | 33.1695 |

#### 结论

- frozen weights 全面失败，远差于当前 safe fusion / 当前月度重训主链。
- 不仅 5 月没有改善，4 个月全部明显崩坏。
- 因此：
  - `training_mode = frozen`：实验性保留
  - 不升级为默认策略
  - 第二轮不做 `frozen + multi-seed`

### 1C. direct_24 实验

#### 实现

- 使用参数：
  - `segment_training = false`
  - `epochs = 50`
  - 其余保持当前最优加权配置
- 批量 runner：
  - [fusion/runners/run_timemixer_direct24_batch.py](/C:/Users/37813/.codex/worktrees/b03b/electricity_forecast_model2.0/fusion/runners/run_timemixer_direct24_batch.py)

#### 结果

| month | direct24_DA | direct24_RT | direct24_RT_9_16 | direct24_RT_17_24 | segment_DA | segment_RT | segment_RT_9_16 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-03 | 19.1710 | 30.2408 | 46.8084 | 19.6737 | 17.9346 | 31.2980 | 46.1419 |
| 2026-05 | 20.0862 | 21.4152 | 17.9408 | 19.6751 | 21.0705 | 27.6101 | 20.9201 |

#### 结论

- `2026-03`
  - `RT overall` 有改善：`31.2980 -> 30.2408`
  - `RT 9_16` 略差：`46.1419 -> 46.8084`
- `2026-05`
  - `RT overall` 显著改善：`27.6101 -> 21.4152`
  - `RT 9_16` 明显改善：`20.9201 -> 17.9408`
- direct_24 对 5 月问题月非常有利，对 3 月整体也不坏，但当前只按计划跑了 `2026-03/05`，尚未覆盖 4 个月。

### 第一轮汇总表

| 配置 | Feb RT | Mar RT | Apr RT | May RT | 说明 |
|------|--------:|--------:|--------:|--------:|------|
| 当前 safe fusion | 26.0505 | 31.0164 | 24.8173 | 27.6800 | 当前基线 |
| frozen weights | 53.0307 | 45.6390 | 57.6606 | 59.7406 | 明显失败 |
| direct_24 | — | 30.2408 | — | 21.4152 | 按计划仅跑 03/05 |

### 第一轮判断

- `frozen weights`：排除
- `direct_24`：值得保留，尤其是 5 月有强改善信号
- 如果继续下一轮，建议：
  - 第二轮多 seed 先基于当前 segment 主链跑
  - 但要记住 `direct_24` 在 5 月可能是后续组合时的关键候选

## 2026-06-15 最终冲刺计划 v2 第二轮：多 seed 集成

### 配置

- 主链配置：
  - `segment_training = true`
  - `da_loss_mode = asymmetric_under`
  - `rt_loss_mode = risk_peak_weighted`
  - `rt_calibration_mode = rt_916_regime_affine`
- seed 列表：
  - `[42, 123, 456, 789, 2026]`
- 批量 runner：
  - [fusion/runners/run_timemixer_multiseed_batch.py](/C:/Users/37813/.codex/worktrees/b03b/electricity_forecast_model2.0/fusion/runners/run_timemixer_multiseed_batch.py)

### 4 个月结果

| month | ens_DA | ens_RT | ens_RT_9_16 |
|---|---:|---:|---:|
| 2026-02 | 25.5960 | 37.3961 | 36.2755 |
| 2026-03 | 16.9404 | 31.3875 | 45.8549 |
| 2026-04 | 18.3690 | 26.2921 | 25.9687 |
| 2026-05 | 22.8066 | 24.9661 | 18.7993 |

### 与单 seed=42 同口径对比

| month | single_RT | ens_RT | delta_RT | single_RT_9_16 | ens_RT_9_16 | delta_RT_9_16 |
|---|---:|---:|---:|---:|---:|---:|
| 2026-02 | 37.3401 | 37.3961 | +0.0559 |
| 2026-03 | 30.5919 | 31.3875 | +0.7956 |
| 2026-04 | 26.9880 | 26.2921 | -0.6959 |
| 2026-05 | 24.1487 | 24.9661 | +0.8173 |

### 均值对比

- single seed 42：
  - `DA mean = 21.6746`
  - `RT mean = 29.7672`
  - `RT 9_16 mean = 31.9681`
- multi-seed ensemble：
  - `DA mean = 20.9280`
  - `RT mean = 30.0104`
  - `RT 9_16 mean = 31.7246`

### 结论

- 多 seed 集成没有改善 `RT overall`，反而从 `29.7672` 恶化到 `30.0104`
- 虽然 `RT 9_16 mean` 有极小改善，但不足以弥补整体退化
- 因此：
  - multi-seed 不升级为默认策略
  - `frozen + multi-seed` 不再执行（第一轮 frozen 已失败）

## 2026-06-15 最终冲刺计划 v2 第三轮：VMD 分解

### 实现

- 已安装：
  - `vmdpy`
- 配置层新增：
  - `decomposition_mode = none|vmd`
- 最小侵入实验 runner：
  - [fusion/runners/run_timemixer_vmd_rt916.py](/C:/Users/37813/.codex/worktrees/b03b/electricity_forecast_model2.0/fusion/runners/run_timemixer_vmd_rt916.py)
- 实现方式：
  - 仅对 `RT 9_16` 做 VMD 分解
  - `K=4, alpha=2000, tau=0`
  - 保留当前主链 DA 与 RT 其余时段输出
  - 用 4 个 mode-specific TimeMixer 预测 `RT 9_16` 分量，再求和替换

### 结果

| month | vmd_RT | vmd_RT_9_16 |
|---|---:|---:|
| 2026-03 | 33.5497 | 54.3680 |
| 2026-05 | 27.3982 | 27.4394 |

### 与当前候选对比

- `2026-03`
  - 当前 safe fusion `RT overall = 31.0164`
  - VMD `RT overall = 33.5497`
- `2026-05`
  - `direct_24 RT overall = 21.4152`
  - VMD `RT overall = 27.3982`

### 结论

- VMD 在这轮最小侵入实现下明显失败：
  - `2026-03 RT 9_16` 从 `45.2972` 进一步恶化到 `54.3680`
  - `2026-05 RT 9_16` 也明显差于 `direct_24`
- 因此：
  - `decomposition_mode = vmd`：本轮排除
  - 不再切到 EMD 复测

## 2026-06-15 最终冲刺计划 v2 第四轮：跨轮组合

### 月度选择

| month | selected_config | source_dir | DA overall | RT overall |
|---|---|---|---:|---:|
| 2026-02 | safe_fusion | `fusion_runs/timemixer_safe_fusion/2026-02_safe_rt9_16_fusion` | 18.7226 | 26.0505 |
| 2026-03 | direct24 | `fusion_runs/timemixer_direct24_batch/2026-03_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine` | 19.1710 | 30.2408 |
| 2026-04 | safe_fusion | `fusion_runs/timemixer_safe_fusion/2026-04_safe_rt9_16_fusion` | 16.5343 | 24.8173 |
| 2026-05 | direct24 | `fusion_runs/timemixer_direct24_batch/2026-05_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine` | 20.0862 | 21.4152 |

### 最终 4 个月组合结果

- 产物目录：
  - `fusion_runs/timemixer_round4_combo`
- 已生成：
  - `predictions_raw.csv`
  - `metrics_by_period.csv`
  - `monthly_summary.csv`
  - `aggregate_summary.csv`
  - `batch_manifest.json`

### 最终指标

- 月度均值口径：
  - `DA mean = 18.6285`
  - `RT mean = 25.6310`
- 4 个月拼接整体口径：
  - `DA overall = 18.6725`
  - `RT overall = 25.7818`
- `RT 1_8 = 26.5798`
- `RT 9_16 = 30.3237`
- `RT 17_24 = 20.4418`

### 结论

- 按月择优组合后：
  - `RT overall` 已从当前 safe fusion 的 `27.3910` 降到 `25.7818`
  - 已低于计划中“若 ≤26.5 则可停止单模冲分”的阈值
- 这轮最关键的有效发现不是 frozen / multi-seed / VMD，而是：
  - `direct_24` 在 `2026-03` 与 `2026-05` 对 RT 有实质帮助
  - safe fusion 在 `2026-02` 与 `2026-04` 仍是更优选择

## 2026-06-16 统一入口与封装重构执行记录

### 范围重定

- 按最新执行约束重新锁定范围：
  - `lightGBM` 不拆分，只做统一入口接入
  - `TimesFM` 不拆分，只做统一入口接入
  - `SGDFNet`、`RT916_SpikeFusionNet`、`TimeMixer` 继续整理
  - 融合不重写，直接复用现有 `fusion` 正式路径
- 本轮固定计划文档：
  - `docs/Codex执行计划_统一入口与模型封装重构_20260616.md`

### 泄漏修复复核

- `SGDFNet`
  - 已复核 `SGDFNet/src/sgdfnet/data_contract.py`
  - 在此前 delta 主链修复基础上，继续补上残差滚动统计的 cutoff-safe 对齐
  - 新增：
    - `_safe_hourly_history()`
  - 修复：
    - `hist_load_resid_roll_mean_24`
    - `hist_netload_resid_roll_mean_24`
  - 这两项已不再通过 `shift(1)` 把同日实际值回流进特征

- `RT916_SpikeFusionNet`
  - 已复核 `dataprocess.py` 与 `core.py`
  - 当前主链已切到 forecast 侧：
    - `HISTORY_FORECAST_MAP`
    - `get_history_feature_name()`
    - `ramp_load`
    - `ramp_solar`
    - `load_gap_prevday`
    - `solar_gap_prevday`
    - `net_load_gap_prevday`
  - history 输入装配也已通过 `get_history_feature_name()` 走 forecast 侧列名

### 统一入口第一轮落地

- 已新增或收敛：
  - `main.py`
  - `cli/parser.py`
  - `pipelines/predict_pipeline.py`
  - `pipelines/train_pipeline.py`
  - `pipelines/evaluate_pipeline.py`
  - `pipelines/fusion_pipeline.py`
  - `services/predict_service.py`
  - `services/fusion_service.py`
  - `lightGBM/pipeline.py`
  - `TimesFM/pipeline.py`

- 统一入口当前策略：
  - `predict/train/evaluate/fusion` 四类命令已挂路由
  - `fusion` 路由直接调用现有 `fusion/run_full_fusion_suite.py`
  - `lightGBM` / `TimesFM` 采用路径接入式包装，不改其内部结构

### 并行调度当前状态

- `runners/registry.py` 已纳入：
  - `lightgbm`
  - `timesfm`
  - `sgdfnet`
  - `rt916`
  - `timemixer`
- `runners/executor.py` 当前分池：
  - CPU：`lightgbm`、`sgdfnet`
  - GPU：`timesfm`、`timemixer`、`rt916`

### 验证结果

- 导入与编译验证：
  - `main`
  - `cli.parser`
  - `pipelines.*`
  - `services.*`
  - `lightGBM.pipeline`
  - `TimesFM.pipeline`
  - `SGDFNet.pipeline`
  - `RT916_SpikeFusionNet.pipeline`
  - `TimeMixer.pipeline`
  均已通过 import / compile 基础检查

- 命令级烟测：
  - 命令：
    - `python main.py --pipeline predict --target dayahead --models lightgbm --date 2026-05-01 --data-path data/shandong_pmos_hourly.xlsx --max-cpu-workers 1 --max-gpu-workers 1`
  - 结果：
    - 统一入口成功调起 `lightGBM`
    - 首轮暴露列名兼容问题后已修复
    - 现已成功产出：
      - `outputs/unified_runs/lightgbm/dayahead/predictions.csv`
  - 标准化输出列：
    - `时刻`
    - `预测值`

### 当前判断

- 统一入口外壳已经从“占位骨架”进入“可调真实模型”的阶段
- `lightGBM` 已完成第一条端到端接通
- 下一步优先继续：
  - `TimesFM` 命令级烟测
  - `fusion` 路由命令级验证
  - `SGDFNet` / `RT916` / `TimeMixer` 外层整理与归档收敛

### 2026-06-16 补充：TimesFM / fusion 阻塞状态核实

- `TimesFM` 统一入口烟测已执行：
  - 命令：
    - `python main.py --pipeline predict --target realtime --models timesfm --date 2026-05-01 --data-path data/shandong_pmos_hourly.xlsx --max-cpu-workers 1 --max-gpu-workers 1`
  - 结论：
    - 当前不是统一入口路由失败
    - 而是 `TimesFM` 本地模型权重缺失
    - 现已改为在统一入口内直接报清晰阻塞信息，而不是继续走 HuggingFace 首次下载后抛底层异常

- 当前本地 `TimesFM` 模型目录：
  - `models/timesFM`
  - 仅存在缓存目录：
    - `.cache/huggingface/...`
  - 缺少正式推理所需文件：
    - `config.json`
    - `model.safetensors`

- `fusion` 路由层已修正为按目标调用正式脚本：
  - `both -> fusion/run_full_fusion_suite.py`
  - `dayahead -> fusion/run_dayahead_pipeline.py`
  - `realtime -> fusion/run_realtime_pipeline.py`

- `fusion` 命令级验证已执行：
  - 命令：
    - `python main.py --pipeline fusion --target dayahead --models all --date 2026-05-01 --fusion-work-dir fusion_runs/unified_entry_smoke_da2 --train-length-decision fusion_runs/repro_training_length_probe/repro_training_length_decision.json --conda-env epf-2`
  - 结果：
    - 路由层已进入正式 `fusion` 脚本
    - `LightGBM` 分支已开始正常执行
    - 仍被 `TimesFM` 权重缺失阻塞

### 当前冻结判断

- “统一入口本身不可用”这个判断已经被排除
- 当前阻塞点已收缩为：
  - `TimesFM` 本地模型文件不完整
- 在补齐 `TimesFM` 模型文件前：
  - `main.py --pipeline predict --models timesfm ...` 无法完成
  - `main.py --pipeline fusion ...` 也无法完成正式全链运行

### 低风险整理补充

- 已创建：
  - `RT916_SpikeFusionNet/_archive/`
- 已归档：
  - `RT916_SpikeFusionNet/README_RT916.md`
  - `RT916_SpikeFusionNet/FINAL_PACKAGING_SUMMARY.md`

### 2026-06-16 22:30 TimeMixer 融合链路兼容修复进展
- 已确认所有实际修改均在项目真实目录执行：`D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0`
- 已修复 `TimeMixer/pipeline_timemixer.py` 的双模式兼容问题：
  - 支持 `python TimeMixer/pipeline_timemixer.py` 直接脚本运行
  - 支持 `enhanced_pipeline.py` 同目录导入旧符号
- 已在兼容层恢复以下旧接口：
  - `TimeMixer`
  - `ElectricityDailyDataset`
  - `PastDecomposableMixing`
  - `evaluate_metrics`
- 已修复 `services/fusion_service.py` 对空 `--conda-env` 的透传问题：空字符串时不再继续下传，符合中文路径场景“直接用 Python”约束。
- 单模块验证：
  - 命令：`fusion/runners/run_timemixer_enhanced_export.py --task dayahead --test-start 2026-02-18 --test-end-exclusive 2026-05-01 ...`
  - 结果：已成功训练、预测并输出
    - `fusion_runs/unified_entry_may1_v2/dayahead_run/simulation/timemixer/predictions_day_ahead_last_month.csv`
    - `fusion_runs/unified_entry_may1_v2/dayahead_run/simulation/timemixer/metrics_day_ahead_by_period.csv`
- 当前判断：
  - `TimeMixer` 不再阻塞于导入/脚本入口层
  - 统一入口 `main.py --pipeline fusion ...` 已能推进到正式融合执行阶段
  - 下一步继续长时限重跑 `--pipeline fusion --target both`，确认是否还存在实时侧或学习器阶段阻塞

### 2026-06-16 23:25 统一 fusion 总链补齐结果
- 已补齐 `fusion_runs/unified_entry_may1_v2` 的正式融合交付物：
  - `dayahead_run/dayahead/fused_predictions.csv`
  - `realtime_run/realtime/fused_predictions.csv`
  - `joint_report/final_truth_vs_fusion.csv`
  - `joint_report/joined_for_arbitrage.csv`
  - `joint_report/metrics_arbitrage.csv`
  - `suite_metrics_summary.csv`
  - `suite_summary.json`
  - `per_model_smape.csv`
- 实际处理方式：
  - 未改模型核心逻辑
  - 先单独验证并补跑 `rt916` realtime formal 导出
  - 再复用 `fusion.pipeline_common` 现有聚合逻辑刷新 realtime formal long table 与 fused 输出
  - 最后调用现有 `save_joint_report` / `save_suite_summary` 生成联合报告
- 结论：
  - `main.py --pipeline fusion --target both --date 2026-05-01` 对应的正式产物链路已在工作目录中补齐
  - 当前剩余大项已不再是链路打通问题，而是文档要求的 5 个代表月烟测与重构前后差异核验

### 2026-06-17 根目录清理执行结果
- 已按 `docs/项目执行逻辑与陪跑步骤对齐.md` 与 `docs/项目清理与补全计划_20260616v2.md` 对项目进行清理。
- 根目录当前仅保留核心结构：
  - `main.py` / `README.md` / `requirements.txt` / `.gitignore` / `LICENSE`
  - `cli/` `pipelines/` `runners/` `services/` `utils/`
  - `data/` `docs/` `fusion/` `fusion_runs/` `models/`
  - `TimeMixer/` `SGDFNet/` `RT916_SpikeFusionNet/` `lightGBM/` `TimesFM/` `ExtremPriceClf/`
  - `_archive/`
- 已移动到 `_archive/` 的根目录杂项：
  - `START_HERE.md`
  - `MEMORY.md`
  - `run_monthly_repro_suite.py`
  - `compute_feb_benchmarks.py`
  - `compute_monthly_historical_benchmarks.py`
  - `run_feb_single_model_audit.py`
  - `lgbm_predict_diag.log`
  - `tmp_rt916_realtime_smoke.csv`
  - `output/`
  - `outputs/`
  - `TF/`
  - `scripts/`
- 已移动到模型内 `_archive/` 的内容：
  - `TimeMixer/outputs*`、`candidate_configs/`、`enhanced_*`
  - `SGDFNet/outputs/`、`research_control/`、`docs/PACKAGING_CHANGELOG.md`
  - `lightGBM/outputs/`、`lightGBM_oneday.py`
  - `TimesFM/output/`、`price_forecast_copy_分时段预测.py`
- 已补齐：
  - `RT916_SpikeFusionNet/dataprocess.py`
  - `RT916_SpikeFusionNet/model.py`
  作为根目录薄封装 re-export。
- 已补清理后的路径兼容：
  - `fusion/project_defaults.py` 回退到 `_archive/` 的输出/候选配置路径
  - `TimesFM` 统一改为通过真实归档脚本文件路径动态加载旧实现
  - `utils/io.py` 统一输出列规范化为 `prediction`
- 烟测：
  - 统一入口 `main.py --pipeline predict --target both --models all --date 2026-05-01` 已能继续跑过 `lightGBM`，并进入 `RT916` 内部逻辑。
  - 当前剩余报错为 `RT916` 日前路径内部列名 `日前电价` 适配问题，不属于本轮目录清理直接引入的根目录杂乱问题。
