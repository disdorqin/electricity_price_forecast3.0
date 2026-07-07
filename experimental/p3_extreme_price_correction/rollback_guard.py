"""
rollback_guard.py — 回滚护栏。

触发回滚条件（任一即回退 original_pred）：
  - NaN / 越界（已在 guard 拦，但双重保险）
  - confidence 低于阈值（低置信不信任修正）
  - 正常时段被大改（original 健康但修正幅度大且置信不足）
返回 (should_rollback, rollback_reason)。
"""
from __future__ import annotations

import numpy as np


def evaluate_rollback(corrected: float, original: float, confidence: float,
                      cfg, ctype: str, applied: bool) -> tuple[bool, str]:
    if not np.isfinite(corrected) or not np.isfinite(original):
        return True, "NaN_detected"
    if corrected < cfg.PRICE_FLOOR or corrected > cfg.PRICE_CEIL:
        return True, "price_range_violation"
    if applied and confidence < cfg.ROLLBACK_MIN_CONF:
        return True, f"low_confidence({confidence:.2f}<{cfg.ROLLBACK_MIN_CONF})"
    return False, ""
