from __future__ import annotations

from typing import Callable

from pipelines.base import BaseModelPipeline

from lightGBM.pipeline import ModelPipeline as LightGBMPipeline
from RT916_SpikeFusionNet.pipeline import ModelPipeline as RT916Pipeline
from SGDFNet.pipeline import ModelPipeline as SGDFNetPipeline
from TimeMixer.pipeline import ModelPipeline as TimeMixerPipeline
from TimesFM.pipeline import ModelPipeline as TimesFMPipeline


PIPELINE_REGISTRY: dict[str, Callable[[], BaseModelPipeline]] = {
    "lightgbm": LightGBMPipeline,
    "sgdfnet": SGDFNetPipeline,
    "rt916": RT916Pipeline,
    "timemixer": TimeMixerPipeline,
    "timesfm": TimesFMPipeline,
}


def get_registered_models() -> list[str]:
    return sorted(PIPELINE_REGISTRY.keys())


def get_model_pipeline(model_name: str) -> BaseModelPipeline:
    key = model_name.strip().lower()
    if key not in PIPELINE_REGISTRY:
        raise KeyError(f"Unsupported model: {model_name}")
    return PIPELINE_REGISTRY[key]()
