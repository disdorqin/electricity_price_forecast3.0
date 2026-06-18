from __future__ import annotations

import logging
from pathlib import Path

from fusion.classifier_bridge import run_classifier_pipeline

logger = logging.getLogger(__name__)


def _resolve_realtime_work_dir(fusion_work_dir: Path, target: str) -> Path:
    if target == "realtime":
        return fusion_work_dir
    if target == "both":
        return fusion_work_dir / "realtime_run"
    return fusion_work_dir


def run_classifier_postprocess(*, fusion_work_dir, project_root, target, start_date, end_date, clf_data_path=None):
    base_dir = _resolve_realtime_work_dir(Path(fusion_work_dir), target)
    project_root = Path(project_root)
    clf_data = Path(clf_data_path) if clf_data_path else project_root / "data" / "shandong_pmos_hourly.xlsx"
    logger.info("Starting ExtremPriceClf postprocess: target=%s start=%s end=%s", target, start_date, end_date)
    return run_classifier_pipeline(
        fusion_work_dir=base_dir,
        project_root=project_root,
        start_date=start_date,
        end_date=end_date,
        clf_data_path=clf_data,
    )
