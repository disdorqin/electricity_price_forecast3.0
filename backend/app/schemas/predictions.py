"""Prediction-related API schemas."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class PredictionRow(BaseModel):
    run_id: str
    target_date: Optional[str] = None
    hour_business: int
    task: Optional[str] = None
    stage: Optional[str] = None
    model_name: Optional[str] = None
    model_version: Optional[str] = None
    pred_price: Optional[float] = None
    is_shadow: bool = False
    is_selected: bool = False
    selected_reason: Optional[str] = None


class HourlyPrediction(PredictionRow):
    pass


class PredictionCompareItem(BaseModel):
    run_id: str
    target_date: Optional[str] = None
    hour_business: int
    model_name: str
    stage: Optional[str] = None
    pred_price: Optional[float] = None
    is_selected: bool = False
    is_shadow: bool = False


class SelectedPrediction(BaseModel):
    run_id: str
    target_date: Optional[str] = None
    hour_business: int
    stage: Optional[str] = None
    model_name: Optional[str] = None
    pred_price: Optional[float] = None
    selected_reason: Optional[str] = None
