# P3 Spike/Residual Before/After 报告（p3_rt_20260125_20260225_v1_cand）

> shadow-only 评估；corrected_pred 仅存在于实验输出，未写入 submission_ready.csv。

## 1. 全局指标
- BEFORE (original fused): MAE=92.90, RMSE=136.37, sMAPE_floor50=40.88 (n=768)
- AFTER  (corrected)     : MAE=84.88, RMSE=133.68, sMAPE_floor50=34.22 (n=768)
- ΔsMAPE=-6.66, ΔMAE=-8.02

## 2. 子集指标
- **negative**: BEFORE MAE=117.89, RMSE=152.77, sMAPE_floor50=78.14 (n=213) | AFTER MAE=87.29, RMSE=146.46, sMAPE_floor50=53.75 (n=213)
- **spike**: BEFORE MAE=249.54, RMSE=345.08, sMAPE_floor50=39.95 (n=26) | AFTER MAE=230.34, RMSE=320.84, sMAPE_floor50=36.26 (n=26)
- **normal**: BEFORE MAE=75.14, RMSE=108.40, sMAPE_floor50=25.93 (n=529) | AFTER MAE=76.76, RMSE=110.67, sMAPE_floor50=26.26 (n=529)

## 3. 分时段 sMAPE_floor50
- period 1_8: BEFORE 43.84 → AFTER 38.58 (Δ-5.26)
- period 9_16: BEFORE 54.78 → AFTER 39.92 (Δ-14.85)
- period 17_24: BEFORE 24.02 → AFTER 24.16 (Δ+0.14)

## 4. 分类器性能
- negative (label≤-50.0): P=0.894 R=0.636 F1=0.743 (pos_rate=0.258, thr=0.8)
- spike   (label>500.0): P=0.118 R=0.154 F1=0.133 (pos_rate=0.034, thr=0.6)

## 5. 纠正统计
- applied_count=140, cap_hit=9, rollback=0
- avg|correction|=63.58, max|correction|=153.01
- neg_applied=127, false_positive_negative=5, missed_spike=22

## 6. 正常时段损伤（负向=更差）
- MAE Δ=+1.62, sMAPE Δ=+0.33 (n=529)

## 7. 安全自检
- nan_count=0, missing_hour_days=0, cutoff=D14, leakage=passed (features D-1 14:00 cutoff-safe; actual only used as training label), shadow_only=True
