"""Agent pairing, heartbeat, version and download (app/agents.py). Mock engine, throwaway data directory, a fake agent build."""
from __future__ import annotations

import hashlib
import importlib
import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

ADMIN = {"email": "boss@example.com", "password": "correct-horse-1"}
EXE = b"MZ-fake-agent-exe" * 100


@pytest.fixture
def agent_dist(tmp_path, monkeypatch):
    d = tmp_path / "agent_dist"
    d.mkdir()
    (d / "SignageAgent.exe").write_bytes(EXE)
    (d / "latest.json").write_text(json.dumps({"version": "1.2.0", "sha256": hashlib.sha256(EXE).hexdigest(), "min_supported": "1.0.0"}))
    monkeypatch.setenv("SIGNAGE_AGENT_DIST", str(d))
    return d


@pytest.fixture
def site(tmp_path, monkeypatch, agent_dist):
    monkeypatch.setenv("SIGNAGE_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("SIGNAGE_ENGINE", "mock")
    monkeypatch.setenv("SIGNAGE_AUTH", "on")
    import app.auth as auth_module
    import app.db as db_module
    import app.main as main_module
    importlib.reload(db_module)
    importlib.reload(main_module)
    auth_module._fails.clear()
    c = TestClient(main_module.app)
    assert c.post("/api/auth/setup", json={**ADMIN, "name": "Boss"}).status_code == 200
    return c


def _pair(site, hostname="DESIGN-PC-1"):
    code = site.post("/api/agent/pairing-code").json()["code"]
    anon = TestClient(site.app)
    r = anon.post("/api/agent/register", json={"code": code, "hostname": hostname, "version": "1.0.0",
                                                "corel": [{"progid": "CorelDRAW.Application.27", "major": 27}]})
    return r, code


def _hb(agent_client, token, **body):
    return agent_client.post("/api/agent/heartbeat", json={"version": "1.0.0", **body}, headers={"Authorization": f"Bearer {token}"})


def test_pairing_and_heartbeat_flow(site):
    r, _ = _pair(site)
    assert r.status_code == 200
    token = r.json()["token"]
    anon = TestClient(site.app)
    hb = _hb(anon, token, corel=[{"progid": "CorelDRAW.Application.27", "major": 27}])
    assert hb.status_code == 200 and hb.json()["latest"] == "1.2.0" and hb.json()["update_required"] is False
    agents = site.get("/api/agents").json()["agents"]
    assert len(agents) == 1
    a = agents[0]
    assert a["online"] is True and a["name"] == "DESIGN-PC-1" and a["corel"][0]["major"] == 27 and a["update_available"] is True


def test_a_code_works_once_and_a_wrong_code_fails(site):
    r, code = _pair(site)
    assert r.status_code == 200
    again = TestClient(site.app).post("/api/agent/register", json={"code": code})
    assert again.status_code == 401
    assert TestClient(site.app).post("/api/agent/register", json={"code": "ZZZZ-ZZZZ"}).status_code == 401


def test_code_is_case_and_dash_insensitive(site):
    code = site.post("/api/agent/pairing-code").json()["code"]
    r = TestClient(site.app).post("/api/agent/register", json={"code": code.lower().replace("-", " ")})
    assert r.status_code == 200


def test_expired_code_is_refused(site, monkeypatch):
    code = site.post("/api/agent/pairing-code").json()["code"]
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3600)
    assert TestClient(site.app).post("/api/agent/register", json={"code": code}).status_code == 401


def test_token_is_stored_hashed(site):
    r, _ = _pair(site)
    from app import db
    with db._conn() as conn:
        row = conn.execute("SELECT token_hash FROM agents").fetchone()
    assert r.json()["token"] not in row["token_hash"] and len(row["token_hash"]) == 64


def test_heartbeat_needs_a_valid_token(site):
    anon = TestClient(site.app)
    assert _hb(anon, "nope").status_code == 401
    assert anon.post("/api/agent/heartbeat", json={}).status_code == 401


def test_revoked_agent_is_cut_off(site):
    r, _ = _pair(site)
    token, aid = r.json()["token"], r.json()["agent_id"]
    assert site.delete(f"/api/agents/{aid}").status_code == 200
    assert _hb(TestClient(site.app), token).status_code == 401
    assert site.get("/api/agents").json()["agents"] == []


def test_an_agent_is_listed_offline_after_60_seconds(site, monkeypatch):
    _pair(site)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 120)
    assert site.get("/api/agents").json()["agents"][0]["online"] is False


def test_designers_see_only_their_own_agents_and_cannot_revoke_others(site):
    r, _ = _pair(site)
    aid = r.json()["agent_id"]
    site.post("/api/users", json={"email": "dee@example.com", "password": "designer-pass-1", "role": "designer"})
    dee = TestClient(site.app)
    assert dee.post("/api/auth/login", json={"email": "dee@example.com", "password": "designer-pass-1"}).status_code == 200
    assert dee.get("/api/agents").json()["agents"] == []
    assert dee.get("/api/agents?all=1").json()["agents"] == []          # only an administrator may ask for all
    assert dee.delete(f"/api/agents/{aid}").status_code == 403
    assert len(site.get("/api/agents?all=1").json()["agents"]) == 1


def test_pairing_code_needs_a_login(site):
    assert TestClient(site.app).post("/api/agent/pairing-code").status_code == 401


def test_version_is_public_and_matches_the_build(site):
    r = TestClient(site.app).get("/api/agent/version")
    assert r.status_code == 200
    assert r.json() == {"version": "1.2.0", "sha256": hashlib.sha256(EXE).hexdigest(), "size": len(EXE), "min_supported": "1.0.0"}


def test_download_zip_has_exe_and_a_config_naming_this_server(site):
    r = site.get("/api/agent/download", headers={"host": "signage.example.com", "x-forwarded-proto": "https"})
    assert r.status_code == 200 and "SignageAgent-1.2.0.zip" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert set(z.namelist()) == {"SignageAgent.exe", "config.json", "README.txt"}
    assert z.read("SignageAgent.exe") == EXE
    cfg = json.loads(z.read("config.json"))
    assert cfg == {"server_url": "https://signage.example.com", "allowed_origins": ["https://signage.example.com"]}


def test_public_base_url_overrides_the_host(site, monkeypatch):
    monkeypatch.setenv("SIGNAGE_PUBLIC_BASE_URL", "https://signage.adinn.com/")
    z = zipfile.ZipFile(io.BytesIO(site.get("/api/agent/download").content))
    assert json.loads(z.read("config.json"))["server_url"] == "https://signage.adinn.com"


def test_zip_download_is_public_but_the_raw_exe_needs_a_login_or_an_agent_token(site):
    anon = TestClient(site.app)
    assert anon.get("/api/agent/download").status_code == 200          # offered on the launch screen, before sign-in
    assert anon.get("/api/agent/download?raw=1").status_code == 401
    r, _ = _pair(site)
    raw = anon.get("/api/agent/download?raw=1", headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert raw.status_code == 200 and raw.content == EXE


def test_without_a_build_download_and_version_are_404(site, tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAGE_AGENT_DIST", str(tmp_path / "empty"))
    assert site.get("/api/agent/download").status_code == 404
    assert site.get("/api/agent/version").status_code == 404
    cfg = site.get("/api/agent/config").json()
    assert cfg["download_available"] is False and cfg["latest_version"] is None


def test_config_says_the_agent_is_required_without_a_real_engine(site):
    assert TestClient(site.app).get("/api/agent/config").status_code == 200      # public: the launch screen reads it
    cfg = site.get("/api/agent/config").json()
    assert cfg["agent_required"] is True and cfg["engine"] == "mock" and cfg["latest_version"] == "1.2.0"


def test_old_agents_are_told_to_update(site, agent_dist):
    (agent_dist / "latest.json").write_text(json.dumps({"version": "2.0.0", "sha256": "0" * 64, "min_supported": "1.5.0"}))
    r, _ = _pair(site)
    hb = _hb(TestClient(site.app), r.json()["token"], version="1.0.0")
    assert hb.json()["update_required"] is True
    assert _hb(TestClient(site.app), r.json()["token"], version="1.6.0").json()["update_required"] is False
