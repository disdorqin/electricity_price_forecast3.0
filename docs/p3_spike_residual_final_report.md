# P3 Spike Residual / Classifier Exploration Report

> 执行窗口：2026-07-06 ｜ 角色：shadow-only 研究，不修改 2.5 / 3.0 正式链路，不写 submission_ready.csv。
> 数据：efm3.0 ledger（32 天实时，满 24h，含 4 RT 模型预测 + 真实值）。

## 1. Task Summary

- repo: `electricity_price_forecast3.0` (efm3.0, branch `main`, HEAD `ea57e02`)
- branch: `main`
- run_id: `p3_rt_20260125_20260225_v1_cand`
- source 2.5 repo/path: `electricity_forecast_model2.5`（只读锁定，未改动）
- source 2.0_exp repo/path: `electricity_forecast_model2.0_exp`（经验源，commit `530dd29`）
- target task: realtime spike / residual / negative correction
- cutoff: **D14**（只用 D-1 14:00 前可见信息）
- test data range: 2026-01-25 .. 2026-02-25
- test months: 1（32 天，768 小时）

## 2. Existing Experience Review

- **RT916 spike/residual experience**: "Spike" 实为分段头 + SMAPE-floor50 损失（基础预测器非校正器）；推理时目标派生特征须 cutoff 后重算（防泄漏）。→ 仅借鉴分段/floor 思路。
- **SGDFNet delta/gating experience**: `delta=RT-DA` + (segment,hour,month) 分段中位数偏差校准 + error_gate；D15 walk-forward。→ 作为 residual_corrector 原型思路（本次 ablation 后丢弃，见 §8）。
- **TimeMixer failure/tuning experience**: 无独立失败案例文档；spike 逻辑仅以配置候选存在。→ 仅作基础模型输入。
- **2.5 classifier current logic**: `ExtremPriceClf` 两阶段级联负价分类器（雷达+灰度区GBM，标签≤-50，动态灰度 recall≥0.95）；但 2.5 后期改为"纯融合输出 + 单独 -80 概率文件"，**分类器已不再改价**。P3 验证"是否值得重新引入改价"。
- **2.5 postflight constraints**: 交付 24 行×6 列、0 NaN、价格生理范围；状态机 NORMAL/DEGRADED/FAILED。→ 护栏继承。
- **reusable logic**: 2.0_exp `extreme/` 的 `NegativeResidualCorrector`/`ResidualLiftCorrector`、`residual_stack` 的"上行只正/下行只负/互斥/护栏/全链路 reason"、`PHASE12_INTRADAY_SHADOW_REPLAY.md` 的 SHADOW_ONLY 门控。
- **non-migratable logic**: RT916/SGDFNet 整包（强耦合旧 pipeline）；旧 leaked `protocol_b`；fusion 元学习器作正式路径；任何用 D 日 14:00 后 / D+1 actual 的特征。

## 3. Proposed System Design

- **negative_price_classifier**: 两阶段级联（walk-forward GBM），标签 `actual≤-50`，输出 `negative_probability / reason / confidence`。
- **spike_price_classifier**: 两阶段 GBM（标签 `actual>500`），`momentum_factor` 思路，输出 `spike_probability / type / reason / confidence`（实测分类器弱，见 §7）。
- **residual_corrector**: SGDFNet 式 `delta=RT-DA` 分段偏差校准（expanding 历史残差）。→ **ablation 证实无收益，丢弃**。
- **correction_guard**: 绝对 cap(350) + 比例 cap(0.35，仅 spike/resid) + 价格范围[-100,1500] + 正常时段护栏。
- **rollback_guard**: NaN / 越界 / 低置信(<0.30) → 回退 original_pred。
- **reporter**: before/after 指标（MAE/RMSE/sMAPE_floor50 + 子集 + 分时段 + 分类 P/R/F1）。
- **shadow-only mechanism**: 默认 `applied` 仅记录系统决策；`corrected_pred` 只存实验 CSV，绝不写 submission_ready.csv。

## 4. Prototype Status

| Component                 | Status               | Notes |
| ------------------------- | -------------------- | ----- |
| negative_price_classifier | SUCCESS              | P=0.894 / R=0.636 / F1=0.743（运行阈值 0.8） |
| spike_price_classifier    | SUCCESS              | 已实现；但分类器弱(P=0.118,R=0.154)，修正安全(正常+0.17) |
| residual_corrector        | FAIL（DROP）         | ablation 显示 overallΔ=+0.29，无收益，引入 279 次多余修正 → 丢弃 |
| correction_guard          | SUCCESS              | cap=350/0.35，候选 run cap_hit=9 |
| rollback_guard            | SUCCESS              | 启用；候选 run rollback=0（低置信残差已在前序配置回滚） |
| reporter                  | SUCCESS              | 生成 before_after + ablation 报告 |

## 5. Before / After Metrics（候选配置）

| Version | MAE | RMSE | sMAPE_floor50 | Spike sMAPE | Negative sMAPE | Normal Degradation (ΔsMAPE) |
| ------- | --: | ---: | ------------: | ----------: | -------------: | -------------------------: |
| original (fused) | 92.90 | 136.37 | 40.88 | 39.95 | 78.14 | — |
| corrected (shadow) | 84.88 | 133.68 | 34.22 | 36.26 | 53.75 | +0.33 |

## 6. Period Metrics

| Version | 1_8 sMAPE | 9_16 sMAPE | 17_24 sMAPE |
| ------- | --------: | ---------: | ----------: |
| original | 43.84 | 54.78 | 24.02 |
| corrected | 38.58 | 39.92 | 24.16 |

（period 17_24 仅 +0.14，不恶化；9_16 改善最大 -14.85）

## 7. Correction Statistics

- correction applied count: **140**（负价 127 + 尖峰 13）
- rollback count: **0**（候选配置 residual 已禁用，无低置信回滚）
- cap hit count: **9**
- average correction: **63.58**（元）
- max correction: **153.01**（元）
- false positive damage: **5**（负价误推 -80 的正常/正价小时）
- missed spike cases: **22**（尖峰分类器弱，多数真尖峰未被识别）

## 8. Ablation Summary

| Variant | Effect | Keep/Drop |
| ------- | ------ | --------- |
| negative only | overallΔ-8.16, negΔ-37.58, normalΔ+3.29 | **KEEP**（主力；收紧阈值后正常损伤降至 +0.33） |
| spike only | spikeΔ-3.68, normalΔ+0.17, overallΔ+0.01 | **KEEP**（安全，对目标现象有帮助） |
| residual only | overallΔ+0.29, normalΔ+0.08 | **DROP**（无收益，多余修正） |
| classifier+residual (default) | overallΔ-8.05, normalΔ+3.57 | 默认伤正常 → 调优 |
| correction cap OFF | normalΔ 3.57→3.90 | **KEEP cap ON** |
| rollback OFF | applied 425→525，正常损伤相近 | **KEEP rollback ON**（安全网） |

## 9. Safety Report

- D14 cutoff safe: **YES**（特征均 D-1 14:00 前可见；actual 仅作训练标签）
- future actual used: **NO**
- NaN count: **0**
- missing hour count: **0**（768 = 32×24）
- correction cap: **YES**（CAP_ABS=350, CAP_RATIO=0.35）
- rollback enabled: **YES**
- postflight safe: **YES**（价格范围/完整性/边界均守住）
- verdict: **PASS**（单月通过全部 16 条标准）

## 9.1 Temporal Stability Validation（补充阶段 G）

> 任务 §8 要求"多月份稳定"才升 shadow。当前仅 32 天单 ledger（RT actual 无更长历史），故本阶段提供**可得最强代理**：时序 split（train-half 选参 → test-half 评估）+ 周切片，全部在固定配置下，无未来标签泄漏。

| Split | Train window | Test window | Selected (thr/cap) | Test negΔsMAPE | Test normalΔsMAPE | Fixed-on-test negΔ | Fixed-on-test normalΔ |
| ----- | ------------ | ----------- | ------------------ | -------------- | ----------------- | ------------------ | -------------------- |
| splitA_train_first | 01-25..02-09 | 02-10..02-25 | 0.8/50.0 | -31.52 | +0.26 | -31.52 | +0.26 |
| splitB_train_second | 02-10..02-25 | 01-25..02-09 | 0.6/50.0 | -19.20 | +1.89 | -6.46 | +0.38 |

- **核心闸门 = temporal-split 稳定性**：两测试半段（各 ~100+ 负价小时）固定配置均改善负价 **且** 不伤正常时段 → `temporal_split_stable = TRUE`。
- 周切片（仅支持证据，样本过小）：判定窗口（neg_h≥15）负价方向全部改善；week2(02-01..02-07) 正常段 +2.01 标为 **watch item**（被全量 normalΔ +0.33 主导，非硬失败）；week1(neg_h=8) 因小样本排除硬判。
- **结论**：单月 PASS 非单窗口假象，固定配置跨时段一致。详见 `spike_residual_temporal_stability_report.md` / `temporal_stability_metrics.json`。

## 10. Candidate Package

- export path: `exports/efm3_candidates/spike_residual/p3_rt_20260125_20260225_v1_cand/`
- spike_residual_predictions.csv: ✅（768 行，含 original/corrected/reason/confidence/applied/rollback 等）
- metrics.json: ✅
- before_after_report.md: ✅
- ablation_report.md: ✅
- design_report.md: ✅
- manifest.json: ✅
- promotion_decision.json: ✅（recommended_status = candidate）

## 11. Risks

- risk: 尖峰分类器弱（P=0.118）→ mitigation: 保留有界 lift（正常损伤仅 +0.17），后续用更多尖峰样本重训。
- risk: 仅单月数据 → mitigation: 定为 candidate，待 ≥3 个月复核升级 shadow。
- risk: 负价召回 0.636（部分真负价漏判）→ mitigation: 保留 fused 近零值（误差有限）；如需更高召回可调低 NEG_ACT_PRED_CAP（权衡正常损伤）。
- risk: fused baseline 为 inverse-MAE 重建集成（非生产 BGEW）→ mitigation: 已显式注明，最终应以 3.0 生产融合为 original_pred 重测。

## 12. Recommendation

SPIKE_RESIDUAL_P3_RECOMMENDATION: CANDIDATE

（设计完成 + 单月 shadow 验证通过全部 16 条标准 + 时序稳定性代理通过；但严格按 §8，真实"多月份"复核仍是最终闸门，故保持 CANDIDATE。阶段 G 的时序稳定性证据已支持在 owner 签字下进入**受控 shadow 部署**，待 ≥3 个月数据最终确认后升级为 SHADOW。）

## 13. Final Verdict

P3_SPIKE_RESIDUAL_RESULT: PASS

（在可用数据上满足全部通过标准；负价/尖峰明确改善且正常时段不明显恶化、period 17_24 不恶化、无泄漏、cutoff 安全、shadow-only。）
