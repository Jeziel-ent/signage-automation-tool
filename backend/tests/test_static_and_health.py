"""/healthz and serving the built web app (single-page-app fallback) from the API. Mock engine, throwaway data directory."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<html>APP</html>")
    (d / "assets" / "app-abc123.js").write_text("console.log(1)")
    (d / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("outside the dist folder")
    return d


def _client(tmp_path, monkeypatch, dist_dir):
    monkeypatch.setenv("SIGNAGE_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("SIGNAGE_ENGINE", "mock")
    monkeypatch.setenv("SIGNAGE_AUTH", "on")
    monkeypatch.setenv("SIGNAGE_FRONTEND_DIST", str(dist_dir))
    import app.db as db_module
    import app.main as main_module
    importlib.reload(db_module)
    importlib.reload(main_module)
    return TestClient(main_module.app), main_module


def test_pages_are_served_without_a_login(tmp_path, monkeypatch, dist):
    c, _ = _client(tmp_path, monkeypatch, dist)
    r = c.get("/")
    assert r.status_code == 200 and "APP" in r.text and r.headers["cache-control"] == "no-cache"
    assert "APP" in c.get("/masters").text                      # a reload on a client-side route
    assert "APP" in c.get("/editor/job1/shop1").text


def test_hashed_assets_are_cached_for_good_and_static_files_are_found(tmp_path, monkeypatch, dist):
    c, _ = _client(tmp_path, monkeypatch, dist)
    r = c.get("/assets/app-abc123.js")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    assert c.get("/favicon.svg").text == "<svg/>"


def test_unknown_api_paths_stay_json_404_not_the_page(tmp_path, monkeypatch, dist):
    c, _ = _client(tmp_path, monkeypatch, dist)
    login = c.post("/api/auth/setup", json={"email": "a@b.co", "password": "long-enough-1"})
    assert login.status_code == 200
    r = c.get("/api/does-not-exist")
    assert r.status_code == 404 and "APP" not in r.text


def test_paths_cannot_escape_the_dist_folder(tmp_path, monkeypatch, dist):
    c, _ = _client(tmp_path, monkeypatch, dist)
    for p in ("/../secret.txt", "/..%2Fsecret.txt", "/assets/..%2F..%2Fsecret.txt"):
        assert "outside the dist folder" not in c.get(p).text


def test_without_a_build_there_is_no_page(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch, tmp_path / "nothing-here")
    assert c.get("/").status_code == 404


def test_healthz_is_public_and_reports_checks(tmp_path, monkeypatch, dist):
    c, _ = _client(tmp_path, monkeypatch, dist)
    r = c.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["checks"]["database"] == "ok" and body["checks"]["data_dir"] == "ok"
    assert body["mock_engine"] is True and body["checks"]["disk_free_gb"] > 0


def test_healthz_is_503_when_the_disk_is_nearly_full(tmp_path, monkeypatch, dist):
    c, _ = _client(tmp_path, monkeypatch, dist)
    monkeypatch.setenv("SIGNAGE_MIN_FREE_DISK_GB", "99999999")
    r = c.get("/healthz")
    assert r.status_code == 503 and r.json()["ok"] is False and "disk" in r.json()["checks"]


def test_healthz_is_503_when_the_database_fails(tmp_path, monkeypatch, dist):
    c, main = _client(tmp_path, monkeypatch, dist)

    def boom():
        raise RuntimeError("database is locked")
    monkeypatch.setattr(main.db, "_conn", boom)
    r = c.get("/healthz")
    assert r.status_code == 503 and "database is locked" in r.json()["checks"]["database"]


def test_require_real_engine_refuses_the_mock(tmp_path, monkeypatch, dist):
    _, main = _client(tmp_path, monkeypatch, dist)
    main._refuse_mock_engine_in_production()                     # not set: fine
    monkeypatch.setenv("SIGNAGE_REQUIRE_REAL_ENGINE", "1")
    with pytest.raises(RuntimeError, match="mock"):
        main._refuse_mock_engine_in_production()
