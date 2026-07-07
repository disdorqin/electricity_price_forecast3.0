# P1 Dayahead — cfg05 Candidate Dossier

> status: **candidate** (NOT shadow, NOT champion)
> registered: 2026-07-07
> reviewed by: P1 Dayahead Candidate Review (gate)

## 模型身份
- **name**: cfg05
- **version**: v_cfg05
- **family**: LightGBM (regression, MAE objective), rich feature frame
- **window**: 90d（rich 帧）；180d 消融 = 14.25%
- **source_repo**: epf-sota-experiment（本地 `models`）
- **run_id**: run_full_rich_v4（预测文件标记 run_full_rich_v4_cpu，CPU-only）

## 证据（4 硬月窗口 2025-11~2026-02, 120 天）
| 指标 | 值 |
| --- | --- |
| sMAPE_floor50 (overall) | 14.68% |
| sMAPE_floor50 (180d) | 14.25% |
| period 1_8 / 9_16 / 17_24 | 13.91 / 16.01 / 14.12 |
| spike / normal | 13.51 / 14.81 |
| NaN 计数 | 0 |
| negative_price_hit_rate | 72.4% |

## 对标（诚实口径）
- ✅ 优于忠实 2.5 ThreeStageLGBM（21.87%，同硬窗口）→ rich 特征收益确证
- ❓ 未证明优于 2.5 受信任冠军 best_two_average（11.85%，但为 easy 单月窗口）→ **不同窗口不可比**
- ❌ 历史 lgbm_spike_residual 11.27% 已因泄露作废，不得引用

## 优势
- rich 特征工程带来最大收益（21.87% → 14.68%）
- 17_24 与 spike 段均稳定（未退化）
- CPU-only 可复现

## 局限 / 风险
- 月度方差大：2026-02(11.67) 远优于其余(≈15–16)，4 月均值被拉低；去 2 月后 ≈15.6%
- 无负价分类器（hit_rate 仅 72.4%）→ 负价段弱项
- 引擎默认 GPU-preferred，脱离 daemon 直接跑会死锁（需 `--cpu-only` 或改默认）
- 训练耗时未记录（skip 路径 train_infer_time_s=0.0）

## 推荐 companion
- **xgboost_rich（14.72%）** 作 period/spike diversity：其在 1_8(13.36) 与 spike(12.99) 上最优，与 cfg05 互补 → 建议 period-aware ensemble(cfg05 + xgboost_rich)

## 升级到 shadow 的 gate（必须全部满足）
1. 同 4 硬月窗口重测 best_two_average / 忠实 2.5 融合，证明 cfg05 同窗口 ≤ 11.85% 量级
2. 补负价分类器后负价段误差可接受
3. period-aware ensemble 不劣于单模型
4. 候选包补齐 metrics.json / manifest.json / promotion_decision.json，FINAL_REPORT 修正对比口径

满足后重评 `recommended_status = shadow`（仍不直接 champion）。
