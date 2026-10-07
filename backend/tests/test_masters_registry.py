"""Master template registry (GET / POST /api/masters/upload / DELETE /api/masters/{id}) and the per-shop `master_id`
picked in the queue's Master column: any number of landscape and portrait masters per brand, each shop converting from
the one it picked, with a fallback to its orientation's default when the pick is unusable."""
from __future__ import annotations

import json
from pathlib import Path

from tests.test_dual_master import _report, _shop, _wait_done  # noqa: F401
from tests.test_main_v2 import _fake_cdr_bytes, client  # noqa: F401


def _register(client, orientation, name="", brand="dalmia", dims=None, filename="m.cdr"):
    data = {"brand": brand, "orientation": orientation, "name": name}
    if dims is not None:
        data["dimensions_default"] = json.dumps(dims)
    r = client.post("/api/masters/upload", data=data,
                    files={"master": (filename, _fake_cdr_bytes(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()


def test_three_landscape_and_three_portrait_masters_are_all_listed_with_their_orientations(client):
    land = [_register(client, "landscape", n) for n in ("Master 1 - Standard", "Master 2 - Promotional", "")]
    port = [_register(client, "portrait", n, dims={"width": 3, "height": 6, "unit": "ft"})
            for n in ("", "Master 2 - High Density", "Master 3 - Vertical Banner")]

    body = client.get("/api/masters").json()
    assert len(body["masters"]) == 6
    assert [m["id"] for m in body["landscape"]] == [m["id"] for m in land]   # oldest first = upload order
    assert [m["id"] for m in body["portrait"]] == [m["id"] for m in port]
    assert all(m["orientation"] == "landscape" for m in body["landscape"])
    assert all(m["orientation"] == "portrait" for m in body["portrait"])
    # given names are kept, a blank one is numbered among its brand + orientation's automatic names (this is the first: Master 1)
    assert [m["name"] for m in body["landscape"]] == ["Master 1 - Standard", "Master 2 - Promotional", "Master 1"]
    assert [m["name"] for m in body["portrait"]] == ["Master 1", "Master 2 - High Density", "Master 3 - Vertical Banner"]
    m = body["portrait"][0]
    assert m["dimensions_default"] == {"width": 3.0, "height": 6.0, "unit": "ft"}
    assert body["landscape"][0]["dimensions_default"] is None
    assert Path(m["file_path"]).is_file()
    assert m["file_size"] > 0
    assert m["file_name"] == "m.cdr"
    assert m["preview_url"] == f"/api/v2/jobs/{m['id']}/preview"
    assert m["created_at"].endswith("+00:00")


def test_filters_by_brand_and_orientation(client):
    _register(client, "landscape", brand="dalmia")
    _register(client, "portrait", brand="dalmia")
    other = _register(client, "portrait", brand="adinn")
    assert [m["id"] for m in client.get("/api/masters", params={"brand": "adinn"}).json()["masters"]] == [other["id"]]
    only_port = client.get("/api/masters", params={"brand": "dalmia", "orientation": "portrait"}).json()
    assert len(only_port["masters"]) == 1
    assert only_port["landscape"] == []
    assert client.get("/api/masters", params={"orientation": "diagonal"}).status_code == 400


def test_upload_validation(client):
    def post(data, filename="m.cdr"):
        return client.post("/api/masters/upload", data={"brand": "dalmia", **data},
                           files={"master": (filename, _fake_cdr_bytes(), "application/octet-stream")})
    assert post({"orientation": "diagonal"}).status_code == 400
    assert post({"orientation": "portrait"}, filename="m.svg").status_code == 400
    assert post({"orientation": "portrait", "dimensions_default": "{\"width\": 3}"}).status_code == 400
    assert post({"orientation": "portrait", "dimensions_default": json.dumps({"width": 3, "height": 0})}).status_code == 400
    assert post({"orientation": "portrait", "dimensions_default": "not json"}).status_code == 400
    assert post({"orientation": "portrait", "name": "x" * 121}).status_code == 400
    assert client.get("/api/masters").json()["masters"] == []   # nothing half-registered


def test_the_old_upload_route_registers_too(client):
    r = client.post("/api/v2/upload", data={"brand": "dalmia", "orientation": "portrait"},
                    files={"master": ("m.cdr", _fake_cdr_bytes(), "application/octet-stream")})
    assert r.status_code == 200
    listed = client.get("/api/masters").json()["portrait"]
    assert [m["id"] for m in listed] == [r.json()["id"]]
    assert listed[0]["name"] == "Master 1"


def test_delete_hides_the_master_keeps_its_file_and_renumbers_the_rest(client):
    a, b = _register(client, "landscape"), _register(client, "landscape")
    assert client.delete(f"/api/masters/{a['id']}").json() == {"deleted": a["id"]}
    assert [m["id"] for m in client.get("/api/masters").json()["masters"]] == [b["id"]]
    assert Path(a["file_path"]).is_file()                      # soft delete: its boards live in its folder
    assert client.delete(f"/api/masters/{a['id']}").status_code == 404
    assert client.delete("/api/masters/nope").status_code == 404
    assert client.get("/api/masters").json()["landscape"][0]["name"] == "Master 1"       # b took a's number
    assert _register(client, "landscape")["name"] == "Master 2"


def test_delete_is_refused_while_a_shop_using_it_converts(client):
    import app.db as db
    m = _register(client, "portrait")
    shop = _shop(client, m["id"], 30, 40, master_id=m["id"])
    db.set_shop_status(shop["id"], "converting")
    assert client.delete(f"/api/masters/{m['id']}").status_code == 409
    db.set_shop_status(shop["id"], "done")
    assert client.delete(f"/api/masters/{m['id']}").status_code == 200


def test_each_shop_converts_from_the_master_it_picked(client):
    """3 portrait + 3 landscape masters; shops pick different ones and are converted one after another - each board's
    report and the file the worker is told to open name the picked master."""
    import app.main as main
    land = [_register(client, "landscape") for _ in range(3)]
    port = [_register(client, "portrait") for _ in range(3)]
    defaults = {"landscape_master_id": land[0]["id"], "portrait_master_id": port[0]["id"]}
    picks = [(36, 72, port[1]), (36, 72, port[2]), (192, 36, land[2]), (192, 36, land[1]), (36, 72, port[0])]
    shops = [_shop(client, land[0]["id"], w, h, **defaults) for w, h, _ in picks]
    for s, (_, _, m) in zip(shops, picks):
        r = client.post(f"/api/v2/shops/{s['id']}/convert", json={**defaults, "master_id": m["id"]})
        assert r.status_code == 200, r.text
    for s, (_, _, m) in zip(shops, picks):
        assert _wait_done(client, s["id"])["status"] == "done"
        used = _report(s["id"])["master_used"]
        assert used["job_id"] == m["id"]
        assert used["selected"] is True
        assert used["fallback"] is False
        assert used["name"] == m["name"]
        assert used["orientation"] == m["orientation"]
        assert main._convert_job(s["id"])[0]["master_path"] == m["file_path"]


def test_master_id_given_when_the_shop_is_added_is_stored_and_used(client):
    port = [_register(client, "portrait") for _ in range(2)]
    shop = _shop(client, port[0]["id"], 3, 6, unit="ft", master_id=port[1]["id"], portrait_master_id=port[0]["id"])
    assert shop["master_id"] == port[1]["id"]
    assert client.post(f"/api/v2/shops/{shop['id']}/convert").status_code == 200
    assert _wait_done(client, shop["id"])["status"] == "done"
    assert _report(shop["id"])["master_used"]["job_id"] == port[1]["id"]


def test_a_pick_of_the_wrong_orientation_falls_back_to_the_orientations_default(client):
    land, port = _register(client, "landscape"), [_register(client, "portrait") for _ in range(2)]
    # the row was picked as landscape, then its size changed to a portrait board
    shop = _shop(client, land["id"], 3, 6, unit="ft", master_id=land["id"],
                 landscape_master_id=land["id"], portrait_master_id=port[0]["id"])
    assert client.post(f"/api/v2/shops/{shop['id']}/convert").status_code == 200
    assert _wait_done(client, shop["id"])["status"] == "done"
    used = _report(shop["id"])["master_used"]
    assert used["job_id"] == port[0]["id"]
    assert not used.get("selected")
    assert "is a landscape master and this board is portrait" in used["reason"]


def test_a_deleted_pick_falls_back_to_the_brands_first_master_of_that_orientation(client):
    import app.db as db
    port = [_register(client, "portrait") for _ in range(3)]
    shop = _shop(client, port[0]["id"], 36, 72, master_id=port[2]["id"])        # no default ids sent
    db.delete_master(port[2]["id"])                                              # deleted after it was picked
    assert client.post(f"/api/v2/shops/{shop['id']}/convert").status_code == 200
    assert _wait_done(client, shop["id"])["status"] == "done"
    used = _report(shop["id"])["master_used"]
    assert used["job_id"] == port[0]["id"]
    assert "was deleted" in used["reason"]


def test_convert_can_clear_the_pick(client):
    port = [_register(client, "portrait") for _ in range(2)]
    ids = {"portrait_master_id": port[0]["id"]}
    shop = _shop(client, port[0]["id"], 36, 72, master_id=port[1]["id"], **ids)
    assert client.post(f"/api/v2/shops/{shop['id']}/convert", json={**ids, "master_id": None}).status_code == 200
    assert _wait_done(client, shop["id"])["status"] == "done"
    import app.db as db
    assert db.get_shop(shop["id"])["master_id"] is None
    assert _report(shop["id"])["master_used"]["job_id"] == port[0]["id"]


def test_master_id_validation(client):
    import app.db as db
    m = _register(client, "portrait")
    other = _register(client, "portrait", brand="adinn")
    gone = _register(client, "portrait")
    db.delete_master(gone["id"])
    r = client.post(f"/api/v2/jobs/{m['id']}/shops", json={"name": "S", "width": 3, "height": 6, "master_id": "nope"})
    assert r.status_code == 404
    for bad in (other["id"], gone["id"]):
        r = client.post(f"/api/v2/jobs/{m['id']}/shops", json={"name": "S", "width": 3, "height": 6, "master_id": bad})
        assert r.status_code == 400
    shop = _shop(client, m["id"], 3, 6)
    assert client.post(f"/api/v2/shops/{shop['id']}/convert", json={"master_id": other["id"]}).status_code == 400
    assert db.get_shop(shop["id"])["status"] == "new"     # nothing queued on a refused pick


def test_every_brand_and_orientation_starts_at_master_1_and_deleting_renumbers(client):
    def reg(brand, orientation):
        r = client.post("/api/masters/upload", data={"brand": brand, "orientation": orientation},
                        files={"master": ("m.cdr", _fake_cdr_bytes(), "application/octet-stream")})
        return r.json()
    a1, a2, a3 = (reg("dalmia", "landscape") for _ in range(3))
    assert [a1["name"], a2["name"], a3["name"]] == ["Master 1", "Master 2", "Master 3"]
    assert reg("dalmia", "portrait")["name"] == "Master 1"            # the portrait list has its own count
    assert reg("hangyo", "landscape")["name"] == "Master 1"           # and so does every other brand
    assert client.delete(f"/api/masters/{a2['id']}").status_code == 200
    names = {m["id"]: m["name"] for m in client.get("/api/masters?brand=dalmia&orientation=landscape").json()["masters"]}
    assert names == {a1["id"]: "Master 1", a3["id"]: "Master 2"}      # Master 3 became Master 2


def test_a_name_the_designer_typed_is_never_renumbered_and_old_automatic_names_are(client):
    import app.db as db
    typed = client.post("/api/masters/upload", data={"brand": "x", "orientation": "landscape", "name": "Master 9 - Promo"},
                        files={"master": ("m.cdr", _fake_cdr_bytes(), "application/octet-stream")}).json()
    old = client.post("/api/masters/upload", data={"brand": "x", "orientation": "landscape"},
                      files={"master": ("m.cdr", _fake_cdr_bytes(), "application/octet-stream")}).json()
    with db._conn() as conn:                                             # what an earlier version stored for an automatic name
        conn.execute("UPDATE jobs SET master_name = 'Master 7' WHERE id = ?", (old["id"],))
    names = {m["id"]: m["name"] for m in client.get("/api/masters?brand=x").json()["masters"]}
    assert names == {typed["id"]: "Master 9 - Promo", old["id"]: "Master 1"}


def test_starting_the_server_hides_the_previous_runs_masters_but_keeps_their_files_and_boards(client, monkeypatch):
    import app.main as main
    monkeypatch.delenv("SIGNAGE_KEEP_MASTERS", raising=False)
    a, b = _register(client, "landscape"), _register(client, "portrait")
    shop = _shop(client, a["id"], 30, 40)
    main._archive_previous_masters()                                   # what the startup handler runs
    body = client.get("/api/masters").json()
    assert body["masters"] == []
    assert body["landscape"] == []
    assert body["portrait"] == []
    assert Path(a["file_path"]).is_file()
    assert Path(b["file_path"]).is_file()
    import app.db as db
    assert db.get_shop(shop["id"]) is not None                          # boards made from them are still there
    assert _register(client, "landscape")["name"] == "Master 1"        # and the next upload starts again at Master 1


def test_keep_masters_env_leaves_them_listed(client, monkeypatch):
    import app.main as main
    monkeypatch.setenv("SIGNAGE_KEEP_MASTERS", "1")
    _register(client, "landscape")
    main._archive_previous_masters()
    assert len(client.get("/api/masters").json()["masters"]) == 1
