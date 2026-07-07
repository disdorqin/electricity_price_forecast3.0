"""
correction_guard.py — 限制过度修正（cap / 价格范围 / 正常时段护栏）。

ctype:
  'negative' : 推向 -80 地板，仅用绝对 cap + 价格下界（比例 cap 不适用于地板）
  'spike'    : 有界上行 lift，用 绝对cap + 比例cap + 价格上界
  'residual' : 偏差校准，用 绝对cap + 比例cap + 价格范围
"""
from __future__ import annotations

import numpy as np


def guard_pass(amount: float, original: float, corrected: float, cfg, ctype: str):
    cap_hit = False
    # 绝对 cap
    if abs(amount) > cfg.CAP_ABS:
        cap_hit = True
        return False, True, f"abs_cap|{amount:.1f}|>{cfg.CAP_ABS}"
    # 价格生理范围
    if corrected < cfg.PRICE_FLOOR or corrected > cfg.PRICE_CEIL:
        return False, False, f"price_range[{cfg.PRICE_FLOOR},{cfg.PRICE_CEIL}] violated"
    # 比例 cap（仅 spike / residual）
    if ctype in ("spike", "residual"):
        denom = abs(original) + 50.0
        if abs(amount) > cfg.CAP_RATIO * denom:
            cap_hit = True
            return False, True, f"ratio_cap|{amount:.1f}|>{cfg.CAP_RATIO*denom:.1f}"
    return True, cap_hit, ""
