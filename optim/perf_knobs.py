"""optim/perf_knobs.py — 无侵入训练加速底座

依据 docs/项目提高速度.md 的工业交付方案:
  - TF32(默认开)
  - AMP BF16(默认开)
  - DataLoader 优化(num_workers/pin_memory/persistent_workers/prefetch_factor)
  - non_blocking 数据传输
  - cudnn benchmark

所有开关通过环境变量可关闭,回退到原始行为。
"""
from __future__ import annotations

import os
import contextlib
from typing import Iterator

import torch
from torch.utils.data import DataLoader


# ============================================================
# 环境变量读取
# ============================================================
def _env_bool(key: str, default: str = "1") -> bool:
    return bool(int(os.getenv(key, default)))


def _env_int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


# ============================================================
# 1) TF32 + cudnn benchmark — 进程级开关
# ============================================================
def enable_tf32() -> None:
    if not torch.cuda.is_available():
        return
    if _env_bool("OPTIM_TF32", "1"):
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
    if _env_bool("OPTIM_CUDNN_BENCHMARK", "1"):
        torch.backends.cudnn.benchmark = True


# ============================================================
# 2) AMP — autocast + GradScaler
# ============================================================
def amp_enabled() -> bool:
    return _env_bool("OPTIM_AMP", "1") and torch.cuda.is_available()


def amp_dtype() -> torch.dtype:
    raw = os.getenv("OPTIM_AMP_DTYPE", "bf16").strip().lower()
    if raw == "fp16":
        return torch.float16
    return torch.bfloat16  # RTX 4060 推荐 BF16


@contextlib.contextmanager
def amp_autocast() -> Iterator[None]:
    if amp_enabled():
        with torch.autocast(device_type="cuda", dtype=amp_dtype()):
            yield
    else:
        yield


def make_grad_scaler():
    """仅 FP16 需要;BF16 返回 None。"""
    if not amp_enabled() or amp_dtype() == torch.bfloat16:
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
    use_cuda = torch.cuda.is_available()
    cpu_cnt = os.cpu_count() or 4

    num_workers = _env_int("OPTIM_NUM_WORKERS", 4)
    num_workers = min(num_workers, max(1, cpu_cnt - 1))

    pin_memory = _env_bool("OPTIM_PIN_MEMORY", "1") and use_cuda
    persistent = num_workers > 0
    prefetch = _env_int("OPTIM_PREFETCH", 2)

    kwargs = dict(
        batch_size=batch_size,
        shuffle=shuffle,
        drop_last=drop_last,
        num_workers=num_workers,
        pin_memory=pin_memory,
        persistent_workers=persistent,
    )
    if num_workers > 0:
        kwargs["prefetch_factor"] = prefetch
    return DataLoader(dataset, **kwargs)


# ============================================================
# 4) non_blocking 数据传输
# ============================================================
def to_device(batch, device, *, non_blocking: bool | None = None):
    if non_blocking is None:
        non_blocking = _env_bool("OPTIM_NON_BLOCKING", "1")
    if isinstance(batch, torch.Tensor):
        return batch.to(device, non_blocking=non_blocking)
    if isinstance(batch, (tuple, list)):
        return type(batch)(to_device(b, device, non_blocking=non_blocking) for b in batch)
    if isinstance(batch, dict):
        return {k: to_device(v, device, non_blocking=non_blocking) for k, v in batch.items()}
    return batch
