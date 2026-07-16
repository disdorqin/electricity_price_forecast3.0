"""
EFM3 Feature Flags — Research Candidate Gate

All research candidates are gated behind feature flags.
Default: ALL OFF. Production A05 is unaffected.

To enable NegCorr shadow (research only):
  export EFM3_ENABLE_NEGCORR=shadow

To enable NegCorr production (REQUIRES maintainer approval):
  export EFM3_ENABLE_NEGCORR=production

NEVER set EFM3_ENABLE_NEGCORR=production without explicit human sign-off.
"""
from __future__ import annotations

import os
import logging

log = logging.getLogger(__name__)

# ── Feature flag definitions ──────────────────────────────────────
FLAGS = {
    "EFM3_ENABLE_NEGCORR": {
        "default": "off",
        "allowed_values": ["off", "shadow", "production"],
        "description": "Gate NegCorr correction module (w120/w180). "
                       "shadow = log only, production = apply to A05 output.",
        "requires_approval": True,  # production mode requires human approval
    },
}


def get_flag(name: str) -> str:
    """Return the current value of a feature flag."""
    if name not in FLAGS:
        raise ValueError(f"Unknown feature flag: {name}. Known: {sorted(FLAGS)}")
    spec = FLAGS[name]
    raw = os.environ.get(name, spec["default"]).strip().lower()
    if raw not in spec["allowed_values"]:
        log.warning(
            "Feature flag %s has invalid value %r (allowed: %s). Falling back to %r.",
            name, raw, spec["allowed_values"], spec["default"],
        )
        return spec["default"]
    return raw


def is_negcorr_enabled() -> bool:
    """Check if NegCorr is enabled in any mode (shadow or production)."""
    val = get_flag("EFM3_ENABLE_NEGCORR")
    return val in ("shadow", "production")


def is_negcorr_shadow() -> bool:
    """Check if NegCorr is in shadow mode (log only, no output change)."""
    return get_flag("EFM3_ENABLE_NEGCORR") == "shadow"


def is_negcorr_production() -> bool:
    """Check if NegCorr is in production mode (applied to output).
    WARNING: This requires explicit maintainer approval."""
    return get_flag("EFM3_ENABLE_NEGCORR") == "production"


def guard_production_flag(name: str) -> None:
    """Raise if a flag is set to production mode without approval gate."""
    spec = FLAGS.get(name, {})
    if get_flag(name) == "production" and spec.get("requires_approval"):
        raise RuntimeError(
            f"Feature flag {name} is set to 'production' but requires "
            f"explicit maintainer approval. Set to 'shadow' for monitoring, "
            f"or contact maintainer for approval."
        )
