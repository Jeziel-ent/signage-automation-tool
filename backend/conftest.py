"""Lets the backend tests run from the repository root too (`PYTHONPATH=. pytest backend/tests/...`), not only from
backend/: pytest loads this file before backend/tests/conftest.py, and the tests import `app` and `tests` from here."""
import sys
from pathlib import Path

_BACKEND = str(Path(__file__).resolve().parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
