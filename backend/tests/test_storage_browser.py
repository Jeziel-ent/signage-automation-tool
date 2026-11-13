"""The admin file browser: masters by brand, boards with their files and who made them, and deletes that never touch what was learned."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_agent_queue import ADMIN, _convert, _shop
from tests.test_stop_conversion import world  # noqa: F401  (remote mode, an admin, a designer and her paired agent)


def _boss(world):
    c = TestClient(world.main.app)
    assert c.post("/api/auth/login", json=ADMIN).status_code == 200
    return c


def _made_board(world, name="NR Traders"):
    job, sid = _shop(world.dee, name)
    _convert(world.dee, sid)
    j = world.agent.claim(n=1)[0]
    for f, data in (("board.cdr", b"C" * 1000), ("board.pdf", b"P" * 500), ("preview.png", b"N" * 200)):
        world.agent.upload(j["id"], f, data)
    assert world.agent.result(j["id"], status="done", files={"cdr": "board.cdr", "pdf": "board.pdf", "preview": "preview.png"},
                              report={"warnings": [], "layout": {"mode": "rules"}}).status_code == 200
    return job, sid


def _learn(world, sid):
    world.db.save_correction({"shop_id": sid, "brand": "dalmia", "master_file": "m.cdr", "page_w_mm": 3657.6, "page_h_mm": 1219.2,
                              "board_type": None, "changes": []})


def test_listing_shows_masters_by_brand_and_boards_with_files(world):
    boss = _boss(world)
    job, sid = _made_board(world)
    _learn(world, sid)
    out = boss.get("/api/admin/files").json()
    m = next(x for x in out["masters"] if x["id"] == job)
    assert m["brand"] == "dalmia" and m["file_name"] == "m.cdr" and m["bytes"] > 0 and m["boards"] == 1 and not m["deleted"]
    b = next(x for x in out["boards"] if x["shop_id"] == sid)
    assert b["name"] == "NR Traders" and b["brand"] == "dalmia" and b["status"] == "done" and b["learned"] is True
    assert {f["name"]: (f["kind"], f["bytes"]) for f in b["files"]} == {"board.cdr": ("cdr", 1000), "board.pdf": ("pdf", 500),
                                                                        "preview.png": ("png", 200)}
    assert b["user_email"] == "dee@example.com" and b["pc"] == "PC" and b["bytes"] == 1700
    assert out["learned_records"] == 1


def test_deleting_a_board_keeps_what_it_taught(world):
    boss = _boss(world)
    _, sid = _made_board(world)
    _learn(world, sid)
    r = boss.delete(f"/api/admin/files/boards/{sid}")
    assert r.status_code == 200 and r.json()["bytes"] == 1700
    assert world.db.get_shop(sid) is None
    assert world.db.get_correction(sid) is not None                 # Corel Intelligence keeps the lesson (data only, no files)
    assert all(b["shop_id"] != sid for b in boss.get("/api/admin/files").json()["boards"])


def test_deleting_only_the_exports_or_scene_keeps_the_board(world):
    boss = _boss(world)
    job, sid = _made_board(world)
    out = world.main.JOBS_V2 / job / "out" / sid
    (out / "exports" / "x1").mkdir(parents=True)
    (out / "exports" / "x1" / "e.cdr").write_bytes(b"E" * 300)
    assert boss.get("/api/admin/files").json()["boards"][0]["parts"] == {"exports": 300}
    assert boss.delete(f"/api/admin/files/boards/{sid}?part=exports").json()["bytes"] == 300
    assert world.db.get_shop(sid) is not None and (out / "board.cdr").is_file() and not (out / "exports").exists()
    assert boss.delete(f"/api/admin/files/boards/{sid}?part=nonsense").status_code == 422


def test_a_master_file_goes_but_its_boards_stay(world):
    boss = _boss(world)
    job, sid = _made_board(world)
    assert boss.get(f"/api/admin/files/masters/{job}/download").status_code == 200
    r = boss.delete(f"/api/admin/files/masters/{job}")
    assert r.status_code == 200 and r.json()["bytes"] > 0
    assert not (world.main.JOBS_V2 / job / "master.cdr").exists()
    assert world.db.get_job(job)["deleted_at"] and world.db.get_shop(sid)["status"] == "done"
    assert boss.get(f"/api/admin/files/masters/{job}/download").status_code == 404


def test_busy_things_are_not_deleted(world):
    boss = _boss(world)
    job, sid = _shop(world.dee, "Busy Board")
    _convert(world.dee, sid)
    assert boss.delete(f"/api/admin/files/boards/{sid}").status_code == 409
    assert boss.delete(f"/api/admin/files/masters/{job}").status_code == 409


def test_backups_can_be_removed_but_nothing_outside_their_folder(world):
    boss = _boss(world)
    bdir = world.main.DATA / "backups"
    bdir.mkdir(exist_ok=True)
    (bdir / "signage-2026.db").write_bytes(b"B" * 64)
    assert [b["name"] for b in boss.get("/api/admin/files").json()["backups"]] == ["signage-2026.db"]
    assert boss.delete("/api/admin/files/backups/..%2Fsignage.db").status_code in (404, 405)   # the router never routes it here
    import pytest
    from app import storage_browser
    for bad in ("../signage.db", "..", "sub/x.db"):
        with pytest.raises(FileNotFoundError):
            storage_browser.delete_backup(world.main.DATA, bad)
    assert boss.delete("/api/admin/files/backups/signage-2026.db").json()["bytes"] == 64
    assert (world.main.DATA / "signage.db").exists()


def test_the_file_browser_is_for_administrators_only(world):
    assert world.dee.get("/api/admin/files").status_code == 403
