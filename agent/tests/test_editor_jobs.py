"""The agent doing the EDITOR's work for the real website (SIGNAGE_ENGINE=remote, mock engine on the agent): building a board's scene, exporting
it with the designer's edits, and the Corel Intelligence chain (convert -> scene -> replay learned edits -> publish)."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from signage_agent import engine_bridge
from signage_agent.jobs import JobRunner, _zip_scene
from test_jobs import _make_agent, _shop, _status, world  # noqa: F401  (world is a fixture)


def _converted(world, agent, runner, name="NR Traders"):
    job, sid = _shop(world, name)
    world.web.post(f"/api/v2/shops/{sid}/convert")
    assert runner.poll_once() == 1
    assert _status(world, sid)["status"] == "done"
    return job, sid


def _scene_ready(world, runner, job, sid):
    assert world.web.get(f"/api/editor/{job}/{sid}/scene").status_code == 202       # queued for the agent
    assert runner.poll_once() == 1
    r = world.web.get(f"/api/editor/{job}/{sid}/scene")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_agent_builds_the_scene_for_the_editor(world):
    agent = _make_agent(world)
    runner = JobRunner(agent, mock=True)
    job, sid = _converted(world, agent, runner)
    scene = _scene_ready(world, runner, job, sid)
    assert scene["page"]["width"] > 3000 and scene["layers"] and "asset_base" in scene
    img = next(f for f in (world.tmp / "data" / "jobs_v2" / job / "out" / sid / "scene" / "img").iterdir())
    assert world.web.get(f"/api/editor/{job}/{sid}/asset/{img.name}").status_code == 200       # the editor can draw it


def test_the_agent_exports_the_board_with_the_designers_edits(world):
    agent = _make_agent(world)
    runner = JobRunner(agent, mock=True)
    job, sid = _converted(world, agent, runner)
    _scene_ready(world, runner, job, sid)
    ops = [{"op": "move", "ids": ["s3"], "dx": 50, "dy": 0}]
    assert world.web.put(f"/api/editor/{job}/{sid}/ops", json={"ops": ops}).status_code == 200
    eid = world.web.post(f"/api/editor/{job}/{sid}/export", json={"formats": ["png", "pdf"]}).json()["export_id"]
    assert runner.poll_once() == 1
    st = world.web.get(f"/api/editor/{job}/{sid}/export/{eid}").json()
    assert st["status"] == "done" and set(st["files"]) == {"png", "pdf"}, st
    assert st["report"]["mock"] is True
    for name in st["files"].values():
        r = world.web.get(f"/api/editor/{job}/{sid}/exports/{eid}/files/{name}")
        assert r.status_code == 200 and len(r.content) > 100


def test_a_scene_build_that_fails_on_the_agent_reaches_the_editor_as_an_error(world, monkeypatch):
    agent = _make_agent(world)
    runner = JobRunner(agent, mock=True)
    job, sid = _converted(world, agent, runner)
    monkeypatch.setattr(engine_bridge, "mock_scene", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("CorelDRAW could not open the file")))
    assert world.web.get(f"/api/editor/{job}/{sid}/scene").status_code == 202
    runner.poll_once()
    r = world.web.get(f"/api/editor/{job}/{sid}/scene")
    assert r.status_code == 500 and "could not open" in r.json()["detail"]


def test_the_scene_zip_ends_with_scene_json(tmp_path):
    src = tmp_path / "scene"
    (src / "img").mkdir(parents=True)
    (src / "img" / "a.svg").write_text("<svg/>")
    (src / "page.png").write_bytes(b"PNG")
    (src / "scene.json").write_text("{}")
    _zip_scene(src, tmp_path / "scene.zip")
    names = zipfile.ZipFile(tmp_path / "scene.zip").namelist()
    assert names[-1] == "scene.json" and set(names) == {"img/a.svg", "page.png", "scene.json"}


def test_corel_intelligence_runs_end_to_end_through_the_agent(world):
    """Board A is converted and corrected by the designer (learned); board B is converted WITH it. The agent converts B, builds its scene,
    the site replays the learned edits as B's edits, and the agent exports B's files with them."""
    agent = _make_agent(world)
    runner = JobRunner(agent, mock=True)
    job, a = _converted(world, agent, runner, "Board A")
    _scene_ready(world, runner, job, a)
    assert world.web.put(f"/api/editor/{job}/{a}/ops", json={"ops": [{"op": "move", "ids": ["s3"], "dx": 120, "dy": 0}]}).status_code == 200
    assert world.db.get_correction(a) is not None

    b = world.web.post(f"/api/v2/jobs/{job}/shops", json={"name": "Board B", "width": 12, "width_unit": "ft", "height": 4, "height_unit": "ft"}).json()["id"]
    world.web.post(f"/api/v2/shops/{b}/convert", json={"use_intelligence": True})
    assert runner.poll_once() == 1                                     # convert
    assert _status(world, b)["status"] == "done"
    assert runner.poll_once() == 1                                     # the scene the replay needs
    ops = world.db.get_editor_ops(b)
    assert [o["ids"] for o in ops] == [["s3"]]                         # the learned edit became B's edit
    assert runner.poll_once() == 1                                     # the publish export with those edits
    exports = world.db.list_exports(b)
    assert len(exports) == 1 and exports[0]["status"] == "done"
    intel = _status(world, b)["report"]["layout"]["intelligence"]
    assert intel["nested_sources"] == [a] and intel["learned_ops"] == ops

    # the designer keeps it as it is: board A's lesson is confirmed
    assert world.web.put(f"/api/editor/{job}/{b}/ops", json={"ops": ops}).status_code == 200
    fb = json.loads(world.db.get_correction(a)["record_json"]).get("feedback", {})
    assert fb.get("boards", {}).get(b) == [1, 0]
    assert world.db.get_correction(b) is None                          # and nothing is learned from merely keeping it


def test_the_agent_reports_its_pcs_fonts_once_and_again_when_they_change(world, monkeypatch):
    import time

    from signage_agent import client

    report = {"available": True, "all_fonts": ["Arial", "Nirmala UI"], "english_fonts": ["Arial"], "tamil_fonts": ["Nirmala UI"],
              "legacy_tamil_fonts": [], "tamil_detection": "cmap", "source": "test"}
    sent: list[str] = []
    real = client.send_fonts
    monkeypatch.setattr(engine_bridge, "fonts_report", lambda: dict(report))
    monkeypatch.setattr(client, "send_fonts", lambda *a, **k: (sent.append(a[2]), real(*a, **k))[1])
    agent = _make_agent(world)                                          # pairing beats once; the fonts are read in the background

    def beat_until(count):
        for _ in range(60):
            agent.beat()
            if len(sent) >= count:
                return
            time.sleep(0.1)

    beat_until(1)
    assert len(sent) == 1
    assert world.web.get("/api/v2/fonts").json()["all_fonts"] == ["Arial", "Nirmala UI"]
    for _ in range(3):
        agent.beat()
    assert len(sent) == 1                                               # unchanged: not sent again
    report["all_fonts"] = ["Arial", "Nirmala UI", "Amaranth"]
    agent._fonts_at = 0                                                 # the hourly re-read
    beat_until(2)
    assert len(sent) == 2 and sent[0] != sent[1]
    assert world.web.get("/api/v2/fonts").json()["all_fonts"] == ["Arial", "Nirmala UI", "Amaranth"]
