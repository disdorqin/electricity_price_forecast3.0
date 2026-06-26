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

# New ledger production pipelines
from pipelines.ledger_predict import run_ledger_predict
from pipelines.ledger_backfill import run_ledger_backfill
from pipelines.ledger_weight import run_ledger_weight
from pipelines.ledger_fuse import run_ledger_fuse
from pipelines.ledger_classifier import run_ledger_classifier
from pipelines.ledger_full import run_ledger_full
from pipelines.ledger_smoke import run_ledger_smoke


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
    # --- New ledger production pipelines ---
    if args.pipeline == "ledger_predict":
        result = run_ledger_predict(args)
        print(f"ledger_predict complete: {result}")
        return 0
    if args.pipeline == "ledger_backfill":
        result = run_ledger_backfill(args)
        print(f"ledger_backfill complete: {result}")
        return 0
    if args.pipeline == "ledger_weight":
        result = run_ledger_weight(args)
        print(f"ledger_weight complete: {result}")
        return 0
    if args.pipeline == "ledger_fuse":
        result = run_ledger_fuse(args)
        print(f"ledger_fuse complete: {result}")
        return 0
    if args.pipeline == "ledger_classifier":
        result = run_ledger_classifier(args)
        print(f"ledger_classifier complete: {result}")
        return 0
    if args.pipeline == "ledger_full":
        result = run_ledger_full(args)
        print(f"ledger_full complete: {result}")
        return 0
    if args.pipeline == "ledger_smoke":
        result = run_ledger_smoke(args)
        print(f"ledger_smoke complete: {result}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
