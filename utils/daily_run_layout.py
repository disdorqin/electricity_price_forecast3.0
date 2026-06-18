from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DailyRunLayout:
    root: Path
    target_dir: Path
    model_outputs_dir: Path
    learner_inputs_dir: Path
    learner_outputs_dir: Path
    final_dir: Path
    classifier_dir: Path


def build_daily_run_layout(base_dir: str | Path, run_date: str, target: str) -> DailyRunLayout:
    root = Path(base_dir) / str(run_date)
    target_dir = root / str(target)
    model_outputs_dir = target_dir / "model_outputs"
    learner_inputs_dir = target_dir / "learner_inputs"
    learner_outputs_dir = target_dir / "learner_outputs"
    final_dir = target_dir / "final"
    classifier_dir = target_dir / "classifier"

    for path in [
        model_outputs_dir,
        learner_inputs_dir,
        learner_outputs_dir,
        final_dir,
        classifier_dir,
    ]:
        path.mkdir(parents=True, exist_ok=True)

    return DailyRunLayout(
        root=root,
        target_dir=target_dir,
        model_outputs_dir=model_outputs_dir,
        learner_inputs_dir=learner_inputs_dir,
        learner_outputs_dir=learner_outputs_dir,
        final_dir=final_dir,
        classifier_dir=classifier_dir,
    )
