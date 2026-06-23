from __future__ import annotations

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from cli.parser import build_parser
from pipelines.evaluate_pipeline import run_evaluate_pipeline
from pipelines.fusion_pipeline import run_fusion_pipeline
from pipelines.predict_pipeline import run_predict_pipeline
from pipelines.sync_dataset_pipeline import run_sync_dataset_pipeline
from pipelines.staged_pipeline import (
    run_classifier_stage,
    run_fuse_stage,
    run_full_pipeline,
    run_learner_stage,
    run_model_stage,
)
from pipelines.train_pipeline import run_train_pipeline


def main() -> int:
    args = build_parser().parse_args()
    # Positional date shortcut: `python main.py 2026-02-01`
    if args.pos_date is not None and args.date is None:
        args.date = args.pos_date
    if args.pipeline == "predict":
        results = run_predict_pipeline(args)
        for result in results:
            if result is None:
                continue
            print(f"{result.model_name}:{result.target} -> {result.output_path}")
        return 0
    if args.pipeline == "train":
        results = run_train_pipeline(args)
        for result in results:
            print(f"{result.model_name}:{result.target} -> train done")
        return 0
    if args.pipeline == "evaluate":
        output_path = run_evaluate_pipeline(args)
        print(output_path)
        return 0
    if args.pipeline == "fusion":
        output_path = run_fusion_pipeline(args)
        print(output_path)
        return 0
    if args.pipeline == "sync_dataset":
        output_path = run_sync_dataset_pipeline(args)
        print(output_path)
        return 0
    if args.pipeline == "model_stage":
        outputs = run_model_stage(args)
        for output in outputs:
            print(output)
        return 0
    if args.pipeline == "learner_stage":
        outputs = run_learner_stage(args)
        for output in outputs:
            print(output)
        return 0
    if args.pipeline == "fuse_stage":
        outputs = run_fuse_stage(args)
        for output in outputs:
            print(output)
        return 0
    if args.pipeline == "classifier_stage":
        result = run_classifier_stage(args)
        print(result)
        return 0
    if args.pipeline == "full":
        result = run_full_pipeline(args)
        print(f"Full pipeline complete: {result['classifier_stage']}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
