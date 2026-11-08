"""The agent job queue (SIGNAGE_ENGINE=remote): a convert becomes a job for the user's own agent, which claims it, downloads the master,
reports progress, uploads its files and posts the result; leases expire, claims are atomic, other users' agents get nothing."""
from __future__ import annotations

import importlib
import io
import json
import threading
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

ADMIN = {"email": "boss@example.com", "password": "correct-horse-1"}
DEE = {"email": "dee@example.com", "password": "designer-pass-1"}


def _cdr() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/x-cdr")
    return buf.getvalue()


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAGE_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("SIGNAGE_ENGINE", "remote")
    monkeypatch.setenv("SIGNAGE_AUTH", "on")
    monkeypatch.setenv("SIGNAGE_AGENT_DIST", str(tmp_path / "nodist"))
    import app.auth as auth_module
    import app.db as db_module
    import app.main as main_module
    importlib.reload(db_module)
    importlib.reload(main_module)
    auth_module._fails.clear()
    boss = TestClient(main_module.app)
    assert boss.post("/api/auth/setup", json={**ADMIN, "name": "Boss"}).status_code == 200
    assert boss.post("/api/users", json={**DEE, "role": "designer"}).status_code == 200
    dee = TestClient(main_module.app)
    assert dee.post("/api/auth/login", json=DEE).status_code == 200

    class W:
        pass

    w = W()
    w.main, w.boss, w.dee, w.db = main_module, boss, dee, db_module
    return w


def _pair(site_client, hostname="PC"):
    code = site_client.post("/api/agent/pairing-code").json()["code"]
    r = TestClient(site_client.app).post("/api/agent/register", json={"code": code, "hostname": hostname, "version": "1.0.0"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["agent_id"]


def _shop(client, name="NR Traders"):
    job = client.post("/api/v2/upload", data={"brand": "dalmia"}, files={"master": ("m.cdr", _cdr(), "application/octet-stream")}).json()["id"]
    shop = client.post(f"/api/v2/jobs/{job}/shops", json={"name": name, "width": 12, "width_unit": "ft", "height": 4, "height_unit": "ft"}).json()
    return job, shop["id"]


def _convert(client, shop_id):
    return client.post(f"/api/v2/shops/{shop_id}/convert")


class Agent:
    """A fake agent: its own HTTP client + token."""

    def __init__(self, site_client, headers, agent_id):
        self.c = TestClient(site_client.app)
        self.h, self.id = headers, agent_id

    def claim(self, n=5):
        r = self.c.post("/api/agent/jobs/claim", json={"max": n}, headers=self.h)
        assert r.status_code == 200, r.text
        return r.json()["jobs"]

    def progress(self, job_id, step):
        return self.c.post(f"/api/agent/jobs/{job_id}/progress", json={"step": step}, headers=self.h)

    def upload(self, job_id, name, data):
        return self.c.put(f"/api/agent/jobs/{job_id}/files/{name}", content=data, headers=self.h)

    def result(self, job_id, **body):
        return self.c.post(f"/api/agent/jobs/{job_id}/result", json=body, headers=self.h)

    def master(self, job_id):
        return self.c.get(f"/api/agent/jobs/{job_id}/master", headers=self.h)


@pytest.fixture
def dee_agent(world):
    h, aid = _pair(world.dee)
    return Agent(world.dee, h, aid)


def test_convert_in_remote_mode_queues_a_job_for_the_agent(world, dee_agent):
    _, sid = _shop(world.dee)
    r = _convert(world.dee, sid)
    assert r.status_code == 200 and r.json()["queued_for_agent"] is True
    assert world.dee.get(f"/api/v2/shops/{sid}/status").json()["status"] == "queued"
    jobs = dee_agent.claim()
    assert len(jobs) == 1 and jobs[0]["kind"] == "convert"
    assert jobs[0]["shop"]["name"] == "NR Traders" and abs(jobs[0]["shop"]["width"] - 3657.6) < 0.5
    assert jobs[0]["master"]["size"] > 0 and jobs[0]["master"]["key"]
    assert "master_path" not in json.dumps(jobs[0]) and "path" not in jobs[0]["master"]      # server paths stay on the server
    assert world.dee.get(f"/api/v2/shops/{sid}/status").json()["status"] == "converting"


def test_the_full_cycle_makes_the_shop_done_with_its_files(world, dee_agent):
    job, sid = _shop(world.dee)
    _convert(world.dee, sid)
    j = dee_agent.claim()[0]
    m = dee_agent.master(j["id"])
    assert m.status_code == 200 and m.content[:2] == b"PK"
    assert dee_agent.progress(j["id"], "pdf").status_code == 200
    st = world.dee.get(f"/api/v2/shops/{sid}/status").json()
    assert st["status"] == "converting" and st["step"] == "pdf" and st["progress_pct"] == 88
    assert dee_agent.upload(j["id"], "board.cdr", b"CDR-BYTES").status_code == 200
    assert dee_agent.upload(j["id"], "board.pdf", b"%PDF-1").status_code == 200
    assert dee_agent.upload(j["id"], "preview.png", b"PNG").status_code == 200
    res = dee_agent.result(j["id"], status="done", files={"cdr": "board.cdr", "pdf": "board.pdf", "preview": "preview.png"},
                           report={"warnings": [], "layout": {"mode": "rules"}})
    assert res.status_code == 200
    st = world.dee.get(f"/api/v2/shops/{sid}/status").json()
    assert st["status"] == "done" and st["progress_pct"] == 100
    assert set(st["files"]) == {"cdr", "pdf", "preview"}
    assert st["report"]["master_used"]["job_id"] == job            # recorded exactly like a local conversion
    assert world.dee.get(f"/api/v2/shops/{sid}/files/board.pdf").content == b"%PDF-1"      # the existing download route serves it


def test_a_failed_job_fails_the_shop_with_the_agents_reason(world, dee_agent):
    _, sid = _shop(world.dee)
    _convert(world.dee, sid)
    j = dee_agent.claim()[0]
    assert dee_agent.result(j["id"], status="error", error="CorelDRAW could not open the file").status_code == 200
    st = world.dee.get(f"/api/v2/shops/{sid}/status").json()
    assert st["status"] == "failed" and "could not open" in st["error"]


def test_a_user_only_gets_their_own_agents_jobs_claimed(world, dee_agent):
    boss_h, boss_id = _pair(world.boss, "BOSS-PC")
    boss_agent = Agent(world.boss, boss_h, boss_id)
    _, sid = _shop(world.dee)
    _convert(world.dee, sid)
    assert boss_agent.claim() == []                                  # the administrator's PC must not take a designer's job
    assert len(dee_agent.claim()) == 1


def test_a_job_is_claimed_once(world, dee_agent):
    _, sid = _shop(world.dee)
    _convert(world.dee, sid)
    assert len(dee_agent.claim()) == 1
    assert dee_agent.claim() == []


def test_two_agents_of_one_user_never_get_the_same_job(world):
    h1, id1 = _pair(world.dee, "PC-A")
    h2, id2 = _pair(world.dee, "PC-B")
    a, b = Agent(world.dee, h1, id1), Agent(world.dee, h2, id2)
    for i in range(6):
        _, sid = _shop(world.dee, f"Shop {i}")
        _convert(world.dee, sid)
    got: list = []
    barrier = threading.Barrier(2)

    def go(agent):
        barrier.wait()
        got.extend(j["id"] for j in agent.claim(3))

    t = [threading.Thread(target=go, args=(x,)) for x in (a, b)]
    [x.start() for x in t]
    [x.join() for x in t]
    assert len(got) == 6 and len(set(got)) == 6                      # every job exactly once


def test_claim_batches_at_most_five(world, dee_agent):
    for i in range(7):
        _, sid = _shop(world.dee, f"Shop {i}")
        _convert(world.dee, sid)
    assert len(dee_agent.claim(50)) == 5
    assert len(dee_agent.claim(50)) == 2


def test_an_expired_lease_requeues_the_job_and_the_shop(world, dee_agent):
    _, sid = _shop(world.dee)
    _convert(world.dee, sid)
    j = dee_agent.claim()[0]
    assert world.db.expire_agent_jobs(now=time.time() + 3600)[0]["state"] == "queued"
    world.main.agents.expire_and_fail()
    assert world.dee.get(f"/api/v2/shops/{sid}/status").json()["status"] in ("queued", "converting")
    # the dead claim no longer works...
    assert dee_agent.progress(j["id"], "pdf").status_code == 409
    assert dee_agent.result(j["id"], status="done").status_code == 409
    # ...and the job can be claimed again
    again = dee_agent.claim()
    assert len(again) == 1 and again[0]["id"] == j["id"]


def test_a_job_that_keeps_expiring_fails_after_three_attempts(world, dee_agent):
    _, sid = _shop(world.dee)
    _convert(world.dee, sid)
    for _ in range(3):
        assert len(dee_agent.claim()) == 1
        world.main.agents  # noqa: B018
        world.db.expire_agent_jobs(now=time.time() + 3600)
        world.main.agents.expire_and_fail()
    assert dee_agent.claim() == []
    # the shop row is failed by expire_and_fail on the last round
    with world.db._conn() as conn:
        conn.execute("UPDATE agent_jobs SET state='claimed', agent_id=?, lease_until=?, attempts=3", (dee_agent.id, time.time() - 5))
        conn.execute("UPDATE agent_jobs SET state='queued', agent_id=NULL WHERE 0")
    world.main.agents.expire_and_fail()
    st = world.dee.get(f"/api/v2/shops/{sid}/status").json()
    assert st["status"] == "failed" and "ran out of retries" in st["error"]


def test_progress_keeps_the_lease_alive(world, dee_agent):
    _, sid = _shop(world.dee)
    _convert(world.dee, sid)
    j = dee_agent.claim()[0]
    with world.db._conn() as conn:
        conn.execute("UPDATE agent_jobs SET lease_until = ?", (time.time() + 1,))
    dee_agent.progress(j["id"], "open")
    job = world.db.get_agent_job(j["id"])
    assert job["lease_until"] > time.time() + 60 and job["state"] == "running" and job["step"] == "open"


def test_unknown_agents_and_unknown_jobs_are_refused(world, dee_agent):
    anon = TestClient(world.main.app)
    assert anon.post("/api/agent/jobs/claim", json={}).status_code == 401
    assert anon.post("/api/agent/jobs/claim", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert dee_agent.master("nope").status_code == 404
    assert dee_agent.progress("nope", "x").status_code == 404


def test_another_agent_cannot_touch_a_job_it_does_not_hold(world, dee_agent):
    h2, id2 = _pair(world.dee, "OTHER")
    other = Agent(world.dee, h2, id2)
    _, sid = _shop(world.dee)
    _convert(world.dee, sid)
    j = dee_agent.claim()[0]
    assert other.progress(j["id"], "pdf").status_code == 409
    assert other.upload(j["id"], "x.cdr", b"x").status_code == 409
    assert other.master(j["id"]).status_code == 409
    assert other.result(j["id"], status="done").status_code == 409


def test_upload_names_are_checked(world, dee_agent):
    _, sid = _shop(world.dee)
    _convert(world.dee, sid)
    j = dee_agent.claim()[0]
    for bad in (".hidden", "a:b.cdr", "a*.cdr"):
        assert dee_agent.upload(j["id"], bad, b"x").status_code == 400
    traversal = dee_agent.c.put(f"/api/agent/jobs/{j['id']}/files/..%2F..%2Fescape.txt", content=b"x", headers=dee_agent.h)
    assert traversal.status_code in (400, 404, 405)                 # never routed to the upload handler
    assert not list(world.main.JOBS_V2.rglob("escape.txt")) and not (world.main.DATA / "escape.txt").exists()


def test_done_needs_every_named_file_to_have_been_uploaded(world, dee_agent):
    _, sid = _shop(world.dee)
    _convert(world.dee, sid)
    j = dee_agent.claim()[0]
    r = dee_agent.result(j["id"], status="done", files={"cdr": "never-uploaded.cdr"}, report={})
    assert r.status_code == 422
    assert world.dee.get(f"/api/v2/shops/{sid}/status").json()["status"] == "converting"      # still held; the agent may retry


def test_oversize_uploads_are_refused_and_leave_no_partial_file(world, dee_agent, monkeypatch):
    _, sid = _shop(world.dee)
    _convert(world.dee, sid)
    j = dee_agent.claim()[0]
    monkeypatch.setenv("SIGNAGE_MAX_UPLOAD_MB", "1")
    assert dee_agent.upload(j["id"], "big.cdr", b"x" * (2 * 1024 * 1024)).status_code == 413
    out = world.main.JOBS_V2
    assert not list(out.rglob("big.cdr*"))


def test_a_waiting_shop_says_when_no_agent_is_online(world, dee_agent):
    _, sid = _shop(world.dee)
    _convert(world.dee, sid)
    assert world.dee.get(f"/api/v2/shops/{sid}/status").json()["waiting_for_agent"] is False       # the agent just paired: online
    with world.db._conn() as conn:
        conn.execute("UPDATE agents SET last_seen = ?", (time.time() - 3600,))
    assert world.dee.get(f"/api/v2/shops/{sid}/status").json()["waiting_for_agent"] is True


def test_the_queue_survives_a_server_restart(world, dee_agent):
    _, sid = _shop(world.dee)
    _convert(world.dee, sid)
    world.main._recover_interrupted_work()                           # what the server does at start-up
    assert world.dee.get(f"/api/v2/shops/{sid}/status").json()["status"] == "queued"
    assert len(dee_agent.claim()) == 1


def test_a_local_shop_is_still_failed_by_recovery_when_not_remote(world, monkeypatch):
    _, sid = _shop(world.dee)
    world.db.set_shop_status(sid, "queued")
    monkeypatch.setenv("SIGNAGE_ENGINE", "mock")
    world.main._recover_interrupted_work()
    assert world.dee.get(f"/api/v2/shops/{sid}/status").json()["status"] == "failed"


def test_the_editor_and_exports_say_they_are_not_available_through_the_agent_yet(world):
    job, sid = _shop(world.boss)
    assert world.boss.get(f"/api/editor/{job}/{sid}/scene").status_code == 501
    assert world.boss.post(f"/api/editor/{job}/{sid}/publish").status_code == 501


def test_remote_mode_is_reported_by_health(world):
    assert world.boss.get("/api/corel/health").json()["ok"] is True
    body = TestClient(world.main.app).get("/healthz").json()
    assert body["engine"] == "remote" and body["mock_engine"] is False
    assert world.boss.get("/api/agent/config").json()["agent_required"] is True


def test_convert_while_already_queued_is_refused(world, dee_agent):
    _, sid = _shop(world.dee)
    assert _convert(world.dee, sid).status_code == 200
    assert _convert(world.dee, sid).status_code == 409


def test_database_uses_write_ahead_logging(world):
    with world.db._conn() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
