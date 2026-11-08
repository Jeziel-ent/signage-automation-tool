"""The agent doing real work for the real website (uvicorn in a thread, SIGNAGE_ENGINE=remote): pairing, a convert queued by the site, claimed
by the agent, run (mock engine or an injected batch runner), uploaded, and shown as done - plus the failure and retry paths."""
from __future__ import annotations

import importlib
import io
import json
import socket
import threading
import time
import zipfile
from pathlib import Path

import httpx
import pytest

from signage_agent import client, engine_bridge
from signage_agent.agent import Agent
from signage_agent.config import Config
from signage_agent.jobs import JobRunner


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
    monkeypatch.delenv("SIGNAGE_PUBLIC_BASE_URL", raising=False)
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
    web = httpx.Client(base_url=base, timeout=30)
    assert web.post("/api/auth/setup", json={"email": "boss@example.com", "password": "correct-horse-1"}).status_code == 200

    class W:
        pass

    w = W()
    w.url, w.web, w.db, w.main, w.tmp = base, web, db_module, main_module, tmp_path
    yield w
    web.close()
    server.should_exit = True
    t.join(timeout=5)


def _make_agent(world, name="agent_home") -> Agent:
    home = world.tmp / name
    home.mkdir(exist_ok=True)
    (home / "config.json").write_text(json.dumps({"server_url": world.url, "allowed_origins": [world.url]}))
    agent = Agent(Config(home))
    code = world.web.post("/api/agent/pairing-code").json()["code"]
    agent.pair(code)
    return agent


def _shop(world, name="NR Traders", master_bytes=None):
    job = world.web.post("/api/v2/upload", data={"brand": "dalmia"}, files={"master": ("m.cdr", master_bytes or _cdr(), "application/octet-stream")}).json()["id"]
    sid = world.web.post(f"/api/v2/jobs/{job}/shops", json={"name": name, "width": 12, "width_unit": "ft", "height": 4, "height_unit": "ft"}).json()["id"]
    return job, sid


def _status(world, sid):
    return world.web.get(f"/api/v2/shops/{sid}/status").json()


# ------------------------------------------------------------------ the whole path, mock engine

def test_agent_converts_a_shop_end_to_end(world):
    agent = _make_agent(world)
    runner = JobRunner(agent, mock=True)
    _, sid = _shop(world)
    assert world.web.post(f"/api/v2/shops/{sid}/convert").json()["queued_for_agent"] is True
    assert runner.poll_once() == 1
    st = _status(world, sid)
    assert st["status"] == "done" and st["progress_pct"] == 100 and st["files"]
    for kind, path in st["files"].items():
        name = Path(path).name
        r = world.web.get(f"/api/v2/shops/{sid}/files/{name}")
        assert r.status_code == 200 and len(r.content) > 0, kind
    assert agent.state == "idle"


def test_nothing_is_claimed_when_nothing_is_queued_or_the_agent_is_unpaired(world, tmp_path):
    agent = _make_agent(world)
    assert JobRunner(agent, mock=True).poll_once() == 0
    lone = Agent(Config(tmp_path / "unpaired"))
    assert JobRunner(lone, mock=True).poll_once() == 0


def test_a_pc_without_coreldraw_does_not_take_jobs(world, monkeypatch):
    agent = _make_agent(world)
    monkeypatch.setattr(agent, "corel_installs", lambda: [])
    runner = JobRunner(agent, mock=False)
    _, sid = _shop(world)
    world.web.post(f"/api/v2/shops/{sid}/convert")
    assert runner.can_work() is False and runner.poll_once() == 0
    assert _status(world, sid)["status"] == "queued"                      # still waiting for a PC that can do it


def test_a_busy_agent_does_not_claim_more(world):
    agent = _make_agent(world)
    agent.state = "busy"
    _, sid = _shop(world)
    world.web.post(f"/api/v2/shops/{sid}/convert")
    assert JobRunner(agent, mock=True).poll_once() == 0


def test_a_failing_conversion_reaches_the_shop_as_a_failure(world):
    agent = _make_agent(world)

    def boom(job):
        raise RuntimeError("CorelDRAW exploded")
    _, sid = _shop(world)
    world.web.post(f"/api/v2/shops/{sid}/convert")
    JobRunner(agent, mock=True, run_mock=boom).poll_once()
    st = _status(world, sid)
    assert st["status"] == "failed" and "CorelDRAW exploded" in st["error"]


# ------------------------------------------------------------------ the real-engine path with an injected batch runner

def _fake_batch(calls=None, fail_index=None, fail_once=False):
    """Stands in for corel_supervisor.run_batch: writes the output files like a real conversion and answers one entry per job."""
    seen = {"retried": False}

    def run(jobs, results_path, on_progress=None):
        if calls is not None:
            calls.append([j["shop"]["name"] for j in jobs])
        out = []
        for i, job in enumerate(jobs):
            if fail_index == i and not (fail_once and seen["retried"]):
                if len(jobs) > 1 or fail_once is False:
                    pass
                if fail_once and len(jobs) > 1:
                    out.append({"status": "error", "error": "reused instance died"})
                    continue
                if not fail_once:
                    out.append({"status": "error", "error": "hard failure"})
                    continue
            if len(jobs) == 1 and fail_once:
                seen["retried"] = True
            d = Path(job["out_dir"])
            d.mkdir(parents=True, exist_ok=True)
            (d / "board.cdr").write_bytes(b"CDR" + job["shop"]["name"].encode())
            (d / "board.pdf").write_bytes(b"%PDF")
            (d / "preview.png").write_bytes(b"PNG")
            out.append({"status": "done", "result": {"files": {"cdr": str(d / "board.cdr"), "pdf": str(d / "board.pdf"),
                                                               "preview": str(d / "preview.png")},
                                                     "report": {"warnings": [], "layout": {"mode": "rules"}}}})
        return out
    return run


def test_a_batch_runs_in_one_go_and_each_shop_gets_its_own_files(world):
    agent = _make_agent(world)
    calls: list = []
    runner = JobRunner(agent, run_batch=_fake_batch(calls), mock=False)
    monkey_installs = [{"progid": "CorelDRAW.Application.27", "major": 27}]
    agent.corel_installs = lambda: monkey_installs
    ids = []
    for n in ("Alpha", "Beta", "Gamma"):
        _, sid = _shop(world, n)
        world.web.post(f"/api/v2/shops/{sid}/convert")
        ids.append(sid)
    assert runner.poll_once() == 3
    assert calls == [["Alpha", "Beta", "Gamma"]]                          # one worker, one CorelDRAW for the batch
    for n, sid in zip(("Alpha", "Beta", "Gamma"), ids):
        st = _status(world, sid)
        assert st["status"] == "done"
        cdr = [Path(p).name for k, p in st["files"].items() if k == "cdr"][0]
        assert world.web.get(f"/api/v2/shops/{sid}/files/{cdr}").content == b"CDR" + n.encode()


def test_a_failed_batch_member_is_retried_alone(world):
    agent = _make_agent(world)
    calls: list = []
    agent.corel_installs = lambda: [{"progid": "CorelDRAW.Application.27", "major": 27}]
    runner = JobRunner(agent, run_batch=_fake_batch(calls, fail_index=1, fail_once=True), mock=False)
    ids = []
    for n in ("One", "Two"):
        _, sid = _shop(world, n)
        world.web.post(f"/api/v2/shops/{sid}/convert")
        ids.append(sid)
    assert runner.poll_once() == 2
    assert calls == [["One", "Two"], ["Two"]]                             # Two failed in the batch, then ran alone
    assert [_status(world, s)["status"] for s in ids] == ["done", "done"]


def test_a_member_that_fails_for_real_fails_only_that_shop(world):
    agent = _make_agent(world)
    agent.corel_installs = lambda: [{"progid": "CorelDRAW.Application.27", "major": 27}]
    runner = JobRunner(agent, run_batch=_fake_batch(fail_index=0), mock=False)
    _, sid = _shop(world, "Solo")
    world.web.post(f"/api/v2/shops/{sid}/convert")
    runner.poll_once()
    st = _status(world, sid)
    assert st["status"] == "failed" and "hard failure" in st["error"]


def test_the_refusal_to_start_fails_every_job_with_the_reason(world):
    agent = _make_agent(world)
    agent.corel_installs = lambda: [{"progid": "CorelDRAW.Application.27", "major": 27}]

    def refuse(jobs, results_path, on_progress=None):
        raise RuntimeError("refusing to start CorelDRAW batch: only 0.9 GB free RAM")
    runner = JobRunner(agent, run_batch=refuse, mock=False)
    _, sid = _shop(world)
    world.web.post(f"/api/v2/shops/{sid}/convert")
    runner.poll_once()
    assert "free RAM" in _status(world, sid)["error"]


# ------------------------------------------------------------------ masters: cache, failures

def test_the_master_is_downloaded_once_and_reused(world, monkeypatch):
    agent = _make_agent(world)
    downloads = []
    real = client.download_master
    monkeypatch.setattr(client, "download_master", lambda *a, **k: (downloads.append(a[2]), real(*a, **k))[1])
    runner = JobRunner(agent, mock=True)
    job = world.web.post("/api/v2/upload", data={"brand": "dalmia"}, files={"master": ("m.cdr", _cdr(), "application/octet-stream")}).json()["id"]
    for n in ("A", "B"):
        sid = world.web.post(f"/api/v2/jobs/{job}/shops", json={"name": n, "width": 12, "width_unit": "ft", "height": 4, "height_unit": "ft"}).json()["id"]
        world.web.post(f"/api/v2/shops/{sid}/convert")
        runner.poll_once()
    assert len(downloads) == 1                                            # the second shop found it in the cache
    assert len(list((agent.config.home / "cache").iterdir())) == 1


def test_the_cache_keeps_only_the_most_recent_masters(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    for i in range(20):
        p = cache / f"{i}-m.cdr"
        p.write_bytes(b"x")
        import os
        os.utime(p, (1000 + i, 1000 + i))
    JobRunner._prune_cache(cache)
    names = sorted(p.name for p in cache.iterdir())
    assert len(names) == 15 and "0-m.cdr" not in names and "19-m.cdr" in names


def test_a_master_that_cannot_be_downloaded_fails_that_job(world, monkeypatch):
    agent = _make_agent(world)

    def broken(*a, **k):
        raise client.ApiError(0, "cannot reach the server")
    monkeypatch.setattr(client, "download_master", broken)
    _, sid = _shop(world)
    world.web.post(f"/api/v2/shops/{sid}/convert")
    JobRunner(agent, mock=True).poll_once()
    st = _status(world, sid)
    assert st["status"] == "failed" and "could not download the master" in st["error"]


def test_a_partial_master_download_is_discarded(world, tmp_path):
    agent = _make_agent(world)
    _, sid = _shop(world)
    world.web.post(f"/api/v2/shops/{sid}/convert")
    cfg = agent.config
    job = client.claim_jobs(cfg.server_url, cfg.token)[0]
    dest = tmp_path / "m.cdr"
    with pytest.raises(client.ApiError):
        client.download_master(cfg.server_url, cfg.token, job["id"], dest, job["master"]["size"] + 5)
    assert not dest.exists() and not (tmp_path / "m.cdr.part").exists()


# ------------------------------------------------------------------ uploads

def test_an_upload_the_site_refuses_fails_the_job_with_the_reason(world, monkeypatch):
    agent = _make_agent(world)
    monkeypatch.setenv("SIGNAGE_MAX_UPLOAD_MB", "1")
    monkeypatch.setattr("signage_agent.jobs.UPLOAD_TRIES", 1)

    def big(job):
        d = Path(job["out_dir"])
        d.mkdir(parents=True, exist_ok=True)
        (d / "board.cdr").write_bytes(b"x" * (2 * 1024 * 1024))
        return {"files": {"cdr": str(d / "board.cdr")}, "report": {}}
    _, sid = _shop(world)
    world.web.post(f"/api/v2/shops/{sid}/convert")
    JobRunner(agent, mock=True, run_mock=big).poll_once()
    st = _status(world, sid)
    assert st["status"] == "failed" and "could not upload" in st["error"]


def test_work_folder_is_removed_after_a_round(world):
    agent = _make_agent(world)
    _, sid = _shop(world)
    world.web.post(f"/api/v2/shops/{sid}/convert")
    JobRunner(agent, mock=True).poll_once()
    work = agent.config.home / "work"
    assert not work.exists() or not list(work.iterdir())


def test_the_agent_status_reports_busy_while_working(world):
    agent = _make_agent(world)
    seen = []

    def spy(job):
        seen.append(agent.status()["state"])
        return engine_bridge.run_mock(job)
    _, sid = _shop(world)
    world.web.post(f"/api/v2/shops/{sid}/convert")
    JobRunner(agent, mock=True, run_mock=spy).poll_once()
    assert seen == ["busy"] and agent.status()["state"] == "idle"


def test_the_batch_runs_with_the_newest_version_that_saved_one_of_its_masters(world, monkeypatch):
    import os
    agent = _make_agent(world)
    agent.corel_installs = lambda: [{"progid": "CorelDRAW.Application.27", "major": 27}]
    seen = []
    inner = _fake_batch()

    def spy(jobs, results_path, on_progress=None):
        seen.append(os.environ.get("SIGNAGE_COREL_MIN_MAJOR"))
        return inner(jobs, results_path, on_progress)
    monkeypatch.delenv("SIGNAGE_COREL_MIN_MAJOR", raising=False)
    runner = JobRunner(agent, run_batch=spy, mock=False)
    for core in ("2100", "2510"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("META-INF/metadata.xml", f"<m><CoreVersion>{core}</CoreVersion></m>")
        _, sid = _shop(world, f"Board {core}", master_bytes=buf.getvalue())
        world.web.post(f"/api/v2/shops/{sid}/convert")
    assert runner.poll_once() == 2
    assert seen == ["25"]                                               # one CorelDRAW for both: it must open the newer master
    assert "SIGNAGE_COREL_MIN_MAJOR" not in os.environ                  # and nothing is left behind for the next batch
