"""The website image installs requirements-server.txt only (no numpy, scipy, pywin32): the app must import and start without them.
A module-level `import numpy` in a module main.py pulls in (master_store -> background_analysis) crashed the 2026-11-23 deploy: uvicorn died on import and /healthz never answered."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

SCRIPT = r"""
import os, sys
for m in ("numpy", "scipy", "win32com", "win32com.client", "pythoncom", "pywintypes", "win32gui", "win32con", "win32api", "win32process"):
    sys.modules[m] = None          # import raises ImportError, as on the server image
from fastapi.testclient import TestClient
import app.main as main
with TestClient(main.app) as c:
    r = c.get("/healthz")
    print("HEALTHZ", r.status_code)
"""


def test_the_app_starts_without_numpy_scipy_or_windows_modules():
    env = {**os.environ, "SIGNAGE_DATA": tempfile.mkdtemp(), "SIGNAGE_ENGINE": "mock", "SIGNAGE_AUTH": "off", "SIGNAGE_MASTER_ANALYSIS": "0", "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, "-c", SCRIPT], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=240)
    assert "HEALTHZ 200" in p.stdout, p.stderr[-1500:]
