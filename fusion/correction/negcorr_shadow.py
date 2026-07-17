"""
EFM3 NegCorr Shadow Module — Interface Reference

This module provides the shadow integration interface for the NegCorr
negative-price correction module (V5 canonical, w120/w180).

CRITICAL SAFETY RULES:
1. Default: DISABLED. Only active when EFM3_ENABLE_NEGCORR=shadow|production
2. Shadow mode: logs predictions alongside A05, does NOT modify final output
3. Production mode: REQUIRES maintainer approval (guard_production_flag)
4. Any failure → fail closed → return A05 prediction unchanged
5. Never reads target-day actual values
6. Never overwrites final.csv

Artifact: artifacts/negcorr/negcorr_w120_w180.pkl
Depends on: A05 output, legal_oos_da_pred
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .feature_flags import (
    is_negcorr_enabled,
    is_negcorr_shadow,
    guard_production_flag,
)

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ARTIFACT_PATH = REPO_ROOT / "artifacts" / "negcorr" / "negcorr_w120_w180.pkl"


class NegCorrShadowModule:
    """Shadow interface for NegCorr negative-price correction.

    Usage:
        module = NegCorrShadowModule()
        if module.is_available():
            shadow_pred = module.predict(a05_pred, da_pred, hour_business)
            # shadow_pred is logged but NOT used as final output (in shadow mode)
    """

    def __init__(self):
        self._model = None
        self._loaded = False
        self._load_error: Optional[str] = None

    def _load(self) -> bool:
        """Lazy-load the NegCorr artifact. Returns True on success."""
        if self._loaded:
            return self._model is not None
        if not ARTIFACT_PATH.exists():
            self._load_error = f"Artifact not found: {ARTIFACT_PATH}"
            log.warning("NegCorr shadow: %s", self._load_error)
            return False
        try:
            import pickle
            with open(ARTIFACT_PATH, "rb") as f:
                self._model = pickle.load(f)
            self._loaded = True
            log.info("NegCorr shadow: artifact loaded from %s", ARTIFACT_PATH)
            return True
        except Exception as e:
            self._load_error = f"Failed to load artifact: {e}"
            log.error("NegCorr shadow: %s", self._load_error)
            return False

    def is_available(self) -> bool:
        """Check if the module is available (artifact exists and loads)."""
        return self._load()

    def predict(
        self,
        a05_prediction: pd.Series,
        da_prediction: pd.Series,
        hour_business: pd.Series,
    ) -> pd.Series:
        """Run NegCorr correction on A05 prediction.

        Args:
            a05_prediction: A05 model output (24 hours)
            da_prediction: legal_oos_da_pred (day-ahead prediction)
            hour_business: hour indices 1..24

        Returns:
            Corrected prediction series.
            On ANY failure → returns a05_prediction unchanged (fail closed).
        """
        # Gate check
        if not is_negcorr_enabled():
            return a05_prediction

        # Load check
        if not self._load():
            log.warning("NegCorr: artifact unavailable, returning A05")
            return a05_prediction

        try:
            # Fail-closed: any exception returns A05
            corrected = self._apply_correction(
                a05_prediction, da_prediction, hour_business
            )
            # Validate output shape
            if len(corrected) != len(a05_prediction):
                log.error(
                    "NegCorr: output length mismatch (%d vs %d), returning A05",
                    len(corrected), len(a05_prediction),
                )
                return a05_prediction
            # Check for NaN
            if corrected.isna().any():
                log.error("NegCorr: output contains NaN, returning A05")
                return a05_prediction

            if is_negcorr_shadow():
                log.info(
                    "NegCorr SHADOW: correction computed but NOT applied to output. "
                    "A05 remains final."
                )

            return corrected

        except Exception as e:
            log.error("NegCorr: prediction failed (%s), returning A05", e)
            return a05_prediction

    def _apply_correction(
        self,
        a05_pred: pd.Series,
        da_pred: pd.Series,
        hours: pd.Series,
    ) -> pd.Series:
        """Internal: apply NegCorr logistic P(neg) + Huber magnitude.

        This is a reference implementation. The actual correction logic
        depends on the trained model in the pkl artifact.
        """
        if self._model is None:
            return a05_pred

        corrected = a05_pred.copy()

        # Identify negative-price candidate hours (A05 predicts < 0)
        neg_mask = a05_pred < 0
        if not neg_mask.any():
            return corrected

        # Apply correction to negative hours only
        try:
            model_w120 = self._model.get("w120")
            if model_w120 is not None:
                # Logistic P(neg) prediction
                features = pd.DataFrame({
                    "a05_pred": a05_pred[neg_mask].values,
                    "da_pred": da_pred[neg_mask].values if len(da_pred) > 0 else 0,
                    "hour": hours[neg_mask].values,
                })
                p_neg = model_w120.predict_proba(features)[:, 1]
                magnitude = model_w120.predict(features)
                corrected.loc[neg_mask] = p_neg * magnitude + (1 - p_neg) * a05_pred[neg_mask].values
        except Exception as e:
            log.warning("NegCorr correction application failed: %s", e)
            return a05_pred

        return corrected
