"""Every test runs against a throwaway CorelDRAW PID-tracking file.

corel_util.PID_FILE is computed at import from SIGNAGE_DATA, i.e. the REAL backend/data/corel_launched_pids.json. The
supervisor's hang tests end in corel_util.cleanup_orphaned_instances(), which force-kills every tracked CorelDRW.exe that
the calling process is not using - so running the suite while the app was converting or building a scene killed the app's
own CorelDRAW (seen 2026-10-01: a scene export died at "The RPC server is unavailable" and cached a half-empty scene).
With this fixture a test only ever sees its own empty tracking file.
"""
from __future__ import annotations

import pytest

from app import corel_util


@pytest.fixture(autouse=True)
def _isolated_corel_tracking(tmp_path_factory, monkeypatch):
    data = tmp_path_factory.mktemp("signage_data")
    monkeypatch.setattr(corel_util, "PID_FILE", data / "corel_launched_pids.json")


@pytest.fixture(autouse=True)
def _login_off_by_default(monkeypatch):
    """The API needs a login (app/auth.py); the existing API tests exercise routes, not accounts, so they run with it off.
    test_auth.py switches it back on."""
    monkeypatch.setenv("SIGNAGE_AUTH", "off")
    # backend/.env is loaded into the environment (app/__init__.py): a developer's own settings must not change what the tests expect
    for name in ("SIGNAGE_PUBLIC_BASE_URL", "SIGNAGE_ADMIN_EMAIL", "SIGNAGE_ADMIN_PASSWORD", "SIGNAGE_AGENT_REQUIRED", "SIGNAGE_CORS_ORIGINS",
                 "SIGNAGE_MAX_UPLOAD_MB", "SIGNAGE_REQUIRE_REAL_ENGINE", "SIGNAGE_MIN_FREE_DISK_GB"):
        monkeypatch.delenv(name, raising=False)
