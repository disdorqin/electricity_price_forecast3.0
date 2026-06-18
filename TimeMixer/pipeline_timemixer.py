"""Compatibility shim for legacy TimeMixer entry/import paths.

This module must support two historical usages at the same time:
1. direct script execution: ``python TimeMixer/pipeline_timemixer.py``
2. same-directory imports from ``enhanced_pipeline.py``

We keep the old symbol surface by re-exporting from ``repro_pipeline`` while
also handling package and script execution contexts safely.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import torch.nn as nn

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from TimeMixer.repro_pipeline import (  # type: ignore
        ElectricityDailyDataset,
        evaluate_metrics as _EVALUATE_METRICS_IMPL,
        main,
    )
    from TimeMixer.backbones import PastDecomposableMixing, build_backbone  # type: ignore
except ModuleNotFoundError:
    from repro_pipeline import (  # type: ignore
        ElectricityDailyDataset,
        evaluate_metrics as _EVALUATE_METRICS_IMPL,
        main,
    )
    from backbones import PastDecomposableMixing, build_backbone  # type: ignore


class TimeMixer(nn.Module):
    """Legacy constructor-compatible wrapper around the current backbone."""

    def __init__(
        self,
        *,
        past_dim: int,
        future_dim: int,
        seq_len: int,
        pred_len: int,
        hidden_dim: int = 64,
        n_blocks: int = 2,
        scales: int = 3,
        dropout: float = 0.1,
        **_: object,
    ) -> None:
        super().__init__()
        self.seq_len = seq_len
        self.pred_len = pred_len
        self.model = build_backbone(
            "timemixer",
            past_dim=past_dim,
            future_dim=future_dim,
            pred_len=pred_len,
            hidden_dim=hidden_dim,
            blocks=n_blocks,
            scales=scales,
            dropout=dropout,
            segment_head_mode="none",
        )

    def forward(self, past_x, future_x):
        return self.model(past_x, future_x)


def evaluate_metrics(pred_df: pd.DataFrame, task: str) -> pd.DataFrame:
    """Bridge legacy metric contract with the newer business-column exports."""
    normalized = pred_df.copy()
    if "y_pred" not in normalized.columns or "y_true" not in normalized.columns:
        if task == "da":
            normalized["y_true"] = normalized["day_ahead_clearing_price"]
            normalized["y_pred"] = normalized["pred_day_ahead_price"]
        else:
            normalized["y_true"] = normalized["realtime_price"]
            normalized["y_pred"] = normalized["pred_realtime_price"]
    return _EVALUATE_METRICS_IMPL(normalized, task)


__all__ = [
    "ElectricityDailyDataset",
    "PastDecomposableMixing",
    "TimeMixer",
    "evaluate_metrics",
    "main",
]


if __name__ == "__main__":
    main()
