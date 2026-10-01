"""The standard export file name "<S.NO> - <W> X <H> <Unit> - <Type> - <SHOP NAME>.<ext>" and where it is applied:
conversion outputs, per-file downloads, the multi-shop ZIP and "Download All CDRs"."""
from __future__ import annotations

import io
import time
import zipfile

from app.file_naming import safe_filename, signage_basename, size_label
from tests.test_dual_master import _upload
from tests.test_main_v2 import client  # noqa: F401


def test_the_designers_example_exactly():
    assert signage_basename(76, 125, "in", 48, "in", "Nonlit", "SRI KANNIYAMMAN NATTU MARUNTHU KADAI") == \
        "76 - 125 X 48 Inch - Nonlit - SRI KANNIYAMMAN NATTU MARUNTHU KADAI"


def test_units_numbers_default_type_and_unsafe_characters():
    assert size_label(10, "ft", 4, "ft") == "10 X 4 Feet"
    assert size_label(10, "ft", 48, "in") == "10 Feet X 48 Inch"
    assert size_label(10.5, "in", 4.0, "in") == "10.5 X 4 Inch"
    assert signage_basename(3, 12, "ft", 5, "ft", None, "A") == "3 - 12 X 5 Feet - Nonlit - A"
    assert signage_basename(3, 12, "ft", 5, "ft", "Backlit", 'Kumar / Sons: "1"?') == "3 - 12 X 5 Feet - Backlit - Kumar Sons 1"
    # an imported designer file name is reduced to the shop name
    assert signage_basename(1, 60, "in", 75, "in", "Frontlit", "73 - 60 X 75 Inch - Nonlit - SRI AMBIRAMI STORES.cdr") == \
        "1 - 60 X 75 Inch - Frontlit - SRI AMBIRAMI STORES"
    assert safe_filename("ஸ்ரீ கவி ஸ்டீல்ஸ்") == "ஸ்ரீ கவி ஸ்டீல்ஸ்"
    assert len(signage_basename(1, 1, "in", 1, "in", "Nonlit", "X" * 400)) <= 120


def _download_name(r) -> str:
    """The file name a browser saves a response under (Starlette uses RFC 5987 `filename*` for names with spaces)."""
    from urllib.parse import unquote
    cd = r.headers["content-disposition"]
    if "filename*=utf-8''" in cd:
        return unquote(cd.split("filename*=utf-8''", 1)[1])
    return cd.split('filename="', 1)[1].rstrip('"')


def _wait(client, sid):
    deadline = time.time() + 10
    while time.time() < deadline:
        st = client.get(f"/api/v2/shops/{sid}/status").json()
        if st["status"] in ("done", "failed"):
            return st
        time.sleep(0.05)
    raise AssertionError("did not finish")


def _converted(client, job, name, sno, board_type="Backlit"):
    shop = client.post(f"/api/v2/jobs/{job}/shops", json={"name": name, "width": 10, "height": 4, "unit": "ft",
                                                          "board_type": board_type, "shop_name_local": "தமிழ்"}).json()
    assert shop["board_type"] == board_type and shop["shop_name_local"] == "தமிழ்"
    assert client.post(f"/api/v2/shops/{shop['id']}/convert", json={"sno": sno}).status_code == 200
    return _wait(client, shop["id"])


def test_conversion_outputs_and_downloads_use_the_standard_name(client):
    job = _upload(client)["id"]
    st = _converted(client, job, "Sri Kumar", 7)
    assert st["status"] == "done"
    assert st["files"]["cdr"] == "7 - 10 X 4 Feet - Backlit - Sri Kumar.cdr"
    r = client.get(f"/api/v2/shops/{st['id']}/files/{st['files']['cdr']}")
    assert r.status_code == 200 and _download_name(r) == "7 - 10 X 4 Feet - Backlit - Sri Kumar.cdr"
    # the board type is editable afterwards like any other field
    r = client.patch(f"/api/v2/shops/{st['id']}", json={"board_type": "Frontlit"})
    assert r.status_code == 200 and r.json()["board_type"] == "Frontlit"


def test_download_all_cdrs_bundles_every_converted_cdr(client):
    job = _upload(client)["id"]
    a = _converted(client, job, "Alpha", 1)
    b = _converted(client, job, "Beta", 2, "Nonlit")
    new = client.post(f"/api/v2/jobs/{job}/shops", json={"name": "Pending", "width": 1, "height": 1}).json()
    r = client.get(f"/api/v2/download-cdrs?ids={a['id']},{b['id']},{new['id']}&nos=4,5,6")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(z.namelist()) == ["4 - 10 X 4 Feet - Backlit - Alpha.cdr", "5 - 10 X 4 Feet - Nonlit - Beta.cdr"]
    assert client.get(f"/api/v2/download-cdrs?ids={new['id']}").status_code == 409
    assert client.get("/api/v2/download-cdrs?ids=nope").status_code == 404
    assert client.get("/api/v2/download-cdrs?ids=").status_code == 422


def test_generate_zip_members_use_the_standard_name(client):
    job = _upload(client)["id"]
    a = _converted(client, job, "Alpha", 1)
    r = client.post("/api/export-zip", json={"shop_ids": [a["id"]], "numbers": {a["id"]: 3}})
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(client.get(r.json()["download"]).content))
    assert "CDR&PDF/cdr/3 - 10 X 4 Feet - Backlit - Alpha.cdr" in z.namelist()


def test_download_all_in_one_chosen_format(client):
    job = _upload(client)["id"]
    a = _converted(client, job, "Alpha", 1)
    b = _converted(client, job, "Beta", 2, "Nonlit")
    ids = f"{a['id']},{b['id']}"
    r = client.get(f"/api/v2/download-all?format=CDR&ids={ids}&nos=SL-1,76")
    assert r.status_code == 200 and _download_name(r) == "All_CDR_Files.zip"
    names = sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert names == ["76 - 10 X 4 Feet - Nonlit - Beta.cdr", "SL-1 - 10 X 4 Feet - Backlit - Alpha.cdr"]
    # MockEngine makes no PDF and only an SVG preview: nothing to bundle -> 409, never a faked file
    for fmt in ("pdf", "png", "jpg"):
        assert client.get(f"/api/v2/download-all?format={fmt}&ids={ids}").status_code == 409, fmt
    assert client.get(f"/api/v2/download-all?format=svg&ids={ids}").status_code == 422
    # the old "Download All CDRs" link still works
    assert client.get(f"/api/v2/download-cdrs?ids={ids}").status_code == 200


def test_format_zip_reencodes_png_as_jpg_and_lists_missing(tmp_path):
    from PIL import Image

    from app.asset_zip import MISSING_NOTE, write_format_zip

    png = tmp_path / "p.png"
    Image.new("RGBA", (40, 20), (255, 0, 0, 0)).save(png)            # fully transparent -> white in the JPG
    out = tmp_path / "all.zip"
    s = write_format_zip([("1 - A", {"path": png, "to_jpeg": True}), ("1 - A", {"path": png, "to_jpeg": True}),
                          ("2 - B", {"path": None, "reason": "no raster image of this board exists"})], "jpg", out)
    assert s["files"] == 2 and len(s["missing"]) == 1
    z = zipfile.ZipFile(out)
    assert sorted(z.namelist()) == ["1 - A (2).jpg", "1 - A.jpg", MISSING_NOTE]
    assert all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist())
    with Image.open(io.BytesIO(z.read("1 - A.jpg"))) as im:
        assert im.format == "JPEG" and im.getpixel((5, 5)) == (255, 255, 255)
    assert "2 - B.jpg - no raster image" in z.read(MISSING_NOTE).decode()
