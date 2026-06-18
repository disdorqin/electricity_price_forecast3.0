from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass
class PredictionResult:
    model_name: str
    target: str
    output_path: Path
    frame: pd.DataFrame


class BaseModelPipeline(ABC):
    model_name: str
    device_type: str = "cpu"

    @abstractmethod
    def train(self, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def predict(self, **kwargs) -> PredictionResult | None:
        raise NotImplementedError

    @abstractmethod
    def predict_range(self, **kwargs) -> PredictionResult | None:
        raise NotImplementedError
