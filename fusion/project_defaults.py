from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_ROOT = PROJECT_ROOT.parent
EPF_ROOT = ASSETS_ROOT / "epf"
LOCAL_DATA_ROOT = PROJECT_ROOT / "data"
CWD_DATA_ROOT = Path.cwd() / "data"
MERGED_DA_SOURCE_ROOT = Path.cwd() / "tmp_merged_dayahead_sources"


def _pick_existing_path(*candidates: Path) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _pick_existing_dir(*candidates: Path) -> Path:
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return candidates[0]


@dataclass(frozen=True)
class ProjectPaths:
    project_root: Path = PROJECT_ROOT
    assets_root: Path = ASSETS_ROOT
    epf_root: Path = EPF_ROOT
    # Prefer current-worktree data first, then the project-local folder, then epf.
    data_xlsx: Path = _pick_existing_path(
        CWD_DATA_ROOT / "shandong_pmos_hourly.xlsx",
        LOCAL_DATA_ROOT / "shandong_pmos_hourly.xlsx",
        EPF_ROOT / "data" / "shandong_pmos_hourly.xlsx",
    )
    data_csv: Path = _pick_existing_path(
        CWD_DATA_ROOT / "shandong_pmos_hourly.csv",
        LOCAL_DATA_ROOT / "shandong_pmos_hourly.csv",
        EPF_ROOT / "data" / "shandong_pmos_hourly.csv",
    )
    external_models_root: Path = ASSETS_ROOT / "models"
    fusion_runs_root: Path = PROJECT_ROOT / "fusion_runs"
    merged_dayahead_source_root: Path = _pick_existing_dir(
        MERGED_DA_SOURCE_ROOT,
        PROJECT_ROOT / "tmp_merged_dayahead_sources",
    )
    preferred_timemixer_candidate: Path = _pick_existing_path(
        PROJECT_ROOT / "TimeMixer" / "candidate_configs" / "module_b_spike_residual_v1.json",
        PROJECT_ROOT / "TimeMixer" / "_archive" / "candidate_configs" / "module_b_spike_residual_v1.json",
    )
    timemixer_output: Path = _pick_existing_dir(
        PROJECT_ROOT / "TimeMixer" / "outputs",
        PROJECT_ROOT / "TimeMixer" / "_archive" / "outputs",
    )
    sgdfnet_output: Path = _pick_existing_dir(
        PROJECT_ROOT / "SGDFNet" / "outputs",
        PROJECT_ROOT / "SGDFNet" / "_archive" / "outputs",
    )
    rt916_output: Path = _pick_existing_dir(
        PROJECT_ROOT / "outputs" / "RT916_SpikeMarketLab" / "model_packages" / "RT916_SpikeFusionNet",
        PROJECT_ROOT / "_archive" / "outputs_root" / "outputs" / "RT916_SpikeMarketLab" / "model_packages" / "RT916_SpikeFusionNet",
    )
    timesfm_output: Path = _pick_existing_dir(
        PROJECT_ROOT / "TimesFM" / "output",
        PROJECT_ROOT / "TimesFM" / "_archive" / "output",
    )
    timesfm_model_dir: Path = _pick_existing_dir(
        PROJECT_ROOT / "models" / "timesFM",
        PROJECT_ROOT / "TimesFM" / "models",
    )
    lightgbm_output: Path = _pick_existing_dir(
        PROJECT_ROOT / "lightGBM" / "outputs",
        PROJECT_ROOT / "lightGBM" / "_archive" / "outputs",
    )


DEFAULTS = ProjectPaths()
