# Codex 执行文档：泄漏修复 + TimeMixer 互补调优（2026-06-16）

## 目标

1. 修复 SGDFNet 和 RT916 的数据泄漏，重跑 5 个代表月拿到可信数据
2. 借鉴 SGDFNet 的 delta-on-DA 架构优势，增强 TimeMixer 的 DA/RT 互补能力
3. 最终产出：可信的全模型对比表 + TimeMixer 增强版结果

**成功标准**：5 代表月 DA mean ≤ 15%（理想 ≤ 13%），RT mean ≤ 25%（理想 ≤ 20%）。

---

## 第一部分：全模型审计结果

### 审计结论一览

| 模型 | DA | RT | 泄漏等级 | 需修复 |
|------|----|----|---------|--------|
| **LightGBM** | ✅ 清洁 | ✅ 清洁 | 无 | 否 |
| **TimesFM** | ✅ 清洁 | ⚠️ 轻微 | RT 用了 D-1 晚间价格做上下文 | 暂不修 |
| **SGDFNet** | N/A | ❌ 严重 | delta_lag_1 + 滚动特征泄漏当日 RT | **是** |
| **RT916** | 未独立审计 | ❌ 严重 | 历史窗口泄漏 D-1 15:00 后的实际值 | **是** |

**LightGBM（1.0 参考基准）**：
- 24 个工程特征，全部 cutoff-safe
- RT：显式 D-1 14:00 cutoff（`infer_fix.py` line 221），y 值在 cutoff 后置 NaN
- DA：`shift(24)` 做滞后，`shift(1)` 做日统计，全部合规
- 这是标杆——所有修复应向 LightGBM 的严谨度看齐

**TimesFM（1.0 参考基准）**：
- Google 预训练基础模型，不做训练只做推理
- DA：清洁，上下文窗口在预测段之前
- RT：上下文窗口包含 D-1 晚间 RT 价格（对 segment 2/3），没有显式 cutoff
- 判定：设计层面差异而非 bug，暂不修改

### SGDFNet 泄漏详情（修正版）

**文件**：`SGDFNet/src/sgdfnet/data_contract.py`

**协议澄清**：D 日的**预测值**（负荷、光伏、风电等 forecast 列）在 D-1 就公布了，作为特征完全合法。唯一不合法的是用了 D 日的 **RT 实际价格**（RT 在当天才产生）。

**安全部分（无需修改）**：
- BLOCK 1（forecast 列，line 155-159）：用 D 日预测值 → ✅ 合法
- BLOCK 2（actual history shift(24)）：最佳配置已**关闭**（`include_actual_history_columns: false`）→ ✅ 无泄漏
- BLOCK 3-4（engineered forecast features）：用 forecast 列 → ✅ 合法
- BLOCK 8（weekly history shift(168)）：7 天前数据 → ✅ 安全
- BLOCK 9（forecast residual history）：默认**关闭** → ✅ 无泄漏

**问题 1（唯一的严重泄漏）：delta 滞后特征泄漏当日 RT**

`_delta_history_source = _rt_history_source - da_anchor`，其中 `_rt_history_source` 是**实际 RT 价格**。delta 系列特征基于此构建：

```python
# Block 5 (line 212-229) — delta 历史特征
delta_lag_1 = delta.shift(1)           # D日H时 → 用到D日H-1时RT ← 泄漏
delta_roll_mean_6 = delta.shift(1).rolling(6).mean()  # 同上
delta_roll_mean_24 = delta.shift(1).rolling(24).mean() # 同上
delta_roll_std_24 = delta.shift(1).rolling(24).std()   # 同上

# Block 6 (line 231-258) — TF 特征
lagged_delta = delta.shift(1)           # 同上
tf_delta_lowfreq_mean_12 = lagged_delta.rolling(12).mean()
tf_delta_highfreq_resid_12 = lagged_delta - tf_delta_lowfreq_mean_12
tf_delta_vol_12 = lagged_delta.rolling(12).std()
tf_delta_ramp_3 = lagged_delta.diff(3)
# ... 全部基于同一个泄漏的 shift(1)

# Block 10 (line 327-357) — 段内统计
delta_same_hour_lag_1d = groupby("hour").shift(1)     # 用到昨天同时段 ← 可能安全
delta_same_hour_roll_mean_7d = groupby("hour").shift(1).rolling(7).mean()  # 用到7天数据
```

**问题 2（严重）：训练/推理数据不对称**

训练时直接用 `raw_df`（`protocol_b_cutoff.py` line 302），特征可以看到完整的当日 RT。推理时用 `visible_df`，post-cutoff 的 RT 被 DA 替换。这意味着：
- 模型在训练时学到了依赖当日 RT 的模式
- 推理时这些模式失效，因为当日 RT 被替换为 DA（delta 变为 0）
- 结果：train/serve skew

**问题 3（中等）：负荷残差滚动特征泄漏**

```python
# Block 9 (line 299-326)
hist_load_resid_roll_mean_24 = load_resid.shift(1).rolling(24).mean()  # 用到当日实际负荷
hist_netload_resid_roll_mean_24 = netload_resid.shift(1).rolling(24).mean()  # 同上
```

### RT916 泄漏详情（修正版）

**文件**：`RT916_SpikeFusionNet/src/rt916_spikefusionnet/core.py` 和 `dataprocess.py`

**协议澄清**：D 日的预测值（负荷/光伏/联络线等）在 D-1 公布，用作特征合法。

**问题：历史窗口用了 actual 列而非 forecast 列**

RT916 的 history patch（8 步）使用 `HISTORY_INPUT`，包含以下**实际值列**：
- `直调负荷实际值`、`联络线受电负荷实际值`、`新能源总加实际值`、`竞价空间实际值`
- 以及从 actual 列派生的：`总用电量`、`其他负荷总加`、`净负荷`、`新能源渗透率`、`空间_新能源比`

future patch 使用 `FUTURE_INPUT`，包含对应的**预测值列** → ✅ 无问题。

**泄漏机制**：预测 D 日时，history 的 8 步覆盖 D-1 的部分时段。cutoff 是 D-1 15:00：
- **Stage 1（1-8 时）**：✅ 清洁 — history 止于 D-1 08:00，全在 cutoff 前
- **Stage 2（9-16 时）**：⚠️ 轻微 — history 包含 1 小时 cutoff 后的 actual（D-1 16:00）
- **Stage 3（17-24 时）**：❌ 严重 — history 包含 8 小时 cutoff 后的 actual（D-1 17:00~00:00）

附加：`ramp_load`/`ramp_solar` 是 actual 列的 `.diff()`，在全数据集上计算，cutoff 后没重算。

**但注意**：RT916 已经正确遮蔽了 RT 价格目标列（`apply_asof_cutoff_for_inference` line 268），问题只出在**负荷/光伏等非价格特征用了 actual 而非 forecast**。

---

## 第二部分：SGDFNet 修复方案

### 修复原则

**Cutoff 规则**：在 D-1 15:00 做预测决策。对于 D 日全部 24 小时的预测：
- DA 价格：D 日全天可用（D-1 ~14:00 发布）✅
- 电网预测（负荷/风/光/联络线）：D 日全天可用（D-1 发布）✅
- RT 价格：仅 D-1 15:00 及之前可用（= business hour 14, 0-indexed）
- 实际负荷/风/光：仅 D-1 15:00 及之前可用

**保留优势的策略**：SGDFNet 的 `shift(1)` 本来是想捕捉最近 1 小时的价格动态。修复后我们用三种替代特征来保留这个信息优势：
1. **逐小时 lag**：每行用 shift(H+9) 取 cutoff 前最后一小时的 delta（保留小时级粒度）
2. **日级统计**：用 D-1 的 0-14 时段计算均值/标准差（保留日内波动信号）
3. **周级 lag**：shift(168) 取 7 天前同时段（完全安全且有周期性信息）

### 修复清单

#### 2.1 新增 cutoff-safe delta 源

在 `preprocess_dataframe()` 中，在现有 `_delta_history_source` 构建之后，新增：

```python
# ── cutoff-safe delta 构建 ──
# 1. 逐日分组，计算 0-14 时段（cutoff 前）的 delta 统计
out["_date"] = out["timestamp"].dt.date
out["_bh"] = out["timestamp"].apply(business_hour)  # 0-indexed, 00:00=0

# 2. 逐行 cutoff-safe 最近小时 delta（保留小时级粒度）
# 对于 D 日 H 时（business hour），cutoff 前最后一行是 D-1 日 hour=14
# 偏移量 = H + 9（当 H=0 时偏移 9 → 到 D-1[15]; H=23 时偏移 32 → 到 D-2[16]）
cutoff_safe_delta = pd.Series(np.nan, index=out.index)
for i in out.index:
    H = out.loc[i, "_bh"]
    shift_amt = int(H) + 9  # H=0→9, H=14→23, H=23→32
    if i - shift_amt >= out.index.min():
        cutoff_safe_delta.loc[i] = out["_delta_history_source"].iloc[i - shift_amt]

out["_delta_cutoff_safe"] = cutoff_safe_delta
```

#### 2.2 Block 5 修复：delta 历史特征

```python
# 替换原有 Block 5 (line 212-229)
delta_cs = out["_delta_cutoff_safe"]

# 逐行 lag（cutoff 前最后一小时）
out["delta_lag_1"] = delta_cs  # 现在每行取的是 cutoff 前最近的 delta

# lag-24（昨天同时段 delta，始终安全）
out["delta_lag_24"] = out["_delta_history_source"].shift(24)

# 日级滚动统计（用 D-1 的 0-14 时段 delta 计算均值/标准差）
# 先算每天的 0-14 时段统计
daily_partial = (
    out[out["_bh"] <= 14]
    .groupby("_date")["_delta_history_source"]
    .agg(["mean", "std"])
)
daily_partial.columns = ["partial_delta_mean", "partial_delta_std"]

# 广播到 D 日（shift 1 天）
date_to_stats = {}
dates = sorted(out["_date"].unique())
for i in range(1, len(dates)):
    prev_date = dates[i - 1]
    curr_date = dates[i]
    if prev_date in daily_partial.index:
        date_to_stats[curr_date] = daily_partial.loc[prev_date]

out["delta_roll_mean_24"] = out["_date"].map(
    lambda d: date_to_stats.get(d, {}).get("partial_delta_mean", np.nan)
)
out["delta_roll_std_24"] = out["_date"].map(
    lambda d: date_to_stats.get(d, {}).get("partial_delta_std", np.nan)
)

# 6 小时滚动均值：用 cutoff_safe_delta 的 rolling
out["delta_roll_mean_6"] = delta_cs  # 简化为与 lag_1 相同（单点值）
# 如果需要多小时窗口，可以构建 cutoff-safe 的 6 小时窗口

out["delta_abs_roll_mean_24"] = out["delta_roll_mean_24"].abs()
```

#### 2.3 Block 6 修复：TF 特征

```python
# 替换原有 Block 6 (line 231-258)
lagged_delta = out["_delta_cutoff_safe"]  # 替代原来的 delta.shift(1)

out["tf_delta_lowfreq_mean_12"] = lagged_delta  # 简化为单点
out["tf_delta_lowfreq_mean_24"] = out["delta_roll_mean_24"]  # 复用日级统计
out["tf_delta_highfreq_resid_12"] = lagged_delta - out["tf_delta_lowfreq_mean_24"]
out["tf_delta_highfreq_resid_24"] = 0.0  # 简化
out["tf_delta_vol_12"] = out["delta_roll_std_24"]
out["tf_delta_vol_24"] = out["delta_roll_std_24"]
out["tf_delta_ramp_3"] = lagged_delta.diff(3)  # diff on cutoff-safe series
out["tf_delta_ramp_6"] = lagged_delta.diff(6)

# 同时段周级特征（安全）
out["tf_delta_same_hour_lowfreq_7d"] = (
    out.groupby("hour")["_delta_history_source"]
    .transform(lambda x: x.shift(1).rolling(7, min_periods=1).mean())
)
out["tf_delta_same_hour_highfreq_7d"] = lagged_delta - out["tf_delta_same_hour_lowfreq_7d"]
```

#### 2.4 Block 9 无需修复

BLOCK 9（forecast residual history，line 299-326）使用 actual 列的 `shift(1).rolling(24)` 计算负荷残差。但在最佳配置中 `include_forecast_residual_history_features: false`（默认关闭），这些特征**不参与模型**。无需修改。

如果将来想启用此 block，需要将 `shift(1).rolling(24)` 改为日级统计广播（同 Block 5 的修复方式）。

#### 2.5 Block 10 修复：段内统计

```python
# 替换 Block 10 (line 327-357)
# delta_same_hour_lag_1d = groupby("hour").shift(1)  ← 用昨天同时段
# 这个本身是安全的（shift(1) on daily groups = 昨天同小时）
# 但需要确保 _delta_history_source 在 cutoff 后不被直接使用

# 改为用 cutoff-safe 版本
out["delta_same_hour_lag_1d"] = (
    out.groupby("hour")["_delta_cutoff_safe"].transform(lambda x: x)
)
# 7 天滚动：安全（7 天的 shift(1) 不会泄漏）
out["delta_same_hour_roll_mean_7d"] = (
    out.groupby("hour")["_delta_history_source"]
    .transform(lambda x: x.shift(1).rolling(7, min_periods=1).mean())
)
out["delta_same_hour_roll_std_7d"] = (
    out.groupby("hour")["_delta_history_source"]
    .transform(lambda x: x.shift(1).rolling(7, min_periods=2).std())
)
out["delta_same_hour_abs_roll_mean_7d"] = (
    out.groupby("hour")["_delta_history_source"].abs()
    .transform(lambda x: x.shift(1).rolling(7, min_periods=1).mean())
)
```

#### 2.6 训练/推理对齐修复

**当前问题**：训练用 `raw_df`（完整数据），推理用 `visible_df`（遮蔽数据）。模型学到依赖当日 RT 的模式，推理时失效。

**修复**：训练也使用 cutoff-safe 特征。

在 `protocol_b_cutoff.py` 的训练环节（约 line 302），改为：
```python
# 原来：train_df = preprocess_dataframe(raw_df, feature_config)
# 改为：
train_df = preprocess_dataframe(raw_df, feature_config, 
                                 rt_history_col="visible_rt_anchor",
                                 actual_history_source_map=visible_actual_map)
```

或者更简单的方案：直接统一使用 `_delta_cutoff_safe` 系列特征，不依赖 `rt_history_col` 切换。

**最低限度修复**：如果完整修复太复杂，至少做到：
1. `delta_lag_1` → `delta.shift(24)`（最简单，损失一些精度但绝对安全）
2. 所有 `shift(1).rolling()` → 用日级统计广播替代
3. 训练和推理用相同的特征函数（不切换数据源）

#### 2.7 新增 DA 衍生特征（借鉴 SGDFNet 优势）

当前 SGDFNet 已经用 `da_anchor` 做了一些交叉特征（Block 4），但修复后可以补充：

```python
# 新增 DA 衍生特征
out["feat_da_level"] = out["da_anchor"]  # DA 价格水平
out["feat_da_net_load_interaction"] = out["da_anchor"] * pred_net_load
out["feat_da_solar_interaction"] = out["da_anchor"] * pred_renewable
out["feat_da_space_interaction"] = out["da_anchor"] * pred_space
```

这些 TimeMixer 也可以借鉴（见第四部分）。

---

## 第三部分：RT916 修复方案

### 3.1 核心修复：历史窗口改用预测值列

**文件**：`RT916_SpikeFusionNet/src/rt916_spikefusionnet/dataprocess.py`

当前 `HISTORY_INPUT` 使用 `实际值` 列。修复方案：**将 history 窗口的列从 actual 切换为 forecast**。

```python
# 原来的 HISTORY_INPUT（actual 列）：
# "直调负荷实际值", "联络线受电负荷实际值", "新能源总加实际值", "竞价空间实际值"

# 修改为（forecast 列）：
# "直调负荷预测值", "联络线受电负荷预测值", "新能源总加预测值", "竞价空间预测值"
```

同时修改派生特征的计算：
```python
# 原来从 actual 列计算：
# 总用电量 = 直调负荷实际值 + 联络线受电负荷实际值 + 新能源总加实际值 + 其他负荷总加
# 净负荷 = 直调负荷实际值 - 新能源总加实际值
# 新能源渗透率 = 新能源总加实际值 / (直调负荷实际值 + 1e-5)
# 空间_新能源比 = 竞价空间实际值 / (新能源总加实际值 + 1.0)

# 修改为从 forecast 列计算：
# 总用电量预测值 = 直调负荷预测值 + 联络线受电负荷预测值 + 新能源总加预测值 + 其他负荷总加预测值
# 净负荷预测值 = 直调负荷预测值 - 新能源总加预测值
# 新能源渗透率预测值 = 新能源总加预测值 / (直调负荷预测值 + 1e-5)
# 空间_新能源比预测值 = 竞价空间预测值 / (新能源总加预测值 + 1.0)
```

### 3.2 Ramp 特征修复

`ramp_load` 和 `ramp_solar` 改为从 forecast 列计算：
```python
# 原来：ramp_load = df["直调负荷实际值"].diff()
# 改为：ramp_load = df["直调负荷预测值"].diff()
# ramp_solar 同理
```

### 3.3 注意事项

- 这个修改会让 history 和 future 窗口都使用预测值，保持一致性
- 预测值不如实际值准确，但这是协议要求——在 D-1 15:00 时只有预测值可用
- Stage 1（1-8 时）原本就是清洁的，改用 forecast 后可能精度略降（因为预测有误差），但更合规
- 预期修复后 Stage 3 的 RT 会变差一些（之前靠泄漏获得了虚高成绩），但这是回归真实水平

---

## 第四部分：TimeMixer 最终调优——借鉴 + 互补

### 4.1 当前 TimeMixer 架构

**Past features（23 维）**：目标价格、load/wind/solar/interconnect/bidding/renewable、net_load、各比率、ramps、rolling stats（3/6/24h）、diff(24)/diff(168)、weekly deviation、rank、is_peak、sin/cos hour

**Future features（23 维）**：load/wind/solar/interconnect/bidding/renewable、net_load、各比率、ramp_load、hour、business_hour、is_peak、is_solar、sin/cos hour、month、dayofweek、is_weekend、**da_values**、baseline_values

**关键观察**：TimeMixer 的 future features **已经包含 DA 价格**（`da_values`），但当前训练目标是 RT 绝对值，没有利用 DA 做残差预测。

### 4.2 改进 1：Delta-on-DA 训练模式（最大杠杆）

**思路**：训练 RT 模型时，目标从 `RT` 改为 `delta = RT - DA`。推理时 `RT_pred = DA_actual + delta_pred`。

**为什么有效**：
- delta 的方差远小于 RT 绝对值（DA 已经解释了大部分价格变动）
- 模型只需学习 DA 和 RT 的偏差，而不是从零预测 RT
- 特别有利于 1_8 和 17_24 段（DA ≈ RT 的时段），对 9_16 段（DA 和 RT 差异大）可能帮助也可能不利

**实现**：在 `repro_pipeline.py` 中新增 `target_mode = "delta_on_da"`：

```python
def make_sample(..., target_mode="direct"):
    ...
    if target_mode == "delta_on_da":
        # 训练目标 = RT - DA（delta）
        da_hist = hist["day_ahead_clearing_price"].to_numpy(float)
        target_vals = target - da_hist  # delta = RT - DA
    elif target_mode == "residual_blend":
        ...  # 现有逻辑
    ...
```

推理侧：
```python
# 预测 delta，然后还原
delta_pred = model(past, future)
rt_pred = da_actual + delta_pred
```

**注意事项**：
- DA 模型不受影响，仍然直接预测 DA
- 对 segment 训练，每个段的 delta 分布不同，可能有利于分段学习
- 如果 9_16 段 delta 方差大，可以给 9_16 段加权训练

### 4.3 改进 2：DA 价格交互特征

当前 future features 有 `da_values` 但只是独立一维。增加 DA 与其他特征的交互：

```python
# 在 make_future_features 中增加
da_net_load = da_values * net_load        # DA × 净负荷
da_solar = da_values * solar              # DA × 光伏
da_bidding = da_values * bidding_space    # DA × 竞价空间
da_supply_demand = da_values * (pred_supply - pred_load)  # DA × 供需缺口
```

新增 4 维 future features（从 23 维 → 27 维）。

### 4.4 改进 3：Past features 增加 delta 历史

当前 past features 的 rolling stats 基于目标价格（RT 或 DA）的绝对值。增加基于 delta 的历史特征：

```python
# 在 make_past_features 中增加
da_hist = hist["day_ahead_clearing_price"].to_numpy(float)
delta_hist = target - da_hist  # 历史 delta

delta_rolling_mean_24 = pd.Series(delta_hist).rolling(24, min_periods=1).mean()
delta_rolling_std_24 = pd.Series(delta_hist).rolling(24, min_periods=1).std()
delta_lag_24 = pd.Series(delta_hist).shift(24)  # 昨天同时段 delta
```

新增 3 维 past features（从 23 维 → 26 维）。

### 4.5 改进 4：DA/RT 互补融合策略

TimeMixer 同时有 DA 模型和 RT 模型。利用 delta-on-DA 实现互补：

**方案 A：级联预测（推荐优先尝试）**
1. DA 模型预测 `DA_pred`（标准 TimeMixer DA）
2. RT 模型预测 `delta_pred`（delta-on-DA 模式的 TimeMixer RT）
3. 最终 RT = `DA_actual + delta_pred`（如果 DA 实际值已出）
4. 或者 RT = `DA_pred + delta_pred`（如果 DA 实际值还没出）

**方案 B：双模型加权融合**
1. DA 模型预测 `DA_pred`
2. RT 模型（delta-on-DA）预测 `delta_pred` → `RT_cascade = DA_actual + delta_pred`
3. RT 模型（标准模式）预测 `RT_direct`
4. 按段加权：`RT_final = w * RT_cascade + (1-w) * RT_direct`
5. 权重 w 按段设定：1_8/17_24 给 cascade 更高权重（DA≈RT），9_16 给 direct 更高权重（DA 和 RT 差异大）

**方案 C：regime-aware 融合**
1. 用月级特征（光伏占比、负荷波动率）判断 regime
2. 高光伏 regime → delta-on-DA 权重低（DA 和 RT 差异大且不稳定）
3. 低光伏 regime → delta-on-DA 权重高（DA ≈ RT）

### 4.6 改进 5：从 SGDFNet 借鉴特征

SGDFNet 有而 TimeMixer 没有的特征：

| SGDFNet 特征 | TimeMixer 对应 | 建议 |
|-------------|---------------|------|
| `feat_pred_pressure_ratio` = space / renewable | 无 | 加入 future features |
| `feat_pred_renewable_share` = renewable / load | 有（类似的 solar/load） | 已有 |
| `feat_pred_supply_sum` = local + link + renewable | 无 | 加入 future features |
| `graph_group_pressure_x_riskhour` = space × is_risk_hour | 无 | 加入 future features |
| `graph_group_da_x_segment` = DA × segment | 无 | 加入 future features |

建议新增 3-4 维 future features。

---

## 第五部分：执行顺序与验证

### 阶段 1：SGDFNet 修复 + 重跑（优先级最高）

1. 按第二部分修复 `data_contract.py` 和 `protocol_b_cutoff.py`
2. 在 5 个代表月重跑，同时报 raw SMAPE 和 capped SMAPE
3. 产出：修复前后对比表

**预期**：修复后 SGDFNet RT 会大幅下降（从 7-15% 到 20-30% 区间），但应该仍有一定竞争力。

### 阶段 2：RT916 修复 + 重跑

1. 按第三部分修复 `core.py`
2. 在 5 个代表月重跑
3. 产出：修复前后对比表

### 阶段 3：TimeMixer delta-on-DA 实验

1. 在 `repro_pipeline.py` 新增 `target_mode = "delta_on_da"`
2. 先在 2026-05 做烟测（这个月 direct_24 效果好）
3. 如果 delta-on-DA 在 2026-05 改善 RT → 扩到 5 个代表月
4. 如果没改善 → 放弃 delta-on-DA，尝试其他改进（DA 交互特征、delta 历史特征）

### 阶段 4：全模型公平对比 + 融合

1. 用修复后的 SGDFNet/RT916 + 原始 LightGBM/TimesFM + 增强版 TimeMixer
2. 在 5 个代表月计算统一 SMAPE 对比表
3. 设计跨模型融合规则
4. 验证融合结果

### 验证标准

每个阶段完成后：
- [ ] 5 个代表月全部跑完
- [ ] 同时报 raw SMAPE 和 capped SMAPE（或 sMAPE clip50，和 benchmark 一致）
- [ ] 对比修复前/基线版本的差异
- [ ] 结果记录到冲刺日志

---

## 第六部分：文件位置速查

| 资源 | 路径 |
|------|------|
| SGDFNet 特征代码 | `SGDFNet/src/sgdfnet/data_contract.py` |
| SGDFNet cutoff 协议 | `SGDFNet/src/sgdfnet/protocol_b_cutoff.py` |
| SGDFNet 指标 | `SGDFNet/src/sgdfnet/metrics.py` |
| RT916 核心代码 | `RT916_SpikeFusionNet/core.py` |
| TimeMixer 复现链 | `TimeMixer/repro_pipeline.py` |
| TimeMixer 骨干 | `TimeMixer/backbones.py` |
| LightGBM RT（参考） | `lightGBM/train_fix.py` + `lightGBM/infer_fix.py` |
| LightGBM DA（参考） | `lightGBM/train_da_fix.py` + `lightGBM/infer_da_fix.py` |
| 主数据 | `epf/data/shandong_pmos_hourly.csv` |
| 历史基准 | `fusion_runs/historical_monthly_benchmarks/monthly_historical_benchmarks.csv` |
| 冲刺日志 | `docs/TimeMixer冲刺日志.md` |
| conda 环境 | epf-2（Python: `D:\computer_download\environment\conda\epf-2\python.exe`）|

---

## 约束

1. **目标驱动**：5 代表月 DA ≤ 15%、RT ≤ 25%
2. **自主决策**：根据中间结果自行决定下一步
3. **快速迭代**：优先跑实验，少写文档
4. **及时换方向**：连续 2 次改善 < 0.5pp 就换
5. **统一口径**：SMAPE 用 floor-50 clip（`smape_clip50`），和 benchmark 一致
6. **记录到冲刺日志**
7. **每阶段完成后**：给我完整对比表，我再决定下一步
