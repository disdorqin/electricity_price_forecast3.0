from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

if __package__ in {None, ""}:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from fusion.registry import get_adapter
else:
    from .registry import get_adapter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect model outputs into one normalized fusion long-table CSV.")
    parser.add_argument("--manifest", required=True, help="Path to a CSV manifest describing model outputs.")
    parser.add_argument("--output", required=True, help="Path to the merged normalized CSV.")
    return parser


def _clean_options(record: dict[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for key, value in record.items():
        if key in {"adapter", "source"}:
            continue
        if pd.isna(value):
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                continue
            if stripped.lower() in {"true", "false"}:
                options[key] = stripped.lower() == "true"
                continue
            options[key] = stripped
            continue
        options[key] = value
    return options


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("JSON manifest must be a list of adapter records")
        return payload
    return pd.read_csv(path).to_dict(orient="records")


def main() -> None:
    args = build_parser().parse_args()
    manifest_path = Path(args.manifest)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    records = _load_manifest(manifest_path)
    frames: list[pd.DataFrame] = []

    for record in records:
        adapter_name = str(record["adapter"]).strip()
        source = record["source"]
        options = _clean_options(record)
        adapter_cls = get_adapter(adapter_name)
        adapter = adapter_cls(source, **options)
        frames.append(adapter.load())

    if not frames:
        raise ValueError("Manifest produced no prediction tables")

    merged = pd.concat(frames, ignore_index=True)
    merged.to_csv(output_path, index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
