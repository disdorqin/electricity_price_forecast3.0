from __future__ import annotations

from pathlib import Path
import sys


SRC_ROOT = Path(__file__).resolve().parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from sgdfnet.data_contract import FeatureConfig, load_dataset, preprocess_dataframe  # noqa: E402

__all__ = ["FeatureConfig", "load_dataset", "preprocess_dataframe"]
