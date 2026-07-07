"""Runner for the P3.2 Extreme Price Shadow test suite.

Usage:
    python tests/run_shadow_tests.py

Prefers the project's conda env (epf-2) when available, otherwise the
current interpreter. The suite is plain pytest and can also be run directly:
    python -m pytest tests/ -q
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EPF2 = r"D:/computer_download/environment/conda/epf-2/python.exe"


def main() -> int:
    py = EPF2 if Path(EPF2).exists() else sys.executable
    cmd = [py, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"]
    print(f"[run_shadow_tests] {py}\n$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=str(REPO)).returncode


if __name__ == "__main__":
    raise SystemExit(main())
