"""
EFM3 Correction Modules

All correction modules are gated behind feature flags and default to DISABLED.
Any failure → fail closed → return A05 prediction unchanged.
"""
from .feature_flags import (
    get_flag,
    is_negcorr_enabled,
    is_negcorr_shadow,
    is_negcorr_production,
    guard_production_flag,
)
