"""Run the burst B.6 scenario; see experiments/fault_tests/README.md."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run import main

if __name__ == "__main__":
    main("burst")
