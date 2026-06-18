# 三大训练加速技巧讲解:AMP / DataLoader 优化 / TF32

> 编写日期:2026-06-18
> 适用本仓库两个 PyTorch 模型:**RT916_SpikeFusionNet** 和 **TimeMixer**。SGDFNet 是 sklearn,不需要这三个。

---

## 0. 三者关系一句话版

| 技巧 | 解决什么 | 动哪一行 |
|---|---|---|
| **TF32** | GPU 跑 FP32 matmul 太慢 | `torch.backends.cuda.matmul.allow_tf32 = True` |
| **AMP 混合精度** | FP16/BF16 比 FP32 快 + 省显存 | `with torch.autocast(...): ... ; scaler.scale(loss).backward()` |
| **DataLoader 优化** | GPU 算太快,数据加载跟不上,GPU 空转 | `num_workers` / `pin_memory` / `persistent_workers` / `prefetch_factor` / `non_blocking` |

**关系**:TF32 是"0 成本"开关(几乎不要写代码),AMP 是"中等改动"(包一个 autocast),DataLoader 是"环境/构造参数"调整(改 DataLoader 实例化那几行)。**三者是正交的,通常一起开**。

---

## 1. TF32(TensorFloat-32)

### 1.1 是什么

NVIDIA Ampere 架构(RTX 30/40 系列、A100)起引入的一种**截断的 FP32**:
- 正常 FP32:**23 位尾数**(高精度)
- TF32:**10 位尾数**(和 FP16 一样精度)+ **8 位指数**(和 FP32 一样范围)

效果:对深度学习 matmul/卷积,**FP32 精度 + 接近 FP16 的速度**。指数范围不缩,不会因为数值大而下溢/上溢。

### 1.2 收益

- **RTX 4060 Laptop**(Ampere):matmul 一般 **1.3x ~ 1.8x** 加速
- 精度损失极小:在 backward、attention score、embedding 这种容差宽的地方几乎无感
- 显存占用不变(还是 FP32 存)

### 1.3 怎么开(2 行)

```python
import torch
torch.backends.cuda.matmul.allow_tf32 = True   # 关键:开 matmul 的 TF32
torch.backends.cudnn.allow_tf32 = True         # 关键:开 cuDNN 卷积的 TF32
```

PyTorch 1.12+ 默认是 `True`,但**显式写出来最稳**(不同版本默认值会变)。

### 1.4 在本项目里影响范围

- **RT916_SpikeFusionNet** 的 `AnnualSpikeGatedTimesNet`:内含 Linear / Conv1d,matmul + 卷积都吃到
- **TimeMixer** 的 `PastDecomposableMixing`:多尺度 attention + MLP,主要是 matmul
- **SGDFNet** 用的 sklearn,跟 TF32 无关
- **TimesFM** 是基础模型推理,主调 forward,加速比例略小

### 1.5 风险

- 极少数任务(科学计算 / 极高精度回归)可能受影响;电价预测的 SMAPE 容差很大,**基本没风险**
- 对推理无副作用:模型权重还是 FP32,导出/保存不变

---

## 2. AMP(Automatic Mixed Precision)混合精度

### 2.1 是什么

训练时,**前向 / 反向用 FP16(或 BF16),权重和优化器状态用 FP32**。好处:

| 维度 | 收益 |
|---|---|
| **速度** | GPU 矩阵单元对 FP16 有 2-8x 理论加速(Ampere + tensor core) |
| **显存** | 激活值/中间量减半 → batch_size 可以翻倍 |
| **带宽** | 显存读写减半 |

风险:FP16 **下溢**(小数变 0)和 **上溢**(梯度爆炸)。AMP 用两个机制解决:

1. **autocast**:自动决定哪些 op 用 FP16(矩阵乘、卷积),哪些保留 FP32(loss、softmax、layer norm)
2. **GradScaler**:把 loss 乘以一个缩放因子 S(比如 2^16),让 FP16 梯度也"放大到能表示",backward 完再除回 S

### 2.2 BF16 vs FP16

PyTorch 1.10+ 支持 `bfloat16`(脑浮点):**8 位指数 + 7 位尾数**。

| | FP16 | BF16 |
|---|---|---|
| 指数位 | 5 | **8**(和 FP32 一样) |
| 尾数位 | 10 | 7 |
| 范围 | 容易溢出 | **不会溢出** |
| 精度 | 高 | 略低 |
| 需不需要 GradScaler | 需要 | **不需要** |

**RTX 4060 Laptop(Ampere)支持 BF16 加速**;本项目用 BF16 更省心(不用 scaler)。

### 2.3 怎么写(标准模板)

**FP16 + GradScaler(传统写法)**:
```python
from torch.cuda.amp import autocast, GradScaler
scaler = GradScaler(enabled=use_amp)

for batch_x, batch_y in train_loader:
    batch_x = batch_x.to(device, non_blocking=True)
    batch_y = batch_y.to(device, non_blocking=True)

    optimizer.zero_grad(set_to_none=True)        # 推荐:set_to_none 比置 0 省一次写入
    with autocast(enabled=use_amp, dtype=torch.float16):
        pred = model(batch_x)
        loss = criterion(pred, batch_y)

    scaler.scale(loss).backward()                 # 关键:用 scaler 包装
    scaler.unscale_(optimizer)                    # 反缩放(为 clip_grad_norm_)
    torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
    scaler.step(optimizer)                        # 关键:内部会检查 inf/NaN
    scaler.update()
```

**BF16(更简洁,无 scaler)**:
```python
from torch.amp import autocast

for batch_x, batch_y in train_loader:
    batch_x = batch_x.to(device, non_blocking=True)
    batch_y = batch_y.to(device, non_blocking=True)

    optimizer.zero_grad(set_to_none=True)
    with autocast(device_type='cuda', enabled=use_amp, dtype=torch.bfloat16):
        pred = model(batch_x)
        loss = criterion(pred, batch_y)

    loss.backward()
    torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
    optimizer.step()
```

### 2.4 在本项目里影响范围

- **RT916_SpikeFusionNet** `core.py:585-594` 训练循环:标准 forward + loss + backward,**直接套 autocast 就行**
- **TimeMixer** `repro_pipeline.py:1416-1433`:同样的标准循环,直接套
- **SGDFNet**:不适用
- **TimesFM 推理**:推理时可以用 `autocast(dtype=torch.bfloat16)` 包 forward,效果有限但能省显存

### 2.5 风险

- 如果 loss 里有自定义 op(比如 `torch.where`、`tensor[tensor]` 索引),autocast 偶尔会做错类型 → 用 `torch.cuda.amp.autocast(..., cache_enabled=False)` 重置
- 极少数 loss 对精度敏感(本项目 `AnnualProtectedCappedLoss`、TimeMixer 的 risk-weighted L1 都没问题)
- 必须保留 `model.eval()` 后的纯 FP32 推理(或不写 autocast)以保证 SMAPE 计算稳定

---

## 3. DataLoader 优化

### 3.1 瓶颈在哪

GPU 算得快,但每步要从磁盘/内存读 batch → 预处理 → 转 tensor。**GPU 经常空等 CPU**。DataLoader 优化的目标是**让 GPU 永远有数据可算**。

PyTorch DataLoader 的并行模型:

```
main process
   └── DataLoader (主进程)
         ├── worker 0 ── 读 batch 0
         ├── worker 1 ── 读 batch 1
         ├── worker 2 ── 读 batch 2
         └── worker 3 ── 读 batch 3
                              ↳
                       pin_memory=True → 直接拷到 CUDA pinned memory
                              ↳
                       non_blocking=True → CPU 拷 GPU 不阻塞
```

### 3.2 五个开关

| 参数 | 推荐值 | 作用 |
|---|---|---|
| `num_workers` | `min(8, os.cpu_count())` | 数据加载的并行进程数(>0 才多进程) |
| `pin_memory` | `True`(有 CUDA 时) | 把张量放到 CUDA pinned(锁页)内存,GPU 拷贝更快 |
| `persistent_workers` | `True`(num_workers>0) | 跨 epoch 保留 worker(避免每个 epoch 重启) |
| `prefetch_factor` | `2 ~ 4` | 每个 worker 预取的 batch 数(默认 2) |
| `non_blocking` | `True` | `.to(device, non_blocking=True)`,CPU 拷 GPU 不阻塞 |

### 3.3 在本项目里看现状

**RT916_SpikeFusionNet** `core.py:528-543`:
```python
train_loader = DataLoader(
    train_dataset,
    batch_size=CONFIG["BATCH_SIZE"],
    shuffle=True,
    num_workers=max(0, int(CONFIG.get("NUM_WORKERS", 0))),  # ← 默认 0(单进程)
    pin_memory=use_cuda,                                       # ← 已经有了
    persistent_workers=bool(CONFIG.get("NUM_WORKERS", 0)),     # ← 依赖 num_workers
)
```

⚠️ **`NUM_WORKERS=0` 是当前默认**,这是最大瓶颈。Windows 多进程 + numpy/pandas 还有 GBK 路径,可能也要小心(下文方案给的是不动代码的旁路)。

**TimeMixer** `repro_pipeline.py:1298-1299`:
```python
train_loader = DataLoader(train_ds, batch_size=cfg.batch_size, shuffle=train_shuffle)  # ← 全默认:0 workers, 无 pin_memory
valid_loader = DataLoader(valid_ds, batch_size=cfg.batch_size, shuffle=False)
```

⚠️ **完全没有优化**,这是第二大瓶颈。

### 3.4 风险

- **Windows + num_workers > 0**:必须有 `if __name__ == "__main__":` 保护(本项目已经有),否则会**无限递归开进程**。RT916 的 `run.py:11` 已经有保护;TimeMixer 的 `__main__` 也有。
- **GBK 中文路径 + joblib** 偶尔会因 worker 启动慢反而变慢,需要实测
- **内存**:`num_workers * prefetch_factor * batch 张量大小` 会在内存里缓存,要估算

---

## 4. 三者收益估算(本项目,RTX 4060 Laptop 8GB)

| 技巧 | 单开加速 | 三者叠加 | 显存影响 |
|---|---|---|---|
| TF32 | 1.3x ~ 1.5x | 基线 | 不变 |
| AMP(BF16) | 1.5x ~ 2.0x | 2.0x ~ 2.5x | -30% ~ -50% |
| DataLoader 优化 | 1.2x ~ 1.8x(取决于 NUM_WORKERS) | 2.0x ~ 3.0x | 不变 |
| **三开** | — | **保守 2.5x ~ 3.0x,乐观 3x ~ 4x** | -30% ~ -50% |

注意:DataLoader 优化的收益在 **GPU 越强、CPU 越弱、数据越复杂**时越大。RT916 / TimeMixer 的数据是 Excel/CSV → pandas,处理慢,所以 DataLoader 优化是**本项目最值得做的一项**。

---

## 5. 实测建议(本项目)

1. **先开 TF32**:零成本,几乎 100% 安全
2. **再开 AMP(BF16)**:用 autocast 包训练循环,无需 GradScaler
3. **最后调 DataLoader**:`num_workers=4` / `pin_memory=True` / `persistent_workers=True` / `prefetch_factor=2`
4. **每开一项**记录 wall-clock 和 SMAPE 差异,确认精度不掉
5. **如果精度掉了 0.1 SMAPE**:回退那一项,精度优先
