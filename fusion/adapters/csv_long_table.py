from __future__ import annotations

import pandas as pd

from ..contracts import standardize_prediction_table
from .base import BasePredictionAdapter


class CsvLongTableAdapter(BasePredictionAdapter):
    """Use this when a model already exports the standard long-table contract."""

    def load(self) -> pd.DataFrame:
        df = pd.read_csv(self.source)
        return standardize_prediction_table(df)
