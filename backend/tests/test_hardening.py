"""Hardening: rate limits, the audit log, alerts + error reporting, and the disk clean-up (app/ratelimit.py, audit.py, alerts.py, retention.py,
admin.py)."""
from __future__ import annotations

import importlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_agent_queue import Agent, _pair, _shop, world  # noqa: F401  (world is a fixture)

ADMIN = {"email": "boss@example.com", "password": "correct-horse-1"}


# ------------------------------------------------------------------ a webhook that records what it is sent

@pytest.fixture
def webhook(monkeypatch):
    got: list[dict] = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("SIGNAGE_ALERT_WEBHOOK", f"http://127.0.0.1:{srv.server_port}/hook")
    yield got
    srv.shutdown()


def _wait(cond, secs=3.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ================================================================== rate limits

def test_a_caller_over_the_limit_gets_429_with_retry_after(world, monkeypatch):
    monkeypatch.setenv("SIGNAGE_RATE_LIMIT_PER_MIN", "5")
    codes = [world.dee.get("/api/masters").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
    r = world.dee.get("/api/masters")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1 and "too many requests" in r.json()["detail"]


def test_each_caller_has_its_own_allowance(world, monkeypatch):
    monkeypatch.setenv("SIGNAGE_RATE_LIMIT_PER_MIN", "3")
    for _ in range(3):
        assert world.dee.get("/api/masters").status_code == 200
    assert world.dee.get("/api/masters").status_code == 429
    assert world.boss.get("/api/masters").status_code == 200              # another user's cookie: untouched


def test_health_checks_and_pages_are_never_limited(world, monkeypatch):
    monkeypatch.setenv("SIGNAGE_RATE_LIMIT_PER_MIN", "1")
    anon = TestClient(world.main.app)
    assert all(anon.get("/healthz").status_code == 200 for _ in range(10))
    assert all(anon.get("/api/health").status_code == 200 for _ in range(10))
    assert all(anon.get("/some-page").status_code != 429 for _ in range(10))


def test_agents_count_separately_and_a_limit_of_zero_means_off(world, monkeypatch):
    h, aid = _pair(world.dee, "PC")
    a = Agent(world.dee, h, aid)
    monkeypatch.setenv("SIGNAGE_RATE_LIMIT_PER_MIN", "1")                 # the browser bucket is tiny ...
    monkeypatch.setenv("SIGNAGE_RATE_LIMIT_AGENT_PER_MIN", "50")
    assert all(a.claim() == [] for _ in range(10))                        # ... the agent has its own
    monkeypatch.setenv("SIGNAGE_RATE_LIMIT_PER_MIN", "0")
    assert all(world.dee.get("/api/masters").status_code == 200 for _ in range(20))


def test_uploads_and_pairing_have_stricter_buckets(world, monkeypatch):
    monkeypatch.setenv("SIGNAGE_RATE_LIMIT_UPLOADS_PER_MIN", "2")
    files = {"master": ("m.cdr", b"x", "application/octet-stream")}
    codes = [world.boss.post("/api/masters/upload", files=files, data={"brand": "b", "orientation": "landscape"}).status_code for _ in range(4)]
    assert codes[:2] == [200, 200] and codes[2:] == [429, 429]
    monkeypatch.setenv("SIGNAGE_RATE_LIMIT_PAIRING_PER_MIN", "2")
    codes = [world.dee.post("/api/agent/pairing-code").status_code for _ in range(4)]
    assert codes[:2] == [200, 200] and codes[2:] == [429, 429]


def test_the_window_slides():
    from app import ratelimit
    os.environ["SIGNAGE_RATE_LIMIT_PER_MIN"] = "2"
    try:
        assert ratelimit.check("k", "api", now=100.0) == (True, 0)
        assert ratelimit.check("k", "api", now=110.0) == (True, 0)
        ok, wait = ratelimit.check("k", "api", now=120.0)
        assert ok is False and 35 <= wait <= 45                          # the first request leaves the window at t = 160
        assert ratelimit.check("k", "api", now=161.0)[0] is True
    finally:
        del os.environ["SIGNAGE_RATE_LIMIT_PER_MIN"]
        ratelimit.reset()


def test_a_garbage_setting_falls_back_to_the_default(monkeypatch):
    from app import ratelimit
    monkeypatch.setenv("SIGNAGE_RATE_LIMIT_PER_MIN", "lots")
    assert ratelimit.limit_for("api") == ratelimit.DEFAULTS["api"]


# ================================================================== audit log

def _entries(client, **params):
    return client.get("/api/admin/audit", params=params).json()


def test_sign_ins_and_sign_outs_are_logged_without_secrets(world):
    anon = TestClient(world.main.app)
    anon.post("/api/auth/login", json={"email": "dee@example.com", "password": "WRONG-password-1"})
    anon.post("/api/auth/login", json={"email": "dee@example.com", "password": "designer-pass-1"})
    anon.post("/api/auth/logout")
    got = _entries(world.boss)
    actions = [e["action"] for e in got["entries"]]
    assert {"login_failed", "login", "logout"} <= set(actions)
    blob = json.dumps(got)
    assert "designer-pass-1" not in blob and "WRONG-password-1" not in blob and "password_hash" not in blob
    login = next(e for e in got["entries"] if e["action"] == "login")
    assert login["user_email"] == "dee@example.com" and login["ip"]


def test_only_an_administrator_reads_the_log(world):
    assert world.dee.get("/api/admin/audit").status_code == 403
    assert world.dee.get("/api/admin/status").status_code == 403
    assert world.dee.get("/api/admin/storage").status_code == 403
    assert world.dee.post("/api/admin/cleanup", json={}).status_code == 403
    assert TestClient(world.main.app).get("/api/admin/audit").status_code == 401


def test_account_changes_are_logged_with_what_changed_not_the_password(world):
    uid = next(u["id"] for u in world.boss.get("/api/users").json()["users"] if u["email"] == "dee@example.com")
    world.boss.patch(f"/api/users/{uid}", json={"password": "brand-new-pass-1", "role": "admin"})
    world.boss.post("/api/users", json={"email": "new@example.com", "password": "another-pass-1", "role": "designer"})
    upd = next(e for e in _entries(world.boss, action="user_updated")["entries"])
    assert upd["target"] == "dee@example.com" and set(upd["detail"]["changed"]) == {"password", "role"} and upd["detail"]["role"] == "admin"
    assert "brand-new-pass-1" not in json.dumps(_entries(world.boss))
    made = _entries(world.boss, action="user_created")["entries"][0]
    assert made["target"] == "new@example.com" and made["user_email"] == "boss@example.com"


def test_master_changes_corrections_agents_and_shops_are_logged(world):
    files = {"master": ("m.cdr", b"x", "application/octet-stream")}
    m = world.boss.post("/api/masters/upload", files=files, data={"brand": "Hangyo", "orientation": "landscape"}).json()
    world.boss.patch(f"/api/masters/{m['id']}", json={"name": "Renamed"})
    world.boss.put(f"/api/masters/{m['id']}/file", files={"master": ("m2.cdr", b"yy", "application/octet-stream")})
    world.boss.delete(f"/api/masters/{m['id']}")
    h, aid = _pair(world.dee, "PC-X")
    world.dee.delete(f"/api/agents/{aid}")
    _, sid = _shop(world.dee, "Gone Store")
    world.dee.delete(f"/api/v2/shops/{sid}")
    got = {e["action"]: e for e in _entries(world.boss, limit=200)["entries"]}
    for action in ("master_uploaded", "master_edited", "master_file_replaced", "master_deleted", "agent_paired", "agent_revoked", "shop_deleted"):
        assert action in got, action
    assert got["master_edited"]["detail"]["fields"] == ["master_name"] and got["master_file_replaced"]["detail"]["was"] == "m.cdr"
    assert got["agent_paired"]["user_email"] == "dee@example.com" and "PC-X" in got["agent_paired"]["target"]
    assert got["shop_deleted"]["user_email"] == "dee@example.com"


def test_the_log_can_be_filtered_searched_and_paged(world):
    for i in range(5):
        world.boss.post("/api/users", json={"email": f"u{i}@example.com", "password": "another-pass-1", "role": "designer"})
    page1 = _entries(world.boss, action="user_created", limit=2)
    page2 = _entries(world.boss, action="user_created", limit=2, offset=2)
    assert page1["total"] == 6 and len(page1["entries"]) == 2          # the five above + the designer the fixture created and page1["entries"][0]["id"] != page2["entries"][0]["id"]
    assert page1["entries"][0]["at"] >= page1["entries"][1]["at"]                 # newest first
    assert [e["target"] for e in _entries(world.boss, q="u3@example")["entries"]] == ["u3@example.com"]
    assert "user_created" in page1["actions"] and "login" in page1["actions"]


def test_a_failing_log_never_breaks_the_action(world, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(world.db, "add_audit", boom)
    assert world.boss.post("/api/users", json={"email": "x@example.com", "password": "another-pass-1", "role": "designer"}).status_code == 200


def test_old_audit_entries_are_purged(world):
    world.db.add_audit(None, "old@example.com", "ancient", "", {}, "")
    with world.db._conn() as conn:
        conn.execute("UPDATE audit_log SET at = ? WHERE action = 'ancient'", (time.time() - 400 * 86400,))
    assert world.db.purge_audit(365 * 86400) == 1
    assert _entries(world.boss, action="ancient")["total"] == 0


# ================================================================== alerts

def test_alerts_are_sent_once_per_cooldown(webhook):
    from app import alerts
    assert alerts.notify("k", "Disk full", "1 GB left", wait=True) is True
    assert alerts.notify("k", "Disk full", "1 GB left", wait=True) is False       # inside the cooldown
    assert alerts.notify("other", "Something else", wait=True) is True
    assert [m["title"] for m in webhook] == ["Disk full", "Something else"]
    assert webhook[0]["text"] == "[Signage] Disk full\n1 GB left" and webhook[0]["detail"] == "1 GB left"


def test_nothing_is_sent_without_a_webhook(monkeypatch):
    from app import alerts
    monkeypatch.delenv("SIGNAGE_ALERT_WEBHOOK", raising=False)
    assert alerts.notify("k", "x", wait=True) is False


def test_a_dead_webhook_does_not_raise(monkeypatch):
    from app import alerts
    monkeypatch.setenv("SIGNAGE_ALERT_WEBHOOK", "http://127.0.0.1:9/nothing")
    assert alerts.notify("k", "x", wait=True) is True                             # tried, failed quietly


def test_a_full_disk_is_a_problem_and_recovery_is_announced(world, webhook, monkeypatch, tmp_path):
    from app import alerts
    monkeypatch.setenv("SIGNAGE_MIN_FREE_DISK_GB", "99999999")
    found = alerts.check_and_notify(tmp_path, wait=True)
    assert [p["key"] for p in found] == ["disk"] and webhook[-1]["title"] == "The server disk is almost full"
    alerts.check_and_notify(tmp_path, wait=True)
    assert len(webhook) == 1                                                       # not repeated
    monkeypatch.setenv("SIGNAGE_MIN_FREE_DISK_GB", "0")
    assert alerts.check_and_notify(tmp_path, wait=True) == []
    assert webhook[-1]["title"] == "Recovered: The server disk is almost full"


def test_boards_waiting_too_long_for_an_offline_agent_are_a_problem(world, tmp_path):
    from app import alerts
    h, aid = _pair(world.dee, "PC")
    _, sid = _shop(world.dee)
    world.dee.post(f"/api/v2/shops/{sid}/convert")
    assert not [p for p in alerts.problems(tmp_path) if p["key"].startswith("waiting")]        # just queued
    with world.db._conn() as conn:
        conn.execute("UPDATE agent_jobs SET created_at = ?", (time.time() - 3600,))
        conn.execute("UPDATE agents SET last_seen = ?", (time.time() - 3600,))
    waiting = [p for p in alerts.problems(tmp_path) if p["key"].startswith("waiting")]
    assert len(waiting) == 1 and "dee@example.com" in waiting[0]["detail"]
    with world.db._conn() as conn:
        conn.execute("UPDATE agents SET last_seen = ?", (time.time(),))                          # the PC comes back
    assert not [p for p in alerts.problems(tmp_path) if p["key"].startswith("waiting")]


def test_the_server_page_shows_the_problems_without_a_webhook(world, monkeypatch):
    monkeypatch.setenv("SIGNAGE_MIN_FREE_DISK_GB", "99999999")
    st = world.boss.get("/api/admin/status").json()
    assert st["webhook_configured"] is False and [p["key"] for p in st["problems"]] == ["disk"] and st["engine"] == "remote"


def test_an_unexpected_error_is_logged_answered_as_500_and_reported(world, webhook):
    from fastapi.routing import APIRoute

    def boom():
        raise RuntimeError("kaput")
    world.main.app.router.routes.insert(0, APIRoute("/api/_boom", boom, methods=["GET"]))     # in front of the page catch-all
    c = TestClient(world.main.app, raise_server_exceptions=False)
    assert c.post("/api/auth/login", json=ADMIN).status_code == 200
    r = c.get("/api/_boom")
    assert r.status_code == 500 and r.json() == {"detail": "internal server error"}
    assert _wait(lambda: any(m["title"] == "Server error" for m in webhook))
    assert "RuntimeError: kaput" in next(m for m in webhook if m["title"] == "Server error")["detail"]


# ================================================================== clean-up

def _tree(data: Path):
    """A data folder with one board: master, outputs, a scene, two exports, product images, plus assorted leftovers."""
    out = data / "jobs_v2" / "job1" / "out" / "shop1"
    (data / "jobs_v2" / "job1").mkdir(parents=True)
    (data / "jobs_v2" / "job1" / "master.cdr").write_bytes(b"M" * 1000)
    (out / "scene" / "img").mkdir(parents=True)
    (out / "scene" / "scene.json").write_text("{}")
    (out / "scene" / "img" / "a.svg").write_bytes(b"S" * 300)
    for eid, size in (("expOLD", 500), ("expNEW", 400), ("expUNFINISHED", 200)):
        d = out / "exports" / eid
        d.mkdir(parents=True)
        (d / "board.png").write_bytes(b"E" * size)
    (out / "board.cdr").write_bytes(b"B" * 2000)
    (out / "board.pdf").write_bytes(b"P" * 700)
    (out / "product_assets").mkdir()
    (out / "product_assets" / "mine.png").write_bytes(b"I" * 100)
    (out / "half.cdr.part").write_bytes(b"x" * 50)
    (data / "convert_runs").mkdir()
    (data / "convert_runs" / "old.heartbeat").write_text("{}")
    (data / "shared_zips" / "abc").mkdir(parents=True)
    (data / "shared_zips" / "abc" / "z.zip").write_bytes(b"Z" * 60)
    (data / "agent_fonts" / "_incoming" / "j1").mkdir(parents=True)
    (data / "agent_fonts" / "k.ttf").write_bytes(b"F" * 80)
    (data / "wetransfer_debug").mkdir()
    (data / "wetransfer_debug" / "shot.png").write_bytes(b"D" * 30)
    (data / "signage.db").write_bytes(b"DB" * 10)
    return out


def _age(path: Path, days: float, now: float):
    t = now - days * 86400
    os.utime(path, (t, t))
    for p in path.rglob("*") if path.is_dir() else []:
        os.utime(p, (t, t))


def test_the_report_counts_bytes_by_category(tmp_path):
    from app import retention
    _tree(tmp_path)
    r = retention.storage_report(tmp_path)
    assert r["masters"] == 1000 and r["boards"] == 2000 + 700 + 50 and r["scenes"] == 302 and r["exports"] == 1100
    assert r["product_assets"] == 100 and r["fonts"] == 80 and r["shared"] == 60 and r["database"] == 20 and r["runs"] > 0


def test_only_leftovers_are_planned_without_a_retention_period(tmp_path):
    from app import retention
    out = _tree(tmp_path)
    now = time.time()
    for p in (out / "half.cdr.part", tmp_path / "convert_runs" / "old.heartbeat", tmp_path / "shared_zips" / "abc",
              tmp_path / "agent_fonts" / "_incoming" / "j1", tmp_path / "wetransfer_debug" / "shot.png"):
        _age(p, 30, now)
    _age(out / "exports" / "expOLD", 400, now)                         # old, but no period given: kept
    items = retention.plan(tmp_path, now=now)
    kinds = {Path(i["path"]).name for i in items}
    assert kinds == {"half.cdr.part", "old.heartbeat", "abc", "j1", "shot.png"}
    assert {i["category"] for i in items} == {"temporary"}


def test_fresh_leftovers_are_left_alone(tmp_path):
    from app import retention
    _tree(tmp_path)
    assert retention.plan(tmp_path, now=time.time()) == []


def test_old_exports_go_except_the_newest_finished_one_and_scenes_rebuild_later(tmp_path):
    from app import retention
    out = _tree(tmp_path)
    now = time.time()
    for name in ("expOLD", "expNEW", "expUNFINISHED"):
        _age(out / "exports" / name, 200, now)
    _age(out / "scene", 200, now)
    items = retention.plan(tmp_path, now=now, days=90, protected_exports={"expNEW"})
    got = {(i["category"], Path(i["path"]).name) for i in items}
    assert got == {("exports", "expOLD"), ("exports", "expUNFINISHED"), ("scenes", "scene")}
    assert not any("expNEW" in i["path"] for i in items)
    young = retention.plan(tmp_path, now=now, days=300, protected_exports={"expNEW"})
    assert young == []


def test_masters_boards_database_and_product_images_are_never_planned(tmp_path):
    from app import retention
    out = _tree(tmp_path)
    now = time.time()
    for p in tmp_path.rglob("*"):
        os.utime(p, (now - 3000 * 86400, now - 3000 * 86400))
    paths = {Path(i["path"]).name for i in retention.plan(tmp_path, now=now, days=1)}
    assert not paths & {"master.cdr", "board.cdr", "board.pdf", "signage.db", "mine.png", "k.ttf"}
    assert (out / "board.cdr").exists()


def test_run_deletes_only_inside_the_data_folder(tmp_path):
    from app import retention
    data = tmp_path / "data"
    data.mkdir()
    victim = data / "convert_runs"
    victim.mkdir()
    (victim / "x").write_text("1")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me")
    items = [{"category": "temporary", "path": str(victim / "x"), "bytes": 1}, {"category": "temporary", "path": str(outside), "bytes": 7}]
    summary = retention.run(items, data)
    assert summary["deleted"] == 1 and not (victim / "x").exists() and outside.read_text() == "keep me"


def test_the_cleanup_api_previews_then_deletes_and_forgets_the_export_rows(world):
    data = world.db.DB_PATH.parent
    job, sid = _shop(world.dee)
    out = world.main.JOBS_V2 / job / "out" / sid
    now = time.time()
    world.db.create_export("expOLD", sid, ["png"], {}, [], [])
    world.db.create_export("expNEW", sid, ["png"], {}, [], [])
    for eid in ("expOLD", "expNEW"):
        d = out / "exports" / eid
        d.mkdir(parents=True)
        (d / "b.png").write_bytes(b"E" * 500)
        world.db.set_export_result(eid, {"png": "b.png"}, {})
        _age(d, 200, now)
    with world.db._conn() as conn:
        conn.execute("UPDATE exports SET created_at = ? WHERE id = 'expOLD'", (now - 100,))
    dry = world.boss.post("/api/admin/cleanup", json={"days": 90, "dry_run": True}).json()
    assert dry["dry_run"] is True and dry["would_delete"] == 1 and dry["by_category"]["exports"]["bytes"] == 500
    assert (out / "exports" / "expOLD").exists()
    done = world.boss.post("/api/admin/cleanup", json={"days": 90, "dry_run": False}).json()
    assert done["deleted"] == 1 and done["bytes"] == 500
    assert not (out / "exports" / "expOLD").exists() and (out / "exports" / "expNEW").exists()      # the newest finished export stays
    assert world.db.get_export("expOLD") is None and world.db.get_export("expNEW") is not None
    assert _entries(world.boss, action="cleanup")["total"] == 1


def test_the_storage_report_endpoint_and_the_configured_period(world, monkeypatch):
    monkeypatch.setenv("SIGNAGE_RETENTION_DAYS", "30")
    r = world.boss.get("/api/admin/storage").json()
    assert r["retention_days"] == 30 and r["free_bytes"] > 0 and r["total_bytes"] >= 0 and "masters" in r["bytes"]


def test_housekeeping_runs_the_cleanup_and_trims_the_log(world):
    world.db.add_audit(None, "old@example.com", "ancient", "", {}, "")
    with world.db._conn() as conn:
        conn.execute("UPDATE audit_log SET at = ? WHERE action = 'ancient'", (time.time() - 400 * 86400,))
    runs = world.main.CONVERT_RUNS
    runs.mkdir(parents=True, exist_ok=True)
    stale = runs / "stale.heartbeat"
    stale.write_text("{}")
    os.utime(stale, (time.time() - 5 * 86400,) * 2)
    world.main._housekeeping_once()
    assert not stale.exists() and _entries(world.boss, action="ancient")["total"] == 0
