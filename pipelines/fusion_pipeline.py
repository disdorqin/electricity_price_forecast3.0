from __future__ import annotations

import logging
from pathlib import Path

from services.fusion_service import run_formal_fusion

logger = logging.getLogger(__name__)


def run_fusion_pipeline(args):
    if args.models and args.models.strip().lower() not in {"all", ""}:
        requested = {item.strip().lower() for item in args.models.split(",") if item.strip()}
        allowed = {"lightgbm", "timesfm", "timemixer", "sgdfnet", "rt916"}
        unknown = requested - allowed
        if unknown:
            raise ValueError(f"Unknown fusion models requested: {sorted(unknown)}")
        if args.target in {"dayahead", "both"} and "timesfm" not in requested:
            raise ValueError("Current formal fusion path for dayahead requires TimesFM in the official model pool.")
        if args.target in {"realtime", "both"} and "timesfm" not in requested:
            raise ValueError("Current formal fusion path for realtime requires TimesFM in the official model pool.")
    if not args.date and not (args.start and args.end):
        raise ValueError("fusion pipeline requires --date or --start/--end")
    start = args.start or args.date
    end = args.end or args.date
    work_dir = run_formal_fusion(
        target=args.target,
        start_date=start,
        end_date=end,
        work_dir=args.fusion_work_dir,
        train_length_decision=args.train_length_decision,
        weight_lower_bound=args.weight_lower_bound,
        weight_upper_bound=args.weight_upper_bound,
        conda_env=args.conda_env,
    )
    if getattr(args, "use_classifier", False):
        if args.target == "dayahead":
            logger.warning("ExtremPriceClf only applies to realtime. Ignoring --use-classifier for dayahead.")
        else:
            from pipelines.classifier_pipeline import run_classifier_postprocess

            clf_result = run_classifier_postprocess(
                fusion_work_dir=work_dir,
                project_root=Path(__file__).resolve().parents[1],
                target=args.target,
                start_date=start,
                end_date=end,
                clf_data_path=getattr(args, "clf_data", None),
            )
            logger.info("Classifier postprocess result: %s", clf_result)
    return work_dir
