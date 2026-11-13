"""Save Changes -> the board's new files: POST /api/editor/{job}/{shop}/publish queues one export of the saved edits, and the ZIP uses it."""
from __future__ import annotations

import io
import time
import zipfile

from tests.test_editor_api import _converted_shop, _scene, client  # noqa: F401  (client is a fixture)

from app import asset_zip as az


def _save_a_move(client, job, shop):  # noqa: F811
    top = _scene(client, job, shop)["layers"][0]["children"][0]["id"]
    assert client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": [{"op": "move", "ids": [top], "dx": 40, "dy": 0}]}).status_code == 200


def _wait_export(client, job, shop, export_id):  # noqa: F811
    for _ in range(200):
        st = client.get(f"/api/editor/{job}/{shop}/export/{export_id}").json()
        if st["status"] in ("done", "failed"):
            return st
        time.sleep(0.05)
    raise AssertionError("export did not finish")


def test_publish_without_edits_queues_nothing(client):  # noqa: F811
    job, shop = _converted_shop(client)
    assert client.post(f"/api/editor/{job}/{shop}/publish").json() == {"status": "unchanged", "export_id": None}


def test_publish_builds_all_four_files_from_the_saved_edits(client):  # noqa: F811
    job, shop = _converted_shop(client)
    _save_a_move(client, job, shop)
    r = client.post(f"/api/editor/{job}/{shop}/publish").json()
    assert r["status"] == "building"
    done = _wait_export(client, job, shop, r["export_id"])
    assert done["status"] == "done"
    assert set(done["files"]) == {"cdr", "pdf", "png", "jpeg"}


def test_publishing_the_same_edits_twice_reuses_the_export(client):  # noqa: F811
    job, shop = _converted_shop(client)
    _save_a_move(client, job, shop)
    first = client.post(f"/api/editor/{job}/{shop}/publish").json()
    again = client.post(f"/api/editor/{job}/{shop}/publish").json()
    assert again["export_id"] == first["export_id"]
    _wait_export(client, job, shop, first["export_id"])
    assert client.post(f"/api/editor/{job}/{shop}/publish").json() == {"status": "ready", "export_id": first["export_id"]}


def test_editing_again_after_publishing_builds_a_new_export(client):  # noqa: F811
    job, shop = _converted_shop(client)
    _save_a_move(client, job, shop)
    first = client.post(f"/api/editor/{job}/{shop}/publish").json()
    _wait_export(client, job, shop, first["export_id"])
    top = _scene(client, job, shop)["layers"][0]["children"][0]["id"]
    client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": [{"op": "move", "ids": [top], "dx": 90, "dy": 0}]})
    second = client.post(f"/api/editor/{job}/{shop}/publish").json()
    assert second["export_id"] != first["export_id"]


def test_the_zip_uses_the_published_files_without_a_warning(client):  # noqa: F811
    job, shop = _converted_shop(client)
    _save_a_move(client, job, shop)
    r = client.post(f"/api/editor/{job}/{shop}/publish").json()
    _wait_export(client, job, shop, r["export_id"])
    built = client.post("/api/export-zip", json={"shop_ids": [shop]}).json()
    assert built["summary"]["notes"] == []
    z = zipfile.ZipFile(io.BytesIO(client.get(built["download"]).content))
    assert any(n.endswith(".cdr") for n in z.namelist())


def test_the_zip_says_when_the_edited_files_are_still_being_built(tmp_path):
    out = tmp_path
    (out / "b.cdr").write_bytes(b"x")
    ops = [{"op": "move", "ids": ["s1"], "dx": 1, "dy": 0}]
    building = [{"id": "e", "status": "running", "files": {}, "ops": ops}]
    note = az.pick_sources(out, {"cdr": "b.cdr"}, building, ops)["notes"][0]
    assert "still being built" in note
    failed = [{"id": "e", "status": "failed", "files": {}, "ops": ops}]
    assert "not exported" in az.pick_sources(out, {"cdr": "b.cdr"}, failed, ops)["notes"][0]


def test_an_export_made_by_an_older_replay_is_built_again(client):  # noqa: F811
    """Found on a real board: Save Changes kept reusing an export made before the edited-text fix (agent 0.3.5), so the files
    still showed the old text layout. An export without the current replay_version is never reused."""
    import json as _json

    from app import db
    job, shop = _converted_shop(client)
    _save_a_move(client, job, shop)
    first = client.post(f"/api/editor/{job}/{shop}/publish").json()
    _wait_export(client, job, shop, first["export_id"])
    row = db.get_export(first["export_id"])
    rep = _json.loads(row["report_json"])
    rep.pop("replay_version")
    with db._conn() as conn:
        conn.execute("UPDATE exports SET report_json = ? WHERE id = ?", (_json.dumps(rep), first["export_id"]))
    again = client.post(f"/api/editor/{job}/{shop}/publish").json()
    assert again["status"] == "building" and again["export_id"] != first["export_id"]


def test_the_board_file_links_give_the_edited_files(client):  # noqa: F811
    """Recently generated's CDR / PDF / PNG links are the shop's conversion file names; once the edits are exported they serve
    the edited files (the conversion's only without exported edits)."""
    job, shop = _converted_shop(client)
    files = client.get(f"/api/v2/shops/{shop}/status").json()["files"]
    name = files["cdr"]
    before = client.get(f"/api/v2/shops/{shop}/files/{name}").content
    _save_a_move(client, job, shop)
    r = client.post(f"/api/editor/{job}/{shop}/publish").json()
    _wait_export(client, job, shop, r["export_id"])
    after = client.get(f"/api/v2/shops/{shop}/files/{name}")
    assert after.status_code == 200 and after.content != before
