"""The agent against the REAL website app (uvicorn in a thread): pairing, heartbeat, the local status server and its safety rules,
and the update / rollback logic. No CorelDRAW needed."""
from __future__ import annotations

import hashlib
import importlib
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from signage_agent import client, updater
from signage_agent.agent import Agent
from signage_agent.config import Config, normalize_origin


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def site(tmp_path, monkeypatch):
    """The website running for real on a free port, with a fake agent build, a first admin, and a helper to get pairing codes."""
    dist = tmp_path / "dist"
    dist.mkdir()
    exe = b"MZ" + b"new-agent-build" * 200
    (dist / "SignageAgent.exe").write_bytes(exe)
    (dist / "latest.json").write_text(json.dumps({"version": "9.9.9", "sha256": hashlib.sha256(exe).hexdigest(), "size": len(exe),
                                                  "min_supported": "0.0.1"}))
    monkeypatch.setenv("SIGNAGE_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("SIGNAGE_ENGINE", "mock")
    monkeypatch.setenv("SIGNAGE_AUTH", "on")
    monkeypatch.setenv("SIGNAGE_AGENT_DIST", str(dist))
    monkeypatch.delenv("SIGNAGE_PUBLIC_BASE_URL", raising=False)   # backend/.env may set it for development
    import app.db as db_module
    import app.main as main_module
    importlib.reload(db_module)
    importlib.reload(main_module)
    import uvicorn

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(main_module.app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"

    from fastapi.testclient import TestClient
    web = TestClient(main_module.app)
    assert web.post("/api/auth/setup", json={"email": "boss@example.com", "password": "correct-horse-1"}).status_code == 200

    class Site:
        url = base
        exe_bytes = exe

        @staticmethod
        def code() -> str:
            return web.post("/api/agent/pairing-code").json()["code"]

        @staticmethod
        def agents() -> list:
            return web.get("/api/agents").json()["agents"]

    yield Site
    server.should_exit = True
    t.join(timeout=5)


@pytest.fixture
def agent(tmp_path, site):
    home = tmp_path / "agent_home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"server_url": site.url, "allowed_origins": [site.url, "http://localhost:5173"]}))
    return Agent(Config(home))


# ------------------------------------------------------------------ config

def test_config_reads_server_and_origins_and_pairing(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"server_url": "HTTPS://Signage.Example.com/", "allowed_origins": ["https://a.example.com/"]}))
    c = Config(tmp_path)
    assert c.configured and c.server_url == "https://signage.example.com" and not c.paired
    assert c.origin_allowed("https://A.example.com") and not c.origin_allowed("https://evil.example.com") and not c.origin_allowed(None)
    c.save_pairing("id1", "tok", "PC")
    assert Config(tmp_path).paired and Config(tmp_path).token == "tok"
    c.clear_pairing()
    assert not Config(tmp_path).paired


def test_origins_default_to_the_server_url(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"server_url": "https://s.example.com"}))
    assert Config(tmp_path).allowed_origins == ["https://s.example.com"]


def test_unconfigured_agent_reports_it(tmp_path):
    c = Config(tmp_path)
    assert not c.configured and not c.paired
    with pytest.raises(client.ApiError):
        Agent(c).pair("ABCD-EFGH")


# ------------------------------------------------------------------ pairing + heartbeat against the real site

def test_pair_then_heartbeat_shows_the_agent_online(agent, site):
    assert agent.pair(site.code())["ok"] is True
    assert agent.config.paired
    a = site.agents()
    assert len(a) == 1 and a[0]["online"] is True and a[0]["version"] == agent.status()["version"]
    agent.beat()
    assert agent.status()["connected"] is True and agent.latest_version == "9.9.9"


def test_a_wrong_code_is_refused_with_the_servers_message(agent):
    with pytest.raises(client.ApiError) as e:
        agent.pair("AAAA-BBBB")
    assert e.value.status == 401 and "code" in e.value.detail
    assert not agent.config.paired


def test_agent_drops_its_pairing_when_disconnected_on_the_website(agent, site):
    import app.main  # noqa: F401
    from fastapi.testclient import TestClient
    agent.pair(site.code())
    aid = site.agents()[0]["id"]
    web = TestClient(importlib.import_module("app.main").app)
    web.post("/api/auth/login", json={"email": "boss@example.com", "password": "correct-horse-1"})
    assert web.delete(f"/api/agents/{aid}").status_code == 200
    agent.beat()
    assert not agent.config.paired and agent.status()["connected"] is False


def test_unreachable_server_is_reported_not_raised(agent, site):
    agent.pair(site.code())
    agent.config.server_url = "http://127.0.0.1:9"
    agent.beat()
    assert "cannot reach" in agent.last_error and agent.config.paired


# ------------------------------------------------------------------ local status server

def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read() or b"{}")


def _post(url, body, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read() or b"{}")


@pytest.fixture
def local(agent):
    from signage_agent import local_server
    port = _free_port()
    started = local_server.start(agent, ports=[port])
    assert started is not None
    yield f"http://127.0.0.1:{port}"
    started[0].shutdown()


def test_status_is_readable_by_the_site_only(local, agent, site):
    ok = _get(local + "/status", {"Origin": site.url})
    assert ok[0] == 200 and ok[1]["Access-Control-Allow-Origin"] == site.url and ok[1]["Access-Control-Allow-Private-Network"] == "true"
    assert ok[2]["agent"] == "adinn-signage-agent" and ok[2]["paired"] is False and ok[2]["configured"] is True
    other = _get(local + "/status", {"Origin": "https://evil.example.com"})
    assert "Access-Control-Allow-Origin" not in other[1]                 # the browser would refuse to hand the answer to that page


def test_preflight_answers_private_network_access(local, site):
    req = urllib.request.Request(local + "/pair", method="OPTIONS", headers={"Origin": site.url, "Access-Control-Request-Method": "POST",
                                                                           "Access-Control-Request-Private-Network": "true"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 204 and r.headers["Access-Control-Allow-Private-Network"] == "true" and "POST" in r.headers["Access-Control-Allow-Methods"]


def test_pairing_through_the_local_server(local, agent, site):
    s, _, body = _post(local + "/pair", {"code": site.code()}, {"Origin": site.url})
    assert s == 200 and body["ok"] is True and agent.config.paired
    assert _get(local + "/status", {"Origin": site.url})[2]["paired"] is True and len(site.agents()) == 1


def test_pairing_is_refused_for_other_pages_and_for_no_origin(local, agent, site):
    assert _post(local + "/pair", {"code": site.code()}, {"Origin": "https://evil.example.com"})[0] == 403
    assert _post(local + "/pair", {"code": site.code()})[0] == 403
    assert not agent.config.paired


def test_pairing_errors_reach_the_page(local, site):
    s, _, body = _post(local + "/pair", {"code": "ZZZZ-ZZZZ"}, {"Origin": site.url})
    assert s == 401 and "code" in body["detail"]
    assert _post(local + "/pair", {}, {"Origin": site.url})[0] == 400


def test_a_foreign_host_header_is_refused(local, site):
    assert _get(local + "/status", {"Origin": site.url, "Host": "evil.example.com"})[0] == 403


def test_unpair_through_the_local_server(local, agent, site):
    agent.pair(site.code())
    assert _post(local + "/unpair", {}, {"Origin": site.url})[0] == 200 and not agent.config.paired


def test_second_instance_finds_the_port_taken(agent):
    from signage_agent import local_server
    a, p = local_server.start(agent, ports=[_free_port()])
    try:
        assert local_server.start(agent, ports=[p]) is None
    finally:
        a.shutdown()


# ------------------------------------------------------------------ updater

def test_version_order():
    assert updater.version_key("1.10.0") > updater.version_key("1.9.9") > updater.version_key("1.9") and updater.version_key("") == (0,)


def test_update_check_reports_current_available_busy(agent, site, monkeypatch):
    agent.pair(site.code())
    c = agent.config
    assert updater.check_and_apply(c.server_url, c.token, "9.9.9", c.home, idle=True) == "current"
    assert updater.check_and_apply(c.server_url, c.token, "0.1.0", c.home, idle=True) == "available"      # running from source: reports only
    monkeypatch.setattr(updater, "exe_path", lambda: c.home / "SignageAgent.exe")
    assert updater.check_and_apply(c.server_url, c.token, "0.1.0", c.home, idle=False) == "busy"


def test_update_downloads_verifies_and_swaps(agent, site, monkeypatch):
    agent.pair(site.code())
    c = agent.config
    exe = c.home / "SignageAgent.exe"
    exe.write_bytes(b"old build")
    monkeypatch.setattr(updater, "exe_path", lambda: exe)
    swapped = {}
    monkeypatch.setattr(updater, "swap_in", lambda e, n, h, previous_version, target_version="": swapped.update(new=n.read_bytes(), prev=previous_version, target=target_version))
    assert updater.check_and_apply(c.server_url, c.token, "0.1.0", c.home, idle=True) == "updated"
    assert swapped == {"new": site.exe_bytes, "prev": "0.1.0", "target": "9.9.9"}


def test_a_download_that_fails_its_checksum_is_discarded(agent, site, monkeypatch):
    agent.pair(site.code())
    c = agent.config
    dest = c.home / "SignageAgent.new.exe"
    with pytest.raises(client.ApiError):
        client.download_exe(c.server_url, c.token, dest, "0" * 64)
    assert not dest.exists()


def test_swap_renames_keeps_old_and_records_a_pending_update(tmp_path, monkeypatch):
    exe, new = tmp_path / "SignageAgent.exe", tmp_path / "SignageAgent.new.exe"
    exe.write_bytes(b"old")
    new.write_bytes(b"new")
    started = []
    monkeypatch.setattr(updater, "start_new", lambda p: started.append(p))
    monkeypatch.setattr(updater.os, "_exit", lambda code: started.append(("exit", code)))
    updater.swap_in(exe, new, tmp_path, previous_version="1.0.0", target_version="2.0.0")
    assert exe.read_bytes() == b"new" and (tmp_path / "SignageAgent.old.exe").read_bytes() == b"old" and not new.exists()
    assert started == [exe, ("exit", 0)]
    assert json.loads((tmp_path / "update_pending.json").read_text())["target"] == "2.0.0"


def test_confirm_after_update_removes_the_old_exe(tmp_path):
    old = tmp_path / "SignageAgent.old.exe"
    old.write_bytes(b"old")
    (tmp_path / "update_pending.json").write_text(json.dumps({"old": str(old), "since": time.time()}))
    updater.confirm_after_update(tmp_path)
    assert not old.exists() and not (tmp_path / "update_pending.json").exists()


def test_rollback_is_due_only_after_the_grace_period(tmp_path):
    assert updater.rollback_due(tmp_path) is False
    (tmp_path / "update_pending.json").write_text(json.dumps({"old": "x", "since": 1000.0}))
    assert updater.rollback_due(tmp_path, now=1000.0 + updater.GRACE_S - 1) is False
    assert updater.rollback_due(tmp_path, now=1000.0 + updater.GRACE_S + 1) is True


def test_rollback_restores_the_old_exe_and_skips_that_version(tmp_path, monkeypatch):
    exe, old = tmp_path / "SignageAgent.exe", tmp_path / "SignageAgent.old.exe"
    exe.write_bytes(b"broken new")
    old.write_bytes(b"working old")
    (tmp_path / "update_pending.json").write_text(json.dumps({"old": str(old), "target": "2.0.0", "since": 1.0}))
    started = []
    monkeypatch.setattr(updater, "exe_path", lambda: exe)
    monkeypatch.setattr(updater, "start_new", lambda p: started.append(p))
    monkeypatch.setattr(updater.os, "_exit", lambda code: started.append(("exit", code)))
    updater.rollback(tmp_path)
    assert exe.read_bytes() == b"working old" and (tmp_path / "SignageAgent.bad.exe").read_bytes() == b"broken new"
    assert updater._skipped(tmp_path) == "2.0.0" and not (tmp_path / "update_pending.json").exists()
    assert started == [exe, ("exit", 0)]


def test_a_skipped_version_is_not_tried_again(agent, site, monkeypatch):
    agent.pair(site.code())
    c = agent.config
    (c.home / "update_skip.json").write_text(json.dumps({"version": "9.9.9"}))
    monkeypatch.setattr(updater, "exe_path", lambda: c.home / "SignageAgent.exe")
    assert updater.check_and_apply(c.server_url, c.token, "0.1.0", c.home, idle=True).startswith("failed")


def test_normalize_origin():
    assert normalize_origin(" HTTP://Localhost:5173/ ") == "http://localhost:5173"


def test_cleanup_removes_leftover_exes_but_not_while_an_update_is_pending(tmp_path, monkeypatch):
    exe = tmp_path / "SignageAgent.exe"
    exe.write_bytes(b"current")
    for n in ("SignageAgent.old.exe", "SignageAgent.bad.exe", "SignageAgent.new.exe"):
        (tmp_path / n).write_bytes(b"x")
    monkeypatch.setattr(updater, "exe_path", lambda: exe)
    (tmp_path / "update_pending.json").write_text("{}")
    updater.cleanup_leftovers(tmp_path)
    assert (tmp_path / "SignageAgent.old.exe").exists()          # an update is still waiting to be confirmed
    (tmp_path / "update_pending.json").unlink()
    updater.cleanup_leftovers(tmp_path)
    assert exe.exists() and not any((tmp_path / n).exists() for n in ("SignageAgent.old.exe", "SignageAgent.bad.exe", "SignageAgent.new.exe"))


def test_swap_works_while_an_older_copy_still_holds_the_old_exe(tmp_path, monkeypatch):
    """Found live: a launcher of the previous version kept running from SignageAgent.old.exe, so the next update failed with
    'Access is denied'. The swap now keeps the current exe under a unique old name instead."""
    exe, new = tmp_path / "SignageAgent.exe", tmp_path / "SignageAgent.new.exe"
    exe.write_bytes(b"current")
    new.write_bytes(b"newest")
    (tmp_path / "SignageAgent.old.exe").write_bytes(b"still running")
    real_unlink = Path.unlink

    def locked(self, missing_ok=False):
        if self.name == "SignageAgent.old.exe":
            raise PermissionError(5, "Access is denied")
        return real_unlink(self, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", locked)
    monkeypatch.setattr(updater, "start_new", lambda p: None)
    monkeypatch.setattr(updater.os, "_exit", lambda code: None)
    updater.swap_in(exe, new, tmp_path, previous_version="1.0.0", target_version="2.0.0")
    kept = list(tmp_path.glob("SignageAgent.old-*.exe"))
    assert exe.read_bytes() == b"newest" and len(kept) == 1 and kept[0].read_bytes() == b"current"
    assert json.loads((tmp_path / "update_pending.json").read_text())["old"] == str(kept[0])
