"""The /admin page's API: designers with their usage, deleting accounts, server health, Spaces usage."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import spaces, sysmetrics
from tests.test_agent_queue import DEE, _pair, _shop, world  # noqa: F401  (world is a fixture)


def _users(world, days=30):
    return {u["email"]: u for u in world.boss.get(f"/api/admin/designers?days={days}").json()["users"]}


def _job(world, job_id, kind, shop, uid, state):
    world.db.create_agent_job(job_id, kind, shop, uid, {"x": 1})
    with world.db._conn() as c:
        c.execute("UPDATE agent_jobs SET state=? WHERE id=?", (state, job_id))


def _login(world, email, password):
    c = TestClient(world.main.app)
    assert c.post("/api/auth/login", json={"email": email, "password": password}).status_code == 200
    return c


# ------------------------------------------------------------------ designers + usage

def test_only_administrators_see_the_admin_api(world):
    for path in ("/api/admin/designers", "/api/admin/health", "/api/admin/spaces"):
        assert world.dee.get(path).status_code == 403
        assert world.boss.get(path).status_code == 200


def test_designers_list_shows_usage_and_pcs(world):
    _pair(world.dee, "Dee-PC")
    _job_id, shop = _shop(world.dee)
    uid = _users(world)[DEE["email"]]["id"]
    _job(world, "j1", "convert", shop, uid, "done")
    _job(world, "j2", "export", shop, uid, "done")
    _job(world, "j3", "convert", shop, uid, "failed")
    users = _users(world)
    dee = users[DEE["email"]]
    assert (dee["converted"], dee["exported"], dee["failed"]) == (1, 1, 1)
    assert dee["role"] == "designer" and dee["active"] is True and dee["signed_in"] is True and dee["logins"] >= 1
    assert dee["last_login"] and dee["last_active"]
    assert [p["name"] for p in dee["pcs"]] == ["Dee-PC"]
    boss = users["boss@example.com"]
    assert boss["converted"] == 0 and boss["pcs"] == []


def test_the_usage_window_only_counts_recent_work(world):
    _job_id, shop = _shop(world.dee)
    uid = _users(world)[DEE["email"]]["id"]
    _job(world, "old", "convert", shop, uid, "done")
    with world.db._conn() as c:
        c.execute("UPDATE agent_jobs SET created_at=? WHERE id='old'", (time.time() - 40 * 86400,))
    assert _users(world, 30)[DEE["email"]]["converted"] == 0
    assert _users(world, 90)[DEE["email"]]["converted"] == 1


# ------------------------------------------------------------------ delete

def test_deleting_a_designer_removes_the_account_and_their_pcs(world):
    _pair(world.dee, "Dee-PC")
    uid = _users(world)[DEE["email"]]["id"]
    assert world.boss.delete(f"/api/users/{uid}").json() == {"deleted": DEE["email"]}
    assert DEE["email"] not in _users(world)
    assert world.dee.get("/api/auth/me").json().get("user") is None                  # their session is gone at once
    assert world.db.list_agents(uid) == []                                          # their PC is disconnected
    log = world.boss.get("/api/admin/audit?action=user_deleted").json()["entries"]
    assert log and log[0]["target"] == DEE["email"]                                  # the activity log remembers it
    assert world.boss.delete(f"/api/users/{uid}").status_code == 404


def test_you_cannot_delete_yourself_or_the_last_administrator(world):
    boss = _users(world)["boss@example.com"]
    r = world.boss.delete(f"/api/users/{boss['id']}")
    assert r.status_code == 409 and "signed in with" in r.json()["detail"]
    assert world.dee.delete(f"/api/users/{boss['id']}").status_code == 403           # designers cannot delete anyone
    world.boss.post("/api/users", json={"email": "b2@example.com", "password": "correct-horse-2", "role": "admin"})
    b2 = _login(world, "b2@example.com", "correct-horse-2")
    assert b2.delete(f"/api/users/{boss['id']}").status_code == 200                  # two admins: one may go
    me = next(u for u in b2.get("/api/users").json()["users"] if u["email"] == "b2@example.com")
    assert b2.delete(f"/api/users/{me['id']}").status_code == 409                    # never yourself


def test_a_deleted_accounts_waiting_jobs_fail_instead_of_hanging(world):
    _job_id, shop = _shop(world.dee)
    uid = _users(world)[DEE["email"]]["id"]
    world.db.create_agent_job("q1", "convert", shop, uid, {})
    world.boss.delete(f"/api/users/{uid}")
    with world.db._conn() as c:
        row = c.execute("SELECT state, error FROM agent_jobs WHERE id='q1'").fetchone()
    assert row["state"] == "failed" and "deleted" in row["error"]


# ------------------------------------------------------------------ health

def test_health_reports_strength_and_queue(world):
    h = world.boss.get("/api/admin/health").json()
    assert h["cpu_count"] >= 1 and h["disk"]["total"] > h["disk"]["free"] > 0
    assert set(h["levels"]) == {"cpu", "memory", "disk", "load"}
    assert h["queue"] == {"queued": 0, "running": 0, "failed_24h": 0} and h["agents_online"] == 0 and h["engine"] == "remote"
    _pair(world.dee)
    assert world.boss.get("/api/admin/health").json()["agents_online"] == 1


def test_levels_turn_warn_and_bad():
    assert sysmetrics._level(10, 70, 90) == "ok" and sysmetrics._level(75, 70, 90) == "warn"
    assert sysmetrics._level(95, 70, 90) == "bad" and sysmetrics._level(None, 70, 90) == "unknown"


def test_health_still_answers_without_psutil(tmp_path, monkeypatch):
    monkeypatch.setattr(sysmetrics, "psutil", None)
    snap = sysmetrics.snapshot(Path(tmp_path))
    assert snap["available"] is False and snap["cpu_pct"] is None and snap["disk"]["total"] > 0 and snap["levels"]["cpu"] == "unknown"


# ------------------------------------------------------------------ Spaces

class _FakeS3:
    def __init__(self, pages):
        self.pages, self.calls = pages, 0

    def list_objects_v2(self, **kw):
        page = self.pages[self.calls]
        self.calls += 1
        return page


def _configure(monkeypatch, quota=None):
    monkeypatch.setenv("SIGNAGE_SPACES_KEY", "k")
    monkeypatch.setenv("SIGNAGE_SPACES_SECRET", "s")
    monkeypatch.setenv("SIGNAGE_SPACES_BUCKET", "adinn-space")
    if quota:
        monkeypatch.setenv("SIGNAGE_SPACES_QUOTA_GB", str(quota))


def test_spaces_not_configured_says_so():
    u = spaces.usage()
    assert u["configured"] is False and "SIGNAGE_SPACES_KEY" in u["message"]


def test_spaces_usage_adds_up_every_page_and_is_cached(monkeypatch):
    _configure(monkeypatch, quota=250)
    fake = _FakeS3([{"Contents": [{"Size": 100}, {"Size": 50}], "IsTruncated": True, "NextContinuationToken": "t"},
                    {"Contents": [{"Size": 25}], "IsTruncated": False}])
    u = spaces.usage(lambda cfg: fake, now=1000.0)
    assert (u["bytes"], u["objects"], u["bucket"], u["region"]) == (175, 3, "adinn-space", "sgp1") and u["quota_bytes"] == 250 * 1024 ** 3
    assert spaces.usage(lambda cfg: 1 / 0, now=1100.0) is u                     # within 10 minutes: no second listing
    assert spaces.usage(lambda cfg: _FakeS3([{"Contents": [{"Size": 1}]}]), now=1000.0 + spaces.CACHE_S + 1)["bytes"] == 1


def test_spaces_problems_are_reported_not_raised(monkeypatch):
    _configure(monkeypatch)

    def boom(cfg):
        raise PermissionError("bad keys")

    assert "could not read the bucket" in spaces.usage(boom, now=1.0)["error"]
    spaces.reset()

    def missing(cfg):
        raise ImportError("boto3")

    assert "boto3" in spaces.usage(missing, now=1.0)["error"]


def test_spaces_endpoint_never_leaks_the_keys(world, monkeypatch):
    _configure(monkeypatch)
    spaces._cache.update(at=time.time(), value={"configured": True, "bucket": "adinn-space", "bytes": 5})
    body = world.boss.get("/api/admin/spaces").text
    assert "adinn-space" in body and '"s"' not in body and '"k"' not in body
