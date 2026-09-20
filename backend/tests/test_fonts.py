from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from app import fonts


def test_registry_names_are_normalised():
    assert fonts.parse_registry_name("Arial (TrueType)") == ["Arial"]
    assert fonts.parse_registry_name("Yu Gothic Medium & Yu Gothic UI Semibold (TrueType)") == ["Yu Gothic Medium", "Yu Gothic UI Semibold"]
    assert fonts.parse_registry_name("Nirmala UI Semilight (OpenType)") == ["Nirmala UI Semilight"]


def test_installed_fonts_falls_back_to_registry_and_caches(monkeypatch):
    monkeypatch.setattr(fonts, "_cache", None)
    monkeypatch.setattr(fonts.sys, "platform", "win32")
    monkeypatch.setattr(fonts, "_from_powershell", lambda: (_ for _ in ()).throw(RuntimeError("no powershell")))
    calls = []
    monkeypatch.setattr(fonts, "_from_registry", lambda: calls.append(1) or ["Zeta", "arial"])
    got = fonts.installed_fonts()
    assert got == {"available": True, "fonts": ["arial", "Zeta"], "source": "registry"}
    fonts.installed_fonts()
    assert len(calls) == 1                      # cached
    fonts.installed_fonts(refresh=True)
    assert len(calls) == 2


def test_unreadable_list_means_unavailable_not_an_empty_allowlist(monkeypatch):
    monkeypatch.setattr(fonts, "_cache", None)
    monkeypatch.setattr(fonts.sys, "platform", "win32")
    boom = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    monkeypatch.setattr(fonts, "_from_powershell", boom)
    monkeypatch.setattr(fonts, "_from_registry", boom)
    assert fonts.installed_fonts()["available"] is False


def test_fonts_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAGE_DATA", str(tmp_path))
    monkeypatch.setenv("SIGNAGE_ENGINE", "mock")
    import app.db as db_module
    import app.main as main_module
    importlib.reload(db_module)
    importlib.reload(main_module)
    monkeypatch.setattr(main_module.fonts, "_cache", {"available": True, "fonts": ["Arial"], "source": "test"})
    assert TestClient(main_module.app).get("/api/fonts").json() == {"available": True, "fonts": ["Arial"], "source": "test"}
