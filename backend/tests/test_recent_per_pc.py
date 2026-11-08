"""Recently generated is per PC: each board remembers the Windows agent that converted it, and the list can be narrowed to one PC, to all of
the signed-in user's PCs, or left whole."""
from __future__ import annotations

from tests.test_agent_queue import Agent, _convert, _pair, _shop, world  # noqa: F401  (world is a fixture)


def _done_by(world, agent, name):
    job, sid = _shop(world.dee, name)
    _convert(world.dee, sid)
    j = agent.claim(1)[0]
    agent.upload(j["id"], "b.cdr", b"CDR")
    agent.upload(j["id"], "b.png", b"PNG")
    assert agent.result(j["id"], status="done", files={"cdr": "b.cdr", "preview": "b.png"}, report={"warnings": []}).status_code == 200
    return sid


def _ids(resp):
    return {r["shop_id"] for r in resp.json()}


def test_each_board_remembers_the_pc_that_made_it_and_the_list_can_be_narrowed(world):
    ha, ida = _pair(world.dee, "PC-A")
    hb, idb = _pair(world.dee, "PC-B")
    a, b = Agent(world.dee, ha, ida), Agent(world.dee, hb, idb)
    s1, s2 = _done_by(world, a, "Made on A"), _done_by(world, b, "Made on B")
    assert _ids(world.dee.get(f"/api/v2/recent?agent_id={ida}")) == {s1}
    assert _ids(world.dee.get(f"/api/v2/recent?agent_id={idb}")) == {s2}
    assert _ids(world.dee.get("/api/v2/recent?mine=1")) == {s1, s2}          # all of this user's PCs
    assert _ids(world.dee.get("/api/v2/recent")) == {s1, s2}                 # nothing asked: everything
    row = next(r for r in world.dee.get(f"/api/v2/recent?agent_id={ida}").json() if r["shop_id"] == s1)
    assert row["agent_id"] == ida


def test_another_users_pcs_are_not_in_mine(world):
    h, aid = _pair(world.dee, "PC-A")
    s1 = _done_by(world, Agent(world.dee, h, aid), "Made on A")
    assert _ids(world.boss.get("/api/v2/recent?mine=1")) == set()            # the administrator has no PC that made it
    assert _ids(world.boss.get("/api/v2/recent")) == {s1}


def test_a_board_converted_on_the_server_has_no_pc_and_shows_only_in_the_whole_list(world):
    _, sid = _shop(world.dee, "Server board")                                # never went through an agent
    assert sid in _ids(world.dee.get("/api/v2/recent"))
    assert sid not in _ids(world.dee.get("/api/v2/recent?mine=1"))
    assert world.dee.get("/api/v2/recent").json()[0]["agent_id"] is None
