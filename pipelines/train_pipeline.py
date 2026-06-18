from __future__ import annotations

from runners.executor import TaskSpec
from runners.registry import get_registered_models
from services.predict_service import run_predict_tasks


def _parse_models(raw_models: str) -> list[str]:
    if raw_models.strip().lower() == "all":
        return get_registered_models()
    return [item.strip().lower() for item in raw_models.split(",") if item.strip()]


def _parse_targets(raw_target: str) -> list[str]:
    if raw_target == "both":
        return ["dayahead", "realtime"]
    return [raw_target]


def run_train_pipeline(args):
    tasks: list[TaskSpec] = []
    for model_name in _parse_models(args.models):
        for target in _parse_targets(args.target):
            tasks.append(
                TaskSpec(
                    model_name=model_name,
                    pipeline_name="train",
                    target=target,
                    kwargs={
                        "predict_date": args.date,
                        "start": args.start,
                        "end": args.end,
                        "forecast_start": args.start or args.date,
                        "forecast_end": args.end or args.date,
                        "data_path": args.data_path,
                        "output_root": args.output_root,
                        "training_months": args.training_months,
                        "val_ratio": args.val_ratio,
                        "use_predicted_temp": args.use_predicted_temp,
                    },
                )
            )
    return run_predict_tasks(tasks, args.max_cpu_workers, args.max_gpu_workers)
