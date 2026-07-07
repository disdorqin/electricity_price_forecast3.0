#!/usr/bin/env python3
"""
EFM3 Fusion Chain v1 — First Big Run Orchestrator

Usage:
    python scripts/run_fusion_shadow_v1.py [--config <path>] [--months ...] [--variants ...]

Default behavior uses:
    - Config: configs/fusion_shadow_v1.yaml
    - Test months: 2025-03..06, 2025-09..10, 2025-11..12, 2026-01..06
    - All 10 variants including oracle_upper_bound
"""

from __future__ import annotations

import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipelines.fusion_shadow_v1 import main

if __name__ == "__main__":
    sys.exit(main())
