# P1 Dayahead — Feature Engineering Lessons

> 沉淀自 P1 日前探索（rich vs 24f 双帧对照）。供 3.0 特征工程复用。

## 1. rich 特征 >> 2.5 特征（最大收益）
- 忠实 2.5 24 特征帧（三段 + 光伏负价分类）：best = 20.15%，baseline_lgbm25 = 21.87%
- rich 帧（~55 列：lag / 同小时 / momentum / calendar / volatility / 交互，90d 窗口）：best = 14.68%
- **同硬窗口（2025-11~2026-02）下，特征丰富度带来 +5.47pp**，且收益来自特征而非模型族（rich 帧内 LightGBM/XGBoost/CatBoost 差异仅 1pp 内）

## 2. 特征集必须 D+1 安全（红线）
- 所有 rich 特征仅用 D-1 14:00 前可见信息
- 评估用 `y_true` 仅作打分目标，绝不进特征（历史 lgbm_spike_residual 11.27% 因 `y_true` 泄露作废，是前车之鉴）
- 候选包 schema 已审查：无 leakage 列、无 D+1 特征

## 3. 窗口长度
- 90d → 180d：14.68% → 14.25%（+0.43pp），更长窗口方向正确但增益递减
- 365d 受数据覆盖(2022-01 起)与 2026-06 仅 19 天限制，不稳健
- 建议固化 90d/180d 为候选窗口，不盲目加长

## 4. 负价是 rich 帧的弱项
- rich 模型无负价分类器，`negative_price_hit_rate` cfg05=72.4%
- 2.5 用 0.7 阈值 -80 校正；rich 帧应补同类分类器后再评估负价段
- 预测下限 -80（山东负价地板）合法，但命中率待提升

## 5. 模型族结论
- rich 帧：LightGBM(cfg05) ≈ XGBoost(xgboost_rich) ≫ CatBoost(catboost_rich)
- cfg05 强于 9_16/17_24，xgboost_rich 强于 1_8/spike → **period-aware ensemble 互补**
- CatBoost 在 rich 帧无优势，暂停投入

## 6. 训练可靠性（本机）
- GPU 路径不稳定（lightgbm 死锁 + catboost 崩溃 + 睡眠杀进程）→ 全面 CPU-only
- 引擎需把 CPU 设为默认（当前 GPU-preferred），避免脱离 daemon 时死锁
- 固化 feature builder 与 config snapshot，关闭 daemon 常驻以省资源
