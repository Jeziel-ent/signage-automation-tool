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
