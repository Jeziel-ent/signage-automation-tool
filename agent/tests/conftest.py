"""Lets the agent tests import `signage_agent` (agent/) and the website's `app` package (backend/) from any working directory."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for p in (_ROOT / "agent", _ROOT / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_limits():
    """The website's in-memory rate limiter and alert state must not carry over from one test to the next."""
    from app import alerts, ratelimit
    ratelimit.reset()
    alerts.reset()
    yield
