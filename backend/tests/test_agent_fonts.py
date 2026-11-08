"""Through an agent the editor's fonts are the fonts of the designer's PC: the agent reports them (when they change), the site's font pickers and
missing-font checks use them, and font FILES stay on the PC."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_agent_queue import Agent, _pair, _shop, world  # noqa: F401  (world is a fixture)

FONTS = {"available": True, "all_fonts": ["Arial", "Nirmala UI", "Amaranth"], "english_fonts": ["Arial", "Amaranth"],
         "tamil_fonts": ["Nirmala UI"], "legacy_tamil_fonts": [], "tamil_detection": "cmap", "source": "InstalledFontCollection"}


def _hb(agent, fonts_hash=""):
    return agent.c.post("/api/agent/heartbeat", json={"version": "0.2.0", "fonts_hash": fonts_hash}, headers=agent.h)


def _send(agent, hash_="h1", fonts=None):
    return agent.c.post("/api/agent/fonts", json={"hash": hash_, "fonts": fonts or FONTS}, headers=agent.h)


def _agent(world):
    h, aid = _pair(world.dee, "PC-1")
    return Agent(world.dee, h, aid)


def test_the_site_asks_for_the_fonts_only_when_they_changed(world):
    a = _agent(world)
    assert _hb(a).json()["want_fonts"] is False                          # the agent has not read them yet
    assert _hb(a, "h1").json()["want_fonts"] is True                     # first time: send them
    assert _send(a, "h1").json() == {"ok": True, "fonts": 3}
    assert _hb(a, "h1").json()["want_fonts"] is False                    # unchanged
    assert _hb(a, "h2").json()["want_fonts"] is True                     # the PC installed a font


def test_the_editors_font_lists_are_the_agents_fonts(world):
    a = _agent(world)
    assert world.dee.get("/api/v2/fonts").json()["available"] is False   # nothing reported yet
    _send(a)
    v2 = world.dee.get("/api/v2/fonts").json()
    assert v2["all_fonts"] == FONTS["all_fonts"] and v2["tamil_fonts"] == ["Nirmala UI"] and v2["source"] == "your PC PC-1"
    plain = world.dee.get("/api/fonts").json()
    assert plain["available"] is True and plain["fonts"] == FONTS["all_fonts"]


def test_another_users_pc_fonts_are_not_used(world):
    _send(_agent(world))
    assert world.boss.get("/api/v2/fonts").json()["available"] is False


ALL = ["convert", "scene_build", "export", "font_files"]


def _font_job(agent):
    jobs = agent.claim(kinds=ALL)
    assert len(jobs) == 1 and jobs[0]["kind"] == "font_files"
    return jobs[0]


def test_a_font_file_is_fetched_from_the_pc_on_first_request_then_served(world):
    a = _agent(world)
    first = world.dee.get("/api/fonts/file?family=Nirmala%20UI")
    assert first.status_code == 404 and first.headers["X-Font-Pending"] == "1"           # being fetched: the editor asks again
    assert world.dee.get("/api/fonts/file?family=Amaranth").headers["X-Font-Pending"] == "1"
    job = _font_job(a)                                                                    # both families share ONE job
    assert job["spec"]["families"] == ["Nirmala UI", "Amaranth"] and job["inputs"] == {}
    assert a.upload(job["id"], "nirmala.ttf", b"TTF-BYTES").status_code == 200
    done = a.result(job["id"], status="done", files={"Nirmala UI": "nirmala.ttf"}, report={"missing": ["Amaranth"]})
    assert done.status_code == 200
    ok = world.dee.get("/api/fonts/file?family=nirmala%20ui")                             # any spelling of the family name
    assert ok.status_code == 200 and ok.content == b"TTF-BYTES" and ok.headers["content-type"] == "font/ttf"
    assert "max-age" in ok.headers["cache-control"]
    gone = world.dee.get("/api/fonts/file?family=Amaranth")
    assert gone.status_code == 404 and "X-Font-Pending" not in gone.headers               # no PC has it: do not wait, do not ask again
    assert a.claim(kinds=ALL) == []


def test_a_family_already_being_fetched_is_not_asked_for_twice(world):
    a = _agent(world)
    world.dee.get("/api/fonts/file?family=Arial")
    job = _font_job(a)                                                                    # claimed: running on the PC now
    world.dee.get("/api/fonts/file?family=Arial")
    world.dee.get("/api/fonts/file?family=Arial")
    assert a.claim(kinds=ALL) == []
    assert job["spec"]["families"] == ["Arial"]


def test_a_family_asked_for_while_a_job_runs_gets_its_own_job(world):
    a = _agent(world)
    world.dee.get("/api/fonts/file?family=Arial")
    _font_job(a)
    world.dee.get("/api/fonts/file?family=Calibri")
    assert _font_job(a)["spec"]["families"] == ["Calibri"]


def test_an_older_agent_is_not_given_font_jobs(world):
    a = _agent(world)
    world.dee.get("/api/fonts/file?family=Arial")
    assert a.claim() == []                                                                # default kinds = convert only
    assert len(a.claim(kinds=ALL)) == 1


def test_a_failed_font_job_marks_the_families_missing_for_a_while(world):
    a = _agent(world)
    world.dee.get("/api/fonts/file?family=Papyrus")
    job = _font_job(a)
    a.result(job["id"], status="error", error="the PC could not read its fonts")
    r = world.dee.get("/api/fonts/file?family=Papyrus")
    assert r.status_code == 404 and "X-Font-Pending" not in r.headers
    assert a.claim(kinds=ALL) == []


def test_a_font_name_cannot_reach_other_files(world):
    _agent(world)
    for evil in ("../../signage.db", "a" * 200, "  "):
        r = world.dee.get("/api/fonts/file", params={"family": evil})
        assert r.status_code == 404                                   # never a file: the cache is keyed by a hash of the name
    assert not [p for p in world.main.FONT_CACHE.glob("*") if ".." in p.name] if world.main.FONT_CACHE.exists() else True


def test_a_missing_font_choice_is_checked_against_the_pcs_fonts(world):
    a = _agent(world)
    _send(a)
    _, sid = _shop(world.dee)
    ok = world.dee.post("/api/fonts/substitute", json={"shop_id": sid, "original_font": "Papyrus", "substitute_font": "arial"})
    assert ok.status_code == 200 and ok.json()["substitute_font"] == "Arial"          # matched to the PC's own spelling
    bad = world.dee.post("/api/fonts/substitute", json={"shop_id": sid, "original_font": "Papyrus", "substitute_font": "Comic Sans MS"})
    assert bad.status_code == 422 and "not installed" in bad.json()["detail"]


def test_a_new_report_replaces_the_old_one(world):
    a = _agent(world)
    _send(a, "h1")
    _send(a, "h2", {**FONTS, "all_fonts": ["Arial"], "english_fonts": ["Arial"], "tamil_fonts": []})
    assert world.dee.get("/api/v2/fonts").json()["all_fonts"] == ["Arial"]


def test_only_a_paired_agent_may_report_fonts(world):
    assert TestClient(world.main.app).post("/api/agent/fonts", json={"hash": "x", "fonts": FONTS}).status_code == 401


def test_an_absurdly_large_font_list_is_refused(world):
    a = _agent(world)
    huge = {**FONTS, "all_fonts": ["F" * 100] * 30000}
    assert _send(a, "big", huge).status_code == 413
