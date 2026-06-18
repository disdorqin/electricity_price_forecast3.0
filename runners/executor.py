from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass

from pipelines.base import PredictionResult
from runners.registry import get_model_pipeline


CPU_MODELS = {"lightgbm", "sgdfnet"}
GPU_MODELS = {"rt916", "timemixer", "timesfm"}


@dataclass
class TaskSpec:
    model_name: str
    pipeline_name: str
    target: str
    kwargs: dict


def _run_task(task: TaskSpec) -> PredictionResult | None:
    pipeline = get_model_pipeline(task.model_name)
    method = getattr(pipeline, task.pipeline_name)
    return method(target=task.target, **task.kwargs)


def execute_tasks(tasks: list[TaskSpec], max_cpu_workers: int = 2, max_gpu_workers: int = 1) -> list[PredictionResult]:
    cpu_tasks = [task for task in tasks if task.model_name in CPU_MODELS]
    gpu_tasks = [task for task in tasks if task.model_name in GPU_MODELS]
    results: list[PredictionResult] = []

    for grouped_tasks, workers in ((cpu_tasks, max_cpu_workers), (gpu_tasks, max_gpu_workers)):
        if not grouped_tasks:
            continue
        if workers <= 1 or len(grouped_tasks) == 1:
            for task in grouped_tasks:
                result = _run_task(task)
                if result is not None:
                    results.append(result)
            continue
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_run_task, task) for task in grouped_tasks]
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    results.append(result)
    return results
