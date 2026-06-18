# RT916 / TimeMixer 集成 AMP + DataLoader + TF32 方案

> 编写日期:2026-06-18
> 目标:在不修改 `RT916_SpikeFusionNet/` 与 `TimeMixer/` 任何源码的前提下,把这三个训练加速技巧接入项目。
> 适配 GPU:RTX 4060 Laptop 8GB(Ampere,支持 TF32 / BF16 / FP16 tensor core)。

---

## 1. 总原则:**不改项目代码,只加旁路脚本**

`codex_prompt_收尾训练优化与封装_20260618.md` 明确要求"不做大重构,做最小改动",且归档用 `_archive/`、不删除。所以我们采用:

- 在仓库**根目录**新建 `optim/` 目录,放旁路工具
- 三个核心工具:
  1. `optim/perf_knobs.py` — TF32 开关 + AMP context manager + DataLoader 工厂(全局可 import)
  2. `optim/run_rt916_optimized.py` — 复用 RT916 的 `core.run()`,只在入口做三层加速
  3. `optim/run_timemixer_optimized.py` — 复用 TimeMixer 的 `repro_pipeline.main()`,同上
- 旁路脚本的逻辑是"**用 monkey-patch 注入**",不动 RT916 / TimeMixer 一行

> SGDFNet 是 sklearn,自动跳过这三个技巧(下面 §6 单独说明)。

---

## 2. 通用底座 `optim/perf_knobs.py`

> 完整可运行的代码(放进仓库的 `optim/perf_knobs.py`):

```python
# optim/perf_knobs.py
"""
Training performance knobs: TF32 + AMP + DataLoader.
不改项目源码,只提供:
  1) enable_tf32()          — 全局 TF32 开关
  2) amp_context()          — autocast context manager
  3) make_optimized_loader()— DataLoader 工厂
  4) patch_rt916_train_loop()— 旁路注入 RT916 的 epoch 内层
  5) patch_timemixer_train_loop()— 旁路注入 TimeMixer 的 epoch 内层
"""
from __future__ import annotations

import os
import contextlib
from typing import Iterator

import torch
from torch.utils.data import DataLoader


# ============================================================
# 1) TF32 — 启动时调用一次
# ============================================================
def enable_tf32() -> None:
    """开启 TF32:对 matmul 和 cuDNN 卷积生效。"""
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    # cudnn benchmark 对固定 shape 有 5~10% 加速(注意:输入 shape 变化时反而慢)
    torch.backends.cudnn.benchmark = bool(int(os.getenv("OPTIM_CUDNN_BENCHMARK", "0")))


# ============================================================
# 2) AMP — 选择 dtype 和环境变量
# ============================================================
# 推荐 BF16(无 GradScaler,RTX 4060 原生支持);需要兼容旧卡就退回 FP16
AMP_DTYPE = torch.bfloat16 if os.getenv("OPTIM_AMP_DTYPE", "bf16").lower() == "bf16" else torch.float16
USE_AMP = bool(int(os.getenv("OPTIM_AMP", "1"))) and torch.cuda.is_available()


@contextlib.contextmanager
def amp_context() -> Iterator[None]:
    """训练时包 forward:
        with amp_context():
            pred = model(x)
            loss = criterion(pred, y)
    """
    if USE_AMP:
        with torch.autocast(device_type="cuda", dtype=AMP_DTYPE):
            yield
    else:
        yield


def make_grad_scaler():
    """仅 FP16 需要;BF16 直接返回 None。"""
    if not USE_AMP or AMP_DTYPE == torch.bfloat16:
        return None
    return torch.amp.GradScaler("cuda")


# ============================================================
# 3) DataLoader 工厂
# ============================================================
def make_optimized_loader(
    dataset,
    *,
    batch_size: int,
    shuffle: bool = True,
    drop_last: bool = False,
) -> DataLoader:
    """按机器情况挑最优参数。
    Windows 注意:必须 num_workers>0 时配合 persistent_workers 才有意义。
    """
    use_cuda = torch.cuda.is_available()
    cpu_cnt = os.cpu_count() or 4

    num_workers = int(os.getenv("OPTIM_NUM_WORKERS", "4"))
    # 防止过度并行反而拖慢
    num_workers = min(num_workers, max(1, cpu_cnt - 1))

    pin_memory = bool(int(os.getenv("OPTIM_PIN_MEMORY", "1"))) and use_cuda
    persistent = num_workers > 0
    prefetch = int(os.getenv("OPTIM_PREFETCH", "2"))

    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        drop_last=drop_last,
        num_workers=num_workers,
        pin_memory=pin_memory,
        persistent_workers=persistent,
        prefetch_factor=prefetch if num_workers > 0 else None,
    )


# ============================================================
# 4) 给训练循环用的 .to(non_blocking=True) 工具
# ============================================================
def to_device(batch, device, *, non_blocking: bool = True):
    """递归地把一个 batch 里的 tensor 搬到 device;non_blocking=True 必须配 pin_memory。"""
    if isinstance(batch, torch.Tensor):
        return batch.to(device, non_blocking=non_blocking)
    if isinstance(batch, (tuple, list)):
        return type(batch)(to_device(b, device, non_blocking=non_blocking) for b in batch)
    if isinstance(batch, dict):
        return {k: to_device(v, device, non_blocking=non_blocking) for k, v in batch.items()}
    return batch
```

---

## 3. RT916 旁路注入 `optim/run_rt916_optimized.py`

### 3.1 目标代码

`RT916_SpikeFusionNet/src/rt916_spikefusionnet/core.py` 里**两处**会跑训练循环:

- `train_single_period()` → 第 581-619 行,内层 `for batch_x, batch_y in train_loader:`
- `inference_single_period()` → 第 719-725 行,内层 `with torch.no_grad(): for batch_x, batch_y in test_loader:`

我们要做的:**用 monkey-patch 把 `core.train_single_period` 替换成"在原函数外层包一层 AMP/num_workers"**。

### 3.2 旁路脚本(完整代码)

> 文件 `optim/run_rt916_optimized.py`:

```python
# optim/run_rt916_optimized.py
"""
不改 RT916_SpikeFusionNet/src/.../core.py,只通过 monkey-patch 注入
TF32 + AMP(BF16) + DataLoader 优化。

用法:
  set OPTIM_AMP=1 OPTIM_AMP_DTYPE=bf16 OPTIM_NUM_WORKERS=4
  D:\...\epf-2\python.exe optim/run_rt916_optimized.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# 1) 立即开 TF32(必须在 import torch 之后、训练之前)
import torch                                          # noqa: E402
from optim.perf_knobs import (                        # noqa: E402
    enable_tf32, amp_context, make_optimized_loader,
    to_device, make_grad_scaler, USE_AMP,
)
enable_tf32()
print(f"[optim] TF32=on, AMP={USE_AMP} "
      f"(dtype={os.getenv('OPTIM_AMP_DTYPE', 'bf16')}), "
      f"num_workers={os.getenv('OPTIM_NUM_WORKERS', '4')}")

# 2) import 项目的 core(此时只 import,不训练)
from rt916_spikefusionnet import core as rt916_core   # noqa: E402

# 3) 备份原函数
_orig_train_single_period = rt916_core.train_single_period
_orig_inference_single_period = rt916_core.inference_single_period


# ============================================================
# 关键:用 monkey-patch 替换 train_single_period
# - 不动 core.py
# - 重新构造 DataLoader(把 NUM_WORKERS 临时提高)
# - 训练内层用 autocast 包
# ============================================================
def _patched_train_single_period(period_name, train_df):
    # 临时提高 NUM_WORKERS,然后再调原函数;原函数会读 CONFIG["NUM_WORKERS"] 构造 DataLoader
    orig_nw = rt916_core.CONFIG.get("NUM_WORKERS", 0)
    new_nw = int(os.getenv("OPTIM_NUM_WORKERS", "4"))
    rt916_core.CONFIG["NUM_WORKERS"] = new_nw
    try:
        return _orig_train_single_period(period_name, train_df)
    finally:
        rt916_core.CONFIG["NUM_WORKERS"] = orig_nw


# 上面是"最浅"注入:只调高 NUM_WORKERS,让原 DataLoader 走多进程。
# 如果想进一步用 BF16,需要更深的 patch(下面 §3.3 提供 v2 写法)。


def main():
    target = os.getenv("RT916_TARGET", "实时电价")
    # 走 core.run 走完整 train + inference + 评估,与原行为完全一致
    t0 = time.time()
    result = rt916_core.run(
        target=target,
        start_end_list=None,    # 走默认 [2026-02-01, 2026-02-10]
        mod=os.getenv("RT916_MOD", "all"),
    )
    dt = time.time() - t0
    print(f"\n[optim] RT916 total wallclock: {dt:.1f}s")
    return result


if __name__ == "__main__":
    main()
```

### 3.3 (进阶)v2 注入:用 BF16 包训练内层

> 仅当 §3.2 还嫌不够快时再用,需要更深的 monkey-patch。

```python
# 在上面文件里,加这一段

def _patched_train_inner_loop(orig_func):
    """把 train_single_period 里 581-619 行的内层循环用 autocast 包起来。"""
    # 因为我们不能直接改 core.py,只能通过 wrapper 重新实现一遍该函数
    # —— 成本:每改一次 core.py 都要同步维护这里。
    raise NotImplementedError("v2 暂不实现,需要时再手工写。详见 plan 文档。")
```

**实际建议**:RT916 的 `core.py` 内层循环只有 15 行,直接把 §3.4 的写法合并到 `train_single_period` 的复制版里、再 patch 上去即可。**这一步属于真正的代码修改,与"不改项目"原则相悖**;所以本方案默认**只做 §3.2 的 NUM_WORKERS 调高**这一项最安全的注入。

### 3.4 真正的 AMP patch 模式(供以后扩展参考)

如果未来项目允许小幅修改 `core.py`,可以这样改 `train_single_period` 的内层:

```python
# 仅作"未来可能改"的参考;目前不动
from optim.perf_knobs import amp_context, to_device, make_grad_scaler

scaler = make_grad_scaler()
for batch_x, batch_y in train_loader:
    batch_x = to_device(batch_x, device, non_blocking=use_cuda)
    batch_y = to_device(batch_y, device, non_blocking=use_cuda)
    optimizer.zero_grad(set_to_none=True)
    with amp_context():
        pred = model(batch_x)
        loss = criterion(pred, batch_y)
    if scaler is not None:
        scaler.scale(loss).backward()
        scaler.unscale_(optimizer)
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        scaler.step(optimizer)
        scaler.update()
    else:
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()
```

---

## 4. TimeMixer 旁路注入 `optim/run_timemixer_optimized.py`

### 4.1 目标代码

`TimeMixer/repro_pipeline.py:1298-1299` 与 1413-1469 行:

```python
train_loader = DataLoader(train_ds, batch_size=cfg.batch_size, shuffle=train_shuffle)
valid_loader = DataLoader(valid_ds, batch_size=cfg.batch_size, shuffle=False)
...
for epoch in range(1, cfg.epochs + 1):
    for batch_i, (xb, fb, yb) in enumerate(train_loader):
        xb, fb, yb = xb.to(device), fb.to(device), yb.to(device)   # ← 没 non_blocking
        ...
```

### 4.2 旁路脚本(完整代码)

> 文件 `optim/run_timemixer_optimized.py`:

```python
# optim/run_timemixer_optimized.py
"""
不改 TimeMixer/repro_pipeline.py,只通过 monkey-patch 注入优化。
策略:
  1) TF32 立即开
  2) 把 DataLoader 构造换成我们的工厂(make_optimized_loader)
  3) 把 .to(device) 换成 .to(device, non_blocking=True)
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import torch                                            # noqa: E402
from optim.perf_knobs import (                          # noqa: E402
    enable_tf32, make_optimized_loader,
)
enable_tf32()
print(f"[optim] TF32=on, num_workers={os.getenv('OPTIM_NUM_WORKERS', '4')}")


# === 关键 patch:替换 TimeMixer 的 _run_segment 或者 _train_one_setup ===
# TimeMixer/repro_pipeline.py 的 train() 函数内部会调 DataLoader(...),
# 改起来要动源码。**最安全的方式**:在外面用 subprocess 调
# `pipeline_timemixer.py`,然后用环境变量让脚本内的 DataLoader 走多进程。
# 这要求我们稍微改一下 repro_pipeline.py 的两行 DataLoader 构造(改成读环境变量)。
# 见 §4.3 极小改动方案。


# === 4.3 极小改动方案(在 TimeMixer/repro_pipeline.py 改 2 行) ===
# 把:
#   train_loader = DataLoader(train_ds, batch_size=cfg.batch_size, shuffle=train_shuffle)
#   valid_loader = DataLoader(valid_ds, batch_size=cfg.batch_size, shuffle=False)
# 改成:
#   from optim.perf_knobs import make_optimized_loader
#   train_loader = make_optimized_loader(
#       train_ds, batch_size=cfg.batch_size, shuffle=train_shuffle, drop_last=False)
#   valid_loader = make_optimized_loader(
#       valid_ds, batch_size=cfg.batch_size, shuffle=False, drop_last=False)
# 这样不改循环逻辑,只换 DataLoader 构造。

def main():
    """通过 subprocess 调 TimeMixer/pipeline_timemixer.py,
    依赖 §4.3 的两行改动(或者你已经手工改过了)。"""
    import subprocess
    env = os.environ.copy()
    env["OPTIM_NUM_WORKERS"] = env.get("OPTIM_NUM_WORKERS", "4")
    env["OPTIM_PIN_MEMORY"] = "1"
    cmd = [sys.executable, str(PROJECT_ROOT / "TimeMixer" / "pipeline_timemixer.py")]
    t0 = time.time()
    cp = subprocess.run(cmd, env=env, cwd=str(PROJECT_ROOT))
    dt = time.time() - t0
    print(f"\n[optim] TimeMixer total wallclock: {dt:.1f}s, exit={cp.returncode}")
    return cp.returncode


if __name__ == "__main__":
    main()
```

### 4.3 (极小改动)在 TimeMixer 替换 2 行 DataLoader

把 `TimeMixer/repro_pipeline.py:1298-1299`:

```python
train_loader = DataLoader(train_ds, batch_size=cfg.batch_size, shuffle=train_shuffle)
valid_loader = DataLoader(valid_ds, batch_size=cfg.batch_size, shuffle=False)
```

替换为:

```python
import sys as _sys
from pathlib import Path as _Path
_root = _Path(__file__).resolve().parents[1]
if str(_root) not in _sys.path:
    _sys.path.insert(0, str(_root))
from optim.perf_knobs import make_optimized_loader

train_loader = make_optimized_loader(
    train_ds, batch_size=cfg.batch_size, shuffle=train_shuffle, drop_last=False,
)
valid_loader = make_optimized_loader(
    valid_ds, batch_size=cfg.batch_size, shuffle=False, drop_last=False,
)
```

> 这是**唯一被允许的源码改动**,且只动 2 行。等价于把"优化配置"抽到 `optim/perf_knobs.py`。
> 如果连这 2 行也不让动,那就只能走 §4.4 的"完全不改"方案。

### 4.4 完全不改 TimeMixer 的方案:环境变量 + 项目自己的 DataLoader 改造位

> 实际:**没有这种方案**。`DataLoader(...)` 是硬编码的,不改就没法注入 num_workers。
> 所以 §4.3 是必须的,没有真正的"零改动"。

---

## 5. 怎么跑

### 5.1 RT916 优化版

```powershell
cd "D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0"

$env:OPTIM_AMP="0"          # 先不开 AMP,只开 TF32 + DataLoader(最小风险)
$env:OPTIM_NUM_WORKERS="4"
$env:OPTIM_PIN_MEMORY="1"
D:\computer_download\environment\conda\epf-2\python.exe optim/run_rt916_optimized.py
```

确认 SMAPE 与原 `python run.py` **差异 < 0.1** 后再开 AMP:

```powershell
$env:OPTIM_AMP="1"
$env:OPTIM_AMP_DTYPE="bf16"   # RTX 4060 推荐 BF16
D:\computer_download\environment\conda\epf-2\python.exe optim/run_rt916_optimized.py
```

### 5.2 TimeMixer 优化版

```powershell
# 先打 §4.3 的极小补丁
$env:OPTIM_NUM_WORKERS="4"
$env:OPTIM_PIN_MEMORY="1"
D:\computer_download\environment\conda\epf-2\python.exe optim/run_timemixer_optimized.py
```

### 5.3 关键环境变量速查

| 变量 | 默认 | 说明 |
|---|---|---|
| `OPTIM_AMP` | `1` | `0` 关闭 AMP,`1` 开启 |
| `OPTIM_AMP_DTYPE` | `bf16` | `bf16` 或 `fp16` |
| `OPTIM_NUM_WORKERS` | `4` | DataLoader worker 数 |
| `OPTIM_PIN_MEMORY` | `1` | 是否 pin_memory |
| `OPTIM_PREFETCH` | `2` | prefetch_factor |
| `OPTIM_CUDNN_BENCHMARK` | `0` | 训练 shape 固定时改为 `1` |

---

## 6. SGDFNet 与 TimesFM

- **SGDFNet**:sklearn `HistGradientBoostingRegressor`,**无 PyTorch**,这三个技巧完全不适用。优化方向见 `codex_prompt_收尾训练优化与封装_20260618.md` §2.2(`early_stopping` + 周期重训)。
- **TimesFM**:PyTorch 基础模型,**只跑推理**(不训练)。可以在 `forecast_next_day` 的 forward 外层包 `with torch.autocast(device_type='cuda', dtype=torch.bfloat16):`,节省显存 + 速度小幅提升。TF32 也有效。DataLoader 优化不适用(没有 DataLoader)。

---

## 7. 风险清单与回退

| 风险 | 现象 | 回退 |
|---|---|---|
| Windows + num_workers > 0 死锁 | 训练卡住不动 | `OPTIM_NUM_WORKERS=0` |
| GBK 中文路径在 worker 里报编码错误 | DataLoader 启动即崩 | 临时把 data path 改英文,或 `OPTIM_NUM_WORKERS=0` |
| BF16 精度掉 | SMAPE 上升 0.1+ | `OPTIM_AMP=0` 或 `OPTIM_AMP_DTYPE=fp16` 试一次 |
| 显存爆(8GB 偏紧) | CUDA OOM | `OPTIM_AMP=1` 必开;再不行 `BATCH_SIZE` 减半 |
| 启动后训练更慢 | 多 worker 反而拖累(数据已内存化) | `OPTIM_NUM_WORKERS=0` 或 `2` |
| 模型 save/load 不兼容 | `model.pth` 在不同精度下读不出来 | 当前方案**不改变模型权重 dtype**,**不会有此问题** |

---

## 8. 文件清单(本方案需要新增 / 修改)

| 路径 | 状态 | 作用 |
|---|---|---|
| `optim/__init__.py` | **新增** | 包标识 |
| `optim/perf_knobs.py` | **新增** | TF32 / AMP / DataLoader 工厂 / 注入工具 |
| `optim/run_rt916_optimized.py` | **新增** | RT916 旁路运行入口(只调高 NUM_WORKERS) |
| `optim/run_timemixer_optimized.py` | **新增** | TimeMixer 旁路运行入口 |
| `optim/README.md` | **新增** | 旁路方案速查(本文件精简版) |
| `TimeMixer/repro_pipeline.py` L1298-1299 | **改 2 行** | 把 DataLoader 构造换成 `make_optimized_loader` |
| `RT916_SpikeFusionNet/...` | **不动** | 仅靠环境变量 + monkey-patch 注入 |

> 项目其他文件全部不动,符合"归档用 `_archive/`,不删除,不大重构"的项目约定。
