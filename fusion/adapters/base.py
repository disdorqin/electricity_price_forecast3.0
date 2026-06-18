from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd


class BasePredictionAdapter(ABC):
    """Convert model-specific outputs into the fusion long-table contract."""

    def __init__(self, source: str | Path):
        self.source = Path(source)

    @abstractmethod
    def load(self) -> pd.DataFrame:
        raise NotImplementedError
