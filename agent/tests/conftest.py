"""Lets the agent tests import `signage_agent` (agent/) and the website's `app` package (backend/) from any working directory."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for p in (_ROOT / "agent", _ROOT / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
