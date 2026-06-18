from __future__ import annotations

from runners.executor import TaskSpec, execute_tasks


def run_predict_tasks(tasks: list[TaskSpec], max_cpu_workers: int, max_gpu_workers: int):
    return execute_tasks(tasks, max_cpu_workers=max_cpu_workers, max_gpu_workers=max_gpu_workers)
