"""The Shops Queue row's quick Download popup: one converted shop's CDR / JPG / PNG / PDF under the standard file name."""
from __future__ import annotations

import io
import json
from urllib.parse import unquote

from PIL import Image

from app import asset_zip as az
from app import main
from tests.test_dual_master import _upload
from tests.test_main_v2 import client  # noqa: F401
from tests.test_print_sheet import _done_shop


def _name(r):
    cd = r.headers["content-disposition"]
    return unquote(cd.split("filename*=utf-8''", 1)[1]) if "filename*=" in cd else cd.split('filename="', 1)[1].rstrip('"')


def test_pick_single_prefers_a_current_export_and_explains_the_fallbacks(tmp_path):
    (tmp_path / "b.cdr").write_bytes(b"CDR")
    Image.new("RGBA", (20, 10), (255, 0, 0, 255)).save(tmp_path / "b.png")
    conv = {"cdr": "b.cdr", "preview": "b.png"}
    ex = tmp_path / "exports" / "e1"
    ex.mkdir(parents=True)
    (ex / "b.pdf").write_bytes(b"%PDF")
    exports = [{"id": "e1", "status": "done", "files": {"pdf": "b.pdf"}, "ops": [{"op": "x"}]}]
    # edits saved AND exported: the export's PDF; the CDR still comes from the conversion (with a note)
    pdf = az.pick_single(tmp_path, conv, exports, [{"op": "x"}], "pdf")
    assert pdf["source"] == "editor export"
    assert pdf["note"] is None
    cdr = az.pick_single(tmp_path, conv, exports, [{"op": "x"}], "cdr")
    assert cdr["source"] == "conversion"
    assert "never exported" in cdr["note"]
    # edits changed since that export: the export no longer counts
    assert az.pick_single(tmp_path, conv, exports, [{"op": "y"}], "pdf")["path"] is None
    jpg = az.pick_single(tmp_path, conv, [], [], "jpg")
    assert jpg["to_jpeg"]
    assert jpg["path"].name == "b.png"
    assert "preview resolution" in jpg["note"]
    assert az.pick_single(tmp_path, {"preview": "b.svg"}, [], [], "png")["reason"]


def test_row_download_names_files_by_the_standard_and_reports_availability(client, monkeypatch):
    job = _upload(client)["id"]
    shop = _done_shop(client, job)
    sid = shop["id"]
    avail = client.get(f"/api/v2/shops/{sid}/downloads").json()
    assert avail["cdr"]["available"]
    assert not avail["pdf"]["available"]
    assert avail["pdf"]["reason"]  # MockEngine: no PDF
    r = client.get(f"/api/v2/shops/{sid}/download/cdr?no=5")
    assert r.status_code == 200
    assert _name(r) == "5 - 214 X 36 Inch - Nonlit - Sri Kumar.cdr"
    assert client.get(f"/api/v2/shops/{sid}/download/pdf").status_code == 404
    assert client.get(f"/api/v2/shops/{sid}/download/tiff").status_code == 422
    # a PNG from an editor export of the current edits -> JPG re-encoded on white, PNG as is
    out = main.JOBS_V2 / job / "out" / sid / "exports" / "ex1"
    out.mkdir(parents=True)
    Image.new("RGBA", (40, 10), (0, 0, 255, 128)).save(out / "board.png")
    monkeypatch.setattr(main.db, "get_editor_ops", lambda s: [])
    monkeypatch.setattr(main.db, "list_exports", lambda s, limit=10: [{"id": "ex1"}])
    monkeypatch.setattr(main.db, "get_export", lambda e: {"id": "ex1", "status": "done",
                                                          "files_json": json.dumps({"png": "board.png"}), "ops_json": "[]"})
    r = client.get(f"/api/v2/shops/{sid}/download/jpg?no=2")
    assert r.status_code == 200
    assert _name(r) == "2 - 214 X 36 Inch - Nonlit - Sri Kumar.jpg"
    im = Image.open(io.BytesIO(r.content))
    assert im.format == "JPEG"
    assert im.size == (40, 10)
    r = client.get(f"/api/v2/shops/{sid}/download/png")
    assert r.status_code == 200
    assert Image.open(io.BytesIO(r.content)).format == "PNG"


def test_not_converted_is_refused(client):
    job = _upload(client)["id"]
    s = client.post(f"/api/v2/jobs/{job}/shops", json={"name": "X", "width": 1, "height": 1}).json()
    assert client.get(f"/api/v2/shops/{s['id']}/downloads").status_code == 409
    assert client.get(f"/api/v2/shops/{s['id']}/download/cdr").status_code == 409
