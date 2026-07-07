# P3 Spike/Residual Ablation 报告（p3_rt_20260125_20260225_v1）

> 每个变体独立跑 shadow 修正并评估；before=original fused，after=corrected。

| 变体 | overallΔsMAPE | negΔsMAPE | spikeΔsMAPE | normalΔsMAPE | normalΔMAE | applied |
|------|------:|------:|------:|------:|------:|------:|
| 1_baseline_no_correction | +0.00 | +0.00 | +0.00 | +0.00 | +0.00 | 0 |
| 2_negative_only | -8.16 | -37.58 | +0.00 | +3.29 | +7.45 | 190 |
| 3_spike_only | +0.01 | +0.04 | -3.68 | +0.17 | +0.92 | 13 |
| 4_residual_only | +0.29 | +0.78 | +0.48 | +0.08 | +0.47 | 279 |
| 5_classifier_only_no_resid | -8.15 | -37.54 | -3.68 | +3.46 | +8.37 | 203 |
| 6_full_default_neg_spike_resid | -8.05 | -37.52 | -3.20 | +3.57 | +8.92 | 425 |
| 7_full_cap_off | -7.84 | -37.56 | -3.20 | +3.90 | +10.92 | 435 |
| 8_full_rollback_off | -8.15 | -37.52 | -3.44 | +3.44 | +8.59 | 525 |

## 结论要点
- 负价修正（变体2）：观察 negΔsMAPE 与 normalΔsMAPE 判断是否净收益。
- 残差修正（变体4）：观察 normalΔsMAPE 是否恶化（正常时段损伤主因假设）。
- cap/rollback（变体7/8）：观察护栏是否阻止过度修正与回滚保护效果。
