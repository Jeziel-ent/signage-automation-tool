"""The interactive API docs must not be public on a server (login on): they list every endpoint and the login does not cover them."""
from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as m


def test_docs_are_on_for_development_and_off_when_login_is_on(monkeypatch):
    monkeypatch.setenv("SIGNAGE_AUTH", "off")
    monkeypatch.delenv("SIGNAGE_API_DOCS", raising=False)
    assert m.api_docs_urls() == {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}
    monkeypatch.setenv("SIGNAGE_AUTH", "on")
    assert m.api_docs_urls() == {"docs_url": None, "redoc_url": None, "openapi_url": None}


def test_an_explicit_switch_turns_them_back_on_for_a_server(monkeypatch):
    monkeypatch.setenv("SIGNAGE_AUTH", "on")
    monkeypatch.setenv("SIGNAGE_API_DOCS", "1")
    assert m.api_docs_urls()["openapi_url"] == "/openapi.json"


def test_an_app_built_with_those_urls_serves_no_docs_pages(monkeypatch):
    """Build an app the way main.py does with login on and ask for the docs pages: nothing is served (no reload of the real module)."""
    from fastapi import FastAPI

    monkeypatch.setenv("SIGNAGE_AUTH", "on")
    monkeypatch.delenv("SIGNAGE_API_DOCS", raising=False)
    c = TestClient(FastAPI(**m.api_docs_urls()))
    assert [c.get(p).status_code for p in ("/docs", "/redoc", "/openapi.json")] == [404, 404, 404]
    monkeypatch.setenv("SIGNAGE_AUTH", "off")
    dev = TestClient(FastAPI(**m.api_docs_urls()))
    assert [dev.get(p).status_code for p in ("/docs", "/redoc", "/openapi.json")] == [200, 200, 200]
