from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fusion.pipeline_common import build_common_parser, run_task_pipeline


def main() -> None:
    parser = build_common_parser("dayahead")
    args = parser.parse_args()
    run_task_pipeline("dayahead", args)


if __name__ == "__main__":
    main()
