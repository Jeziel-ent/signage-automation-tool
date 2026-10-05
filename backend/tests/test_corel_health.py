"""GET /api/corel/health - what the launch screen checks. Never starts CorelDRAW."""
from __future__ import annotations

import sys

from tests.test_main_v2 import client  # noqa: F401  (fixture re-export)

INSTALL = {"progid": "CorelDRAW.Application", "exe": "c:/Corel/27/CorelDRW.exe", "version": "27.0.0.121"}


def _main():
    return sys.modules["app.main"]


def _patch(monkeypatch, installs, free=4.0, engine="corel", platform="win32"):
    m = _main()
    monkeypatch.setenv("SIGNAGE_ENGINE", engine)
    monkeypatch.setattr(m.sys, "platform", platform)
    monkeypatch.setattr(m.corel_util, "corel_installs", lambda: installs)
    monkeypatch.setattr(m.corel_util, "check_memory", lambda *a, **k: free)
    monkeypatch.setattr(m.corel_util, "dispatch_corel", lambda: (_ for _ in ()).throw(AssertionError("must not launch CorelDRAW")))


def test_ready_when_an_install_is_found(client, monkeypatch):  # noqa: F811
    _patch(monkeypatch, [INSTALL])
    body = client.get("/api/corel/health").json()
    assert body["ok"]
    assert body["engine"] == "corel"
    assert body["selected"] == INSTALL
    assert body["message"] == "CorelDRAW 27.0.0.121 ready"
    assert not body["low_memory"]


def test_low_memory_is_reported_but_does_not_fail_the_check(client, monkeypatch):  # noqa: F811
    _patch(monkeypatch, [INSTALL], free=1.2)
    body = client.get("/api/corel/health").json()
    assert body["ok"]
    assert body["low_memory"]
    assert "low memory" in body["message"]


def test_not_ok_without_an_install(client, monkeypatch):  # noqa: F811
    _patch(monkeypatch, [])
    body = client.get("/api/corel/health").json()
    assert not body["ok"]
    assert not body["corel_available"]
    assert "No CorelDRAW installation" in body["message"]


def test_mock_engine_is_ok_without_corel(client, monkeypatch):  # noqa: F811
    _patch(monkeypatch, [], engine="mock")
    body = client.get("/api/corel/health").json()
    assert body["ok"]
    assert body["engine"] == "mock"


def test_auto_engine_off_windows_is_mock(client, monkeypatch):  # noqa: F811
    _patch(monkeypatch, [], engine="auto", platform="linux")
    body = client.get("/api/corel/health").json()
    assert body["ok"]
    assert body["engine"] == "mock"
