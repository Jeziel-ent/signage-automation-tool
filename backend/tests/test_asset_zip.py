"""Signage_Assets_Export.zip (app/asset_zip.py) and POST /api/export-zip."""
from __future__ import annotations

import io
import json
import zipfile

from PIL import Image

from app import asset_zip as az
from app import main
from tests.test_dual_master import _upload
from tests.test_main_v2 import client  # noqa: F401
from tests.test_print_sheet import _done_shop
from tests.test_shops_edit import _import_one


def _files(tmp_path, *names):
    for n in names:
        p = tmp_path / n
        p.parent.mkdir(parents=True, exist_ok=True)
        if n.endswith(".png"):
            Image.new("RGBA", (40, 10), (255, 0, 0, 0)).save(p)
        elif n.endswith((".jpg", ".jpeg")):
            Image.new("RGB", (40, 10), (0, 0, 255)).save(p, "JPEG")
        else:
            p.write_bytes(b"data:" + n.encode())
    return tmp_path


def test_member_names():
    assert az.member_base(1, "FAYAZ HARDWREAS  (COLACHAL)") == "01_FAYAZ_HARDWREAS_COLACHAL"
    assert az.member_base(12, "NU COLOURS AND HARDWARES") == "12_NU_COLOURS_AND_HARDWARES"
    assert az.member_base(3, "அல் மதீனா") == "03_அல்_மதீனா"
    assert az.member_base(4, "  ") == "04_Shop"


def test_sources_prefer_an_export_made_from_the_current_edits(tmp_path):
    out = _files(tmp_path, "b.cdr", "b.pdf", "b.png",
                 "exports/new/b.cdr", "exports/new/b.jpg", "exports/old/b.pdf", "exports/old/b.jpg")
    conv = {"cdr": "b.cdr", "pdf": "b.pdf", "preview": "b.png"}
    ops = [{"op": "move", "ids": ["s1"], "dx": 1, "dy": 0}]
    exports = [{"id": "new", "status": "done", "files": {"cdr": "b.cdr", "jpeg": "b.jpg"}, "ops": ops},
               {"id": "old", "status": "done", "files": {"pdf": "b.pdf", "jpeg": "b.jpg"}, "ops": []}]
    src = az.pick_sources(out, conv, exports, ops)
    assert src["cdr"] == out / "exports/new/b.cdr" and src["image"] == out / "exports/new/b.jpg"
    # the PDF only exists in an export of OLDER edits: the conversion is used, and the note says the edits are missing
    assert src["pdf"] == out / "b.pdf"
    assert src["notes"] and "pdf" in src["notes"][0] and "cdr" not in src["notes"][0]


def test_sources_without_edits_use_the_conversion_and_never_an_svg(tmp_path):
    out = _files(tmp_path, "b.cdr", "b.svg")
    exports = [{"id": "x", "status": "failed", "files": {"cdr": "b.cdr"}, "ops": []}]
    src = az.pick_sources(out, {"cdr": "b.cdr", "preview": "b.svg"}, exports, [])
    assert src == {"cdr": out / "b.cdr", "pdf": None, "image": None, "notes": []}


def test_zip_layout_png_to_jpeg_and_missing_list(tmp_path):
    src = _files(tmp_path / "src", "a.cdr", "a.pdf", "a.png", "b.cdr", "b.jpg")
    shops = [az.ShopAssets(1, "FAYAZ HARDWREAS", src / "a.cdr", src / "a.pdf", src / "a.png"),
             az.ShopAssets(2, "NU COLOURS", src / "b.cdr", None, src / "b.jpg", ["note"]),
             az.ShopAssets(2, "NU COLOURS", None, None, None)]                         # same number and name
    out = tmp_path / az.ZIP_NAME
    summary = az.write_zip(shops, out)
    z = zipfile.ZipFile(out)
    assert sorted(z.namelist()) == sorted([
        "01_FAYAZ_HARDWREAS.jpg", "CDR&PDF/cdr/01_FAYAZ_HARDWREAS.cdr", "CDR&PDF/pdf/01_FAYAZ_HARDWREAS.pdf",
        "02_NU_COLOURS.jpg", "CDR&PDF/cdr/02_NU_COLOURS.cdr"])
    with Image.open(io.BytesIO(z.read("01_FAYAZ_HARDWREAS.jpg"))) as im:
        assert im.format == "JPEG" and im.getpixel((5, 5)) == (255, 255, 255)   # transparency -> white
    assert z.read("CDR&PDF/cdr/01_FAYAZ_HARDWREAS.cdr") == b"data:a.cdr"
    assert z.getinfo("CDR&PDF/cdr/01_FAYAZ_HARDWREAS.cdr").compress_type == zipfile.ZIP_STORED
    assert z.getinfo("CDR&PDF/pdf/01_FAYAZ_HARDWREAS.pdf").compress_type == zipfile.ZIP_STORED
    assert summary["files"] == 5
    assert summary["missing"] == ["CDR&PDF/pdf/02_NU_COLOURS.pdf", "02_NU_COLOURS_2.jpg", "CDR&PDF/cdr/02_NU_COLOURS_2.cdr",
                                  "CDR&PDF/pdf/02_NU_COLOURS_2.pdf"]
    assert summary["notes"] == ["02_NU_COLOURS: note"]


def test_endpoint_packs_the_selected_shops(client):
    job = _upload(client)["id"]
    a, b = _done_shop(client, job), _done_shop(client, job, 180, 36)
    built = client.post("/api/export-zip", json={"shop_ids": [a["id"], b["id"]], "numbers": {a["id"]: 1, b["id"]: 2}})
    assert built.status_code == 200
    summary = built.json()["summary"]
    r = client.get(built.json()["download"])
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert 'filename="Signage_Assets_Export.zip"' in r.headers["content-disposition"]
    assert len(r.content) == summary["bytes"]
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    a_ = "1 - 214 X 36 Inch - Nonlit - Sri Kumar"
    b_ = "2 - 180 X 36 Inch - Nonlit - Sri Kumar"
    assert f"CDR&PDF/cdr/{a_}.cdr" in names and f"CDR&PDF/cdr/{b_}.cdr" in names
    assert summary["shops"] == 2
    # MockEngine: no PDF and an SVG preview - reported, not faked
    assert f"CDR&PDF/pdf/{a_}.pdf" in summary["missing"] and f"{a_}.jpg" in summary["missing"]


def test_endpoint_uses_an_export_of_the_current_edits(client, monkeypatch):
    job = _upload(client)["id"]
    shop = _done_shop(client, job)
    out = main.JOBS_V2 / job / "out" / shop["id"] / "exports" / "ex1"
    out.mkdir(parents=True)
    Image.new("RGB", (40, 10)).save(out / "board.jpg", "JPEG")
    (out / "board.pdf").write_bytes(b"%PDF-edited")
    ops = [{"op": "move", "ids": ["s1"], "dx": 1, "dy": 0}]
    monkeypatch.setattr(main.db, "get_editor_ops", lambda sid: ops)
    monkeypatch.setattr(main.db, "list_exports", lambda sid, limit=10: [{"id": "ex1"}])
    monkeypatch.setattr(main.db, "get_export", lambda eid: {
        "id": "ex1", "status": "done", "files_json": json.dumps({"pdf": "board.pdf", "jpeg": "board.jpg"}),
        "ops_json": json.dumps(ops)})
    built = client.post("/api/export-zip", json={"shop_ids": [shop["id"]]}).json()
    z = zipfile.ZipFile(io.BytesIO(client.get(built["download"]).content))
    from app.file_naming import shop_basename
    base = shop_basename(main.db.get_shop(shop["id"]))
    assert z.read(f"CDR&PDF/pdf/{base}.pdf") == b"%PDF-edited" and f"{base}.jpg" in z.namelist()
    notes = built["summary"]["notes"]
    assert len(notes) == 1 and "cdr" in notes[0]            # the CDR came from the conversion, without the edits


def test_a_built_zip_lives_until_deleted_or_expired(client):
    """The modal downloads it (any number of times) and may upload it; closing the modal deletes it; the TTL catches
    modals that were never closed."""
    job = _upload(client)["id"]
    shop = _done_shop(client, job)
    built = client.post("/api/export-zip", json={"shop_ids": [shop["id"]]}).json()
    token, tmp = built["token"], main._asset_zips[built["token"]].dir
    assert built["download"] == f"/api/export-zip/{token}"
    assert client.get(built["download"]).status_code == 200
    assert client.get(built["download"]).status_code == 200               # still there for a second download
    assert client.delete(f"/api/export-zip/{token}").json() == {"deleted": True}
    assert not tmp.exists() and client.get(built["download"]).status_code == 404
    assert client.delete(f"/api/export-zip/{token}").json() == {"deleted": False}
    assert client.get("/api/export-zip/unknown").status_code == 404
    again = client.post("/api/export-zip", json={"shop_ids": [shop["id"]]}).json()
    z = main._asset_zips[again["token"]]
    z.at = 0.0
    main._sweep_asset_zips()
    assert again["token"] not in main._asset_zips and not z.dir.exists()


def test_endpoint_validates(client):
    job = _upload(client)["id"]
    pending = _import_one(client, job)
    assert client.post("/api/export-zip", json={"shop_ids": []}).status_code == 422
    assert client.post("/api/export-zip", json={"shop_ids": ["nope"]}).status_code == 404
    assert client.post("/api/export-zip", json={"shop_ids": [pending["id"]]}).status_code == 409
