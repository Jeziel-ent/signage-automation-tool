"""The X buttons: stopping one waiting / converting board, or every board of a Convert All.

Remote mode (the agent does the work): the job becomes `cancelled`, the shop `failed` with "Stopped by the user", the agent's next
progress call gets 410 (that is how it knows to kill CorelDRAW) and nothing it uploads afterwards is accepted.
Local mode: the supervisor kills the worker of a stopped job (should_cancel), and a stopped shop never gets a result stored."""
from __future__ import annotations

import importlib
import platform
import time

import pytest
from fastapi.testclient import TestClient

from tests.test_agent_queue import ADMIN, DEE, Agent, _convert, _pair, _shop

STOPPED = "Stopped by the user"


# ------------------------------------------------------------------ remote mode (agent)

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
    w.main, w.dee, w.db = main_module, dee, db_module
    h, aid = _pair(dee)
    w.agent = Agent(dee, h, aid)
    return w


def _status(world, sid):
    return world.dee.get(f"/api/v2/shops/{sid}/status").json()


def test_a_waiting_board_is_stopped_and_never_given_to_the_agent(world):
    _, sid = _shop(world.dee)
    _convert(world.dee, sid)
    r = world.dee.post(f"/api/v2/shops/{sid}/stop")
    assert r.status_code == 200 and r.json() == {"stopped": True, "status": "failed"}
    st = _status(world, sid)
    assert st["status"] == "failed" and st["error"] == STOPPED
    assert world.agent.claim() == []


def test_a_running_board_tells_the_agent_to_stop_and_refuses_its_results(world):
    _, sid = _shop(world.dee)
    _convert(world.dee, sid)
    j = world.agent.claim()[0]
    assert world.agent.progress(j["id"], "open").status_code == 200
    assert world.dee.post(f"/api/v2/shops/{sid}/stop").json()["stopped"] is True
    r = world.agent.progress(j["id"], "saveas")
    assert r.status_code == 410                                   # the agent kills CorelDRAW on this
    assert world.agent.upload(j["id"], "board.cdr", b"CDR").status_code == 409
    assert world.agent.result(j["id"], status="done", files={}).status_code == 409
    st = _status(world, sid)
    assert st["status"] == "failed" and st["error"] == STOPPED     # the late progress call did not flip it back to converting
    assert world.db.get_agent_job(j["id"])["state"] == "cancelled"


def test_a_stopped_board_can_be_converted_again(world):
    _, sid = _shop(world.dee)
    _convert(world.dee, sid)
    world.dee.post(f"/api/v2/shops/{sid}/stop")
    assert _convert(world.dee, sid).status_code == 200
    jobs = world.agent.claim()
    assert len(jobs) == 1 and _status(world, sid)["status"] == "converting"


def test_stop_all_stops_only_the_waiting_and_converting_boards(world):
    _, a = _shop(world.dee, "A")
    _, b = _shop(world.dee, "B")
    _, c = _shop(world.dee, "C")                                   # never converted: nothing to stop
    _convert(world.dee, a)
    _convert(world.dee, b)
    world.agent.claim(n=1)                                         # A is running, B waits
    r = world.dee.post("/api/v2/shops/stop", json={"ids": [a, b, c, "no-such-shop"]})
    assert r.status_code == 200 and sorted(r.json()["stopped"]) == sorted([a, b])
    assert _status(world, a)["status"] == _status(world, b)["status"] == "failed"
    assert _status(world, c)["status"] == "new"
    assert world.agent.claim() == []


def test_stopping_a_finished_board_changes_nothing(world):
    _, sid = _shop(world.dee)
    _convert(world.dee, sid)
    j = world.agent.claim()[0]
    for name in ("board.cdr", "board.pdf", "preview.png"):
        world.agent.upload(j["id"], name, b"x")
    world.agent.result(j["id"], status="done", files={"cdr": "board.cdr", "pdf": "board.pdf", "preview": "preview.png"},
                       report={"warnings": [], "layout": {"mode": "rules"}})
    assert world.dee.post(f"/api/v2/shops/{sid}/stop").json() == {"stopped": False, "status": "done"}
    assert world.dee.post("/api/v2/shops/nope/stop").status_code == 404


# ------------------------------------------------------------------ local mode: the supervisor kills a stopped job

@pytest.mark.skipif(platform.system() != "Windows", reason="corel_supervisor uses taskkill")
def test_the_supervisor_kills_the_worker_of_a_stopped_job(tmp_path):
    from app import corel_supervisor

    asked = {"stop_at": time.time() + 1.5}
    jobs = [{"master_path": "m.cdr", "shop": {"name": "A", "_test_action": "beat"}, "out_dir": "o1"},
            {"master_path": "m.cdr", "shop": {"name": "B"}, "out_dir": "o2"}]
    t0 = time.time()
    results = corel_supervisor.run_batch(jobs, tmp_path / "r.json", overall_timeout_s=60, worker_module="tests.fake_hanging_worker",
                                         skip_memory_check=True, should_cancel=lambda i: i == 0 and time.time() > asked["stop_at"])
    assert time.time() - t0 < 20                                   # killed at once, not after the 60 s no-progress limit
    assert results[0]["status"] == "error" and results[0]["error"] == corel_supervisor.STOPPED_ERROR
    assert "not started" in results[1]["error"]


@pytest.mark.skipif(platform.system() != "Windows", reason="corel_supervisor uses taskkill")
def test_a_broken_stop_check_never_kills_a_healthy_job(tmp_path):
    from app import corel_supervisor

    def broken(_i):
        raise RuntimeError("boom")
    results = corel_supervisor.run_batch([{"master_path": "m.cdr", "shop": {"name": "A"}, "out_dir": "o"}], tmp_path / "r.json",
                                         overall_timeout_s=30, worker_module="tests.fake_hanging_worker", skip_memory_check=True,
                                         should_cancel=broken)
    assert results[0]["status"] == "done"


def test_a_local_result_of_a_stopped_shop_is_not_stored(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAGE_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("SIGNAGE_ENGINE", "mock")
    monkeypatch.setenv("SIGNAGE_AUTH", "off")
    import app.db as db_module
    import app.main as main_module
    importlib.reload(db_module)
    importlib.reload(main_module)
    c = TestClient(main_module.app)
    _, sid = _shop(c)
    db_module.set_shop_status(sid, "converting", step="open")       # as if a local CorelDRAW batch were on it
    assert c.post(f"/api/v2/shops/{sid}/stop").json()["stopped"] is True
    main_module._store_convert_result(sid, {"status": "done", "result": {"files": {"cdr": "x.cdr"}, "report": {}}}, {})
    st = c.get(f"/api/v2/shops/{sid}/status").json()
    assert st["status"] == "failed" and st["error"] == STOPPED


def test_a_worker_started_from_the_agent_exe_gets_a_fresh_pyinstaller_environment(monkeypatch):
    """Found live: an agent restarted by its updater passed its PyInstaller variables on and every CorelDRAW worker hung at launch."""
    from app import corel_supervisor
    base = {"PATH": "x", "_PYI_APPLICATION_HOME_DIR": r"C:\Temp\_MEI1"}
    monkeypatch.setattr(corel_supervisor.sys, "frozen", True, raising=False)
    env = corel_supervisor.worker_env(base)
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1" and env["PATH"] == "x"
    monkeypatch.delattr(corel_supervisor.sys, "frozen")
    assert "PYINSTALLER_RESET_ENVIRONMENT" not in corel_supervisor.worker_env(base)      # from source: unchanged
