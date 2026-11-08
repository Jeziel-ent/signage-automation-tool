"""The editor through the agent (SIGNAGE_ENGINE=remote): the board's scene and its exports are jobs for the owner's agent, with the board's
files as inputs and the results stored where the local engine would have put them - and Corel Intelligence's learned edits follow."""
from __future__ import annotations

import io
import json
import time
import zipfile

import pytest

from tests.test_agent_queue import Agent, _convert, _pair, _shop, world  # noqa: F401  (world is a fixture)

from app import scene_export

ALL = ["convert", "scene_build", "export"]


@pytest.fixture
def dee_agent(world):  # noqa: F811
    h, aid = _pair(world.dee)
    return Agent(world.dee, h, aid)


def _convert_with(world, agent, shop_id):
    """The agent converts a queued shop (fake files)."""
    j = agent.claim(kinds=ALL)[0]
    assert j["kind"] == "convert"
    for name, data in (("board.cdr", b"CDR-BYTES"), ("board.pdf", b"%PDF"), ("preview.png", b"PNG")):
        assert agent.upload(j["id"], name, data).status_code == 200
    assert agent.result(j["id"], status="done", files={"cdr": "board.cdr", "pdf": "board.pdf", "preview": "preview.png"},
                        report={"warnings": [], "layout": {"mode": "rules"}}).status_code == 200
    return j


def _scene_zip(tmp_path, w_mm=3657.6, h_mm=1219.2, bad_member=None) -> bytes:
    d = tmp_path / "scene_src"
    scene_export.mock_scene(d, w_mm, h_mm)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in sorted(d.rglob("*")):
            if f.is_file() and f.name != "scene.json":
                z.write(f, f.relative_to(d).as_posix())
        if bad_member:
            z.writestr(bad_member, "evil")
        z.write(d / "scene.json", "scene.json")                   # last, like the agent does
    return buf.getvalue()


def _board(world, agent, tmp_path):
    """A converted shop whose scene has been built by the agent."""
    job, sid = _shop(world.dee)
    _convert(world.dee, sid)
    _convert_with(world, agent, sid)
    assert world.dee.get(f"/api/editor/{job}/{sid}/scene").status_code == 202
    s = agent.claim(kinds=ALL)[0]
    assert s["kind"] == "scene_build"
    assert agent.upload(s["id"], "scene.zip", _scene_zip(tmp_path)).status_code == 200
    assert agent.result(s["id"], status="done", files={"scene": "scene.zip"}).status_code == 200
    return job, sid


def test_opening_the_editor_queues_a_scene_build_for_the_agent(world, dee_agent, tmp_path):
    job, sid = _shop(world.dee)
    _convert(world.dee, sid)
    _convert_with(world, dee_agent, sid)
    r = world.dee.get(f"/api/editor/{job}/{sid}/scene")
    assert r.status_code == 202 and r.json()["status"] == "building"
    assert world.dee.get(f"/api/editor/{job}/{sid}/scene").json()["step"] == "waiting for your agent"
    j = dee_agent.claim(kinds=ALL)[0]
    assert j["kind"] == "scene_build" and set(j["inputs"]) == {"cdr"} and j["spec"]["width_mm"] > 3000
    assert dee_agent.input(j["id"], "cdr").content == b"CDR-BYTES"
    assert dee_agent.progress(j["id"], "images 3/10").status_code == 200
    st = world.dee.get(f"/api/editor/{job}/{sid}/scene").json()
    assert st["status"] == "building" and st["step"] == "images 3/10" and st["progress_pct"] > 10
    assert dee_agent.upload(j["id"], "scene.zip", _scene_zip(tmp_path)).status_code == 200
    assert dee_agent.result(j["id"], status="done", files={"scene": "scene.zip"}).status_code == 200
    scene = world.dee.get(f"/api/editor/{job}/{sid}/scene")
    assert scene.status_code == 200 and scene.json()["page"]["width"] > 3000 and "asset_base" in scene.json()


def test_an_older_agent_that_only_converts_is_not_given_a_scene_build(world, dee_agent):
    job, sid = _shop(world.dee)
    _convert(world.dee, sid)
    _convert_with(world, dee_agent, sid)
    world.dee.get(f"/api/editor/{job}/{sid}/scene")
    assert dee_agent.claim() == []                                    # default kinds = convert only
    assert len(dee_agent.claim(kinds=ALL)) == 1


def test_asking_for_the_scene_twice_queues_one_job(world, dee_agent):
    job, sid = _shop(world.dee)
    _convert(world.dee, sid)
    _convert_with(world, dee_agent, sid)
    for _ in range(3):
        world.dee.get(f"/api/editor/{job}/{sid}/scene")
    assert len(dee_agent.claim(kinds=ALL)) == 1 and dee_agent.claim(kinds=ALL) == []


def test_a_scene_zip_with_a_path_escape_is_rejected_and_reported(world, dee_agent, tmp_path):
    job, sid = _shop(world.dee)
    _convert(world.dee, sid)
    _convert_with(world, dee_agent, sid)
    world.dee.get(f"/api/editor/{job}/{sid}/scene")
    j = dee_agent.claim(kinds=ALL)[0]
    dee_agent.upload(j["id"], "scene.zip", _scene_zip(tmp_path, bad_member="../escape.txt"))
    assert dee_agent.result(j["id"], status="done", files={"scene": "scene.zip"}).status_code == 500
    assert not list(world.main.JOBS_V2.rglob("escape.txt"))
    r = world.dee.get(f"/api/editor/{job}/{sid}/scene")
    assert r.status_code == 500 and "unsafe path" in r.json()["detail"]


def test_a_failed_scene_build_is_reported_and_can_be_retried(world, dee_agent):
    job, sid = _shop(world.dee)
    _convert(world.dee, sid)
    _convert_with(world, dee_agent, sid)
    world.dee.get(f"/api/editor/{job}/{sid}/scene")
    j = dee_agent.claim(kinds=ALL)[0]
    dee_agent.result(j["id"], status="error", error="CorelDRAW could not open the file")
    r = world.dee.get(f"/api/editor/{job}/{sid}/scene")
    assert r.status_code == 500 and "could not open" in r.json()["detail"]
    assert world.dee.get(f"/api/editor/{job}/{sid}/scene?retry=1").status_code == 202       # a new job is queued
    assert len(dee_agent.claim(kinds=ALL)) == 1


def test_an_export_runs_on_the_agent_and_its_files_are_stored(world, dee_agent, tmp_path):
    job, sid = _board(world, dee_agent, tmp_path)
    ops = [{"op": "move", "ids": ["s3"], "dx": 50, "dy": 0}]
    assert world.dee.put(f"/api/editor/{job}/{sid}/ops", json={"ops": ops}).status_code == 200
    r = world.dee.post(f"/api/editor/{job}/{sid}/export", json={"formats": ["png", "pdf"]})
    assert r.status_code == 200
    eid = r.json()["export_id"]
    assert world.dee.get(f"/api/editor/{job}/{sid}/export/{eid}").json()["status"] == "queued"
    j = dee_agent.claim(kinds=ALL)[0]
    assert j["kind"] == "export" and j["spec"]["formats"] == ["pdf", "png"] and j["spec"]["ops"] == ops
    assert {"cdr", "scene"} <= set(j["inputs"])
    scene_bytes = dee_agent.input(j["id"], "scene").content
    assert json.loads(scene_bytes)["page"]["width"] > 3000
    assert world.dee.get(f"/api/editor/{job}/{sid}/export/{eid}").json()["status"] == "running"
    dee_agent.progress(j["id"], "replay 2/5")
    st = world.dee.get(f"/api/editor/{job}/{sid}/export/{eid}").json()
    assert st["step"] == "replay" and st["sub"] == {"done": 2, "total": 5}
    dee_agent.upload(j["id"], "board.png", b"PNGDATA")
    dee_agent.upload(j["id"], "board.pdf", b"%PDFDATA")
    report = {"files": {"png": "board.png", "pdf": "board.pdf"}, "verification": {"ok": True, "compared": 3, "mismatches": []}, "warnings": []}
    assert dee_agent.result(j["id"], status="done", files=report["files"], report=report).status_code == 200
    st = world.dee.get(f"/api/editor/{job}/{sid}/export/{eid}").json()
    assert st["status"] == "done" and st["progress_pct"] == 100 and st["files"] == {"png": "board.png", "pdf": "board.pdf"}
    assert st["report"]["verification"]["ok"] is True
    assert world.dee.get(f"/api/editor/{job}/{sid}/exports/{eid}/files/board.png").content == b"PNGDATA"


def test_the_product_images_travel_with_an_export(world, dee_agent, tmp_path):
    job, sid = _board(world, dee_agent, tmp_path)
    assets = world.main._product_assets_dir(job, sid)
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "mine.png").write_bytes(b"IMG")
    world.dee.put(f"/api/editor/{job}/{sid}/ops", json={"ops": [{"op": "move", "ids": ["s3"], "dx": 5, "dy": 0}]})
    world.dee.post(f"/api/editor/{job}/{sid}/export", json={"formats": ["png"]})
    j = dee_agent.claim(kinds=ALL)[0]
    assert "asset:mine.png" in j["inputs"] and dee_agent.input(j["id"], "asset:mine.png").content == b"IMG"


def test_a_failed_export_marks_the_export_failed(world, dee_agent, tmp_path):
    job, sid = _board(world, dee_agent, tmp_path)
    world.dee.put(f"/api/editor/{job}/{sid}/ops", json={"ops": [{"op": "move", "ids": ["s3"], "dx": 5, "dy": 0}]})
    eid = world.dee.post(f"/api/editor/{job}/{sid}/export", json={"formats": ["png"]}).json()["export_id"]
    j = dee_agent.claim(kinds=ALL)[0]
    dee_agent.result(j["id"], status="error", error="the scene does not match the document")
    st = world.dee.get(f"/api/editor/{job}/{sid}/export/{eid}").json()
    assert st["status"] == "failed" and "does not match" in st["error"]


def test_an_export_whose_agent_disappears_fails_after_its_retries(world, dee_agent, tmp_path):
    job, sid = _board(world, dee_agent, tmp_path)
    world.dee.put(f"/api/editor/{job}/{sid}/ops", json={"ops": [{"op": "move", "ids": ["s3"], "dx": 5, "dy": 0}]})
    eid = world.dee.post(f"/api/editor/{job}/{sid}/export", json={"formats": ["png"]}).json()["export_id"]
    for _ in range(3):
        assert len(dee_agent.claim(kinds=ALL)) == 1
        assert world.dee.get(f"/api/editor/{job}/{sid}/export/{eid}").json()["status"] == "running"
        with world.db._conn() as conn:                                # its lease runs out
            conn.execute("UPDATE agent_jobs SET lease_until = ? WHERE state IN ('claimed', 'running')", (time.time() - 5,))
        world.main.agents.expire_and_fail()
        if _ < 2:
            assert world.dee.get(f"/api/editor/{job}/{sid}/export/{eid}").json()["status"] == "queued"      # back in the queue
    st = world.dee.get(f"/api/editor/{job}/{sid}/export/{eid}").json()
    assert st["status"] == "failed" and "ran out of retries" in st["error"]


def test_the_export_survives_a_server_restart(world, dee_agent, tmp_path):
    job, sid = _board(world, dee_agent, tmp_path)
    world.dee.put(f"/api/editor/{job}/{sid}/ops", json={"ops": [{"op": "move", "ids": ["s3"], "dx": 5, "dy": 0}]})
    eid = world.dee.post(f"/api/editor/{job}/{sid}/export", json={"formats": ["png"]}).json()["export_id"]
    world.main._recover_interrupted_work()
    assert world.dee.get(f"/api/editor/{job}/{sid}/export/{eid}").json()["status"] == "queued"      # not failed as "interrupted"
    assert len(dee_agent.claim(kinds=ALL)) == 1


def test_save_changes_queues_a_publish_export_for_the_agent(world, dee_agent, tmp_path):
    job, sid = _board(world, dee_agent, tmp_path)
    world.dee.put(f"/api/editor/{job}/{sid}/ops", json={"ops": [{"op": "move", "ids": ["s3"], "dx": 5, "dy": 0}]})
    r = world.dee.post(f"/api/editor/{job}/{sid}/publish")
    assert r.status_code == 200 and r.json()["status"] == "building"
    j = dee_agent.claim(kinds=ALL)[0]
    assert j["kind"] == "export" and set(j["spec"]["formats"]) == {"cdr", "pdf", "png", "jpeg"}


def test_corel_intelligence_continues_through_the_agent_after_a_learned_conversion(world, dee_agent, tmp_path):
    """A designer's fix is learned; the next board of that size is converted with it: the agent converts, builds the scene, and the learned
    edits are replayed and published - three jobs chained by the site."""
    job, a = _board(world, dee_agent, tmp_path)
    assert world.dee.put(f"/api/editor/{job}/{a}/ops", json={"ops": [{"op": "move", "ids": ["s3"], "dx": 120, "dy": 0}]}).status_code == 200
    assert world.db.get_correction(a) is not None
    b = world.dee.post(f"/api/v2/jobs/{job}/shops", json={"name": "Second", "width": 12, "width_unit": "ft", "height": 4, "height_unit": "ft"}).json()["id"]
    world.dee.post(f"/api/v2/shops/{b}/convert", json={"use_intelligence": True})
    _convert_with(world, dee_agent, b)
    s = dee_agent.claim(kinds=ALL)                                    # the replay needs the new board's scene
    assert len(s) == 1 and s[0]["kind"] == "scene_build"
    dee_agent.upload(s[0]["id"], "scene.zip", _scene_zip(tmp_path))
    dee_agent.result(s[0]["id"], status="done", files={"scene": "scene.zip"})
    ops = world.db.get_editor_ops(b)
    assert [o["ids"] for o in ops] == [["s3"]]                        # learned and saved as the board's edits
    intel = world.dee.get(f"/api/v2/shops/{b}/status").json()["report"]["layout"]["intelligence"]
    assert intel["nested_sources"] == [a] and intel["learned_ops"] == ops
    pub = dee_agent.claim(kinds=ALL)                                  # ... and the files are rebuilt with them
    assert len(pub) == 1 and pub[0]["kind"] == "export" and pub[0]["spec"]["ops"] == ops
