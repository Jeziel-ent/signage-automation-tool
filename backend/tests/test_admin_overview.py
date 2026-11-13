"""GET /api/admin/overview - the admin monitor's activity panels, built only from what the database records."""
from __future__ import annotations

from tests.test_stop_conversion import world  # noqa: F401  (fixture: remote mode, an admin, a designer and her paired agent)
from tests.test_agent_queue import ADMIN, _convert, _shop
from fastapi.testclient import TestClient


def _boss(world):
    c = TestClient(world.main.app)
    assert c.post("/api/auth/login", json=ADMIN).status_code == 200
    return c


def _finish(world, job):
    for name in ("board.cdr", "board.pdf", "preview.png"):
        world.agent.upload(job["id"], name, b"x")
    r = world.agent.result(job["id"], status="done", files={"cdr": "board.cdr", "pdf": "board.pdf", "preview": "preview.png"},
                           report={"warnings": [], "layout": {"mode": "rules"}})
    assert r.status_code == 200


def test_overview_counts_what_really_happened(world):
    boss = _boss(world)
    empty = boss.get("/api/admin/overview").json()
    assert len(empty["series"]) == 14 and all(d["converted"] == 0 for d in empty["series"])
    assert empty["live"] == [] and empty["recent"] == [] and empty["median_convert_s"] is None

    _, done = _shop(world.dee, "Done Board")
    _, stopped = _shop(world.dee, "Stopped Board")
    _, waiting = _shop(world.dee, "Waiting Board")
    _convert(world.dee, done)
    _finish(world, world.agent.claim(n=1)[0])
    _convert(world.dee, stopped)
    world.dee.post(f"/api/v2/shops/{stopped}/stop")
    _convert(world.dee, waiting)

    o = boss.get("/api/admin/overview").json()
    today = o["series"][-1]
    assert (today["converted"], today["stopped"], today["failed"]) == (1, 1, 0)
    assert [j["shop"] for j in o["live"]] == ["Waiting Board"] and o["live"][0]["state"] == "queued"
    assert o["live"][0]["user_email"] == "dee@example.com"
    by_shop = {j["shop"]: j for j in o["recent"]}
    assert by_shop["Done Board"]["state"] == "done" and by_shop["Done Board"]["pc"] == "PC"
    assert by_shop["Done Board"]["seconds"] is not None and by_shop["Stopped Board"]["state"] == "cancelled"
    assert o["median_convert_s"] is not None
    assert o["counts"]["users"] == 2 and o["counts"]["shops"] == 3


def test_overview_is_for_administrators_only(world):
    assert world.dee.get("/api/admin/overview").status_code == 403
