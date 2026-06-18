from __future__ import annotations

from dataclasses import dataclass

from .adapters.base import BasePredictionAdapter
from .adapters.csv_long_table import CsvLongTableAdapter
from .adapters.lightgbm import LightGBMAdapter
from .adapters.rt916 import RT916Adapter
from .adapters.sgdfnet import SGDFNetAdapter
from .adapters.timemixer import TimeMixerAdapter
from .adapters.timesfm import TimesFMAdapter


@dataclass(frozen=True)
class AdapterSpec:
    name: str
    adapter_cls: type[BasePredictionAdapter]
    description: str


ADAPTER_REGISTRY: dict[str, AdapterSpec] = {
    "csv_long_table": AdapterSpec(
        name="csv_long_table",
        adapter_cls=CsvLongTableAdapter,
        description="Reads a CSV that already follows the fusion long-table contract.",
    ),
    "timemixer": AdapterSpec(
        name="timemixer",
        adapter_cls=TimeMixerAdapter,
        description="Converts TimeMixer exported prediction CSVs into the fusion long table.",
    ),
    "lightgbm": AdapterSpec(
        name="lightgbm",
        adapter_cls=LightGBMAdapter,
        description="Converts LightGBM prediction outputs into the fusion long table.",
    ),
    "timesfm": AdapterSpec(
        name="timesfm",
        adapter_cls=TimesFMAdapter,
        description="Converts TimesFM backtest/forecast CSVs into the fusion long table.",
    ),
    "sgdfnet": AdapterSpec(
        name="sgdfnet",
        adapter_cls=SGDFNetAdapter,
        description="Converts SGDFNet realtime predictions.csv into the fusion long table.",
    ),
    "rt916": AdapterSpec(
        name="rt916",
        adapter_cls=RT916Adapter,
        description="Converts RT916 day-ahead or realtime CSV outputs into the fusion long table.",
    ),
}


def get_adapter(name: str) -> type[BasePredictionAdapter]:
    try:
        return ADAPTER_REGISTRY[name].adapter_cls
    except KeyError as exc:
        raise KeyError(f"Unknown adapter: {name}. Available: {sorted(ADAPTER_REGISTRY)}") from exc
