import sys
from pathlib import Path

# Ensure the repository root is importable so that `pipelines`, `experimental`
# and `tests` packages can be imported from anywhere pytest is invoked.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
