"""The free-form "Create Print File" sheet: renderer, totals and POST /api/print-file/generate."""
from __future__ import annotations

import io
import json
import re
import zipfile

import pytest

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from app import print_file  # noqa: E402
from tests.test_main_v2 import client  # noqa: E402,F401


def _png(color=(10, 120, 200), size=(300, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _cdr(with_preview=True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if with_preview:
            z.writestr("previews/page1.png", _png((200, 40, 40)))
        else:
            z.writestr("content/root.dat", b"x")
    return buf.getvalue()


def _spec(**kw):
    base = {"title": "Acme - ACP board", "project_no": "P-1", "date": "2026-10-06", "lines": ["MDU"], "format": "jpeg",
            "sections": [{"name": "ACP BOARD", "items": [
                {"file": 0, "name": "Shop A", "width": 10, "height": 4, "unit": "ft", "type": "ACP", "no": 1},
                {"file": None, "name": "Shop B", "width": 24, "height": 12, "unit": "in", "no": 2, "qty": 3}]}]}
    base.update(kw)
    return base


def _post(client, spec, files):
    return client.post("/api/print-file/generate", data={"spec": json.dumps(spec)},
                       files=[("files", (n, io.BytesIO(b), "application/octet-stream")) for n, b in files])


def test_section_totals_multiply_by_qty_and_overrides_win():
    items = [print_file.Item("A", 10, 4, "ft", qty=2), print_file.Item("B", 12, 12, "in")]
    assert print_file.section_totals(print_file.Section("S", items)) == (3, pytest.approx(2 * 40 + 1))
    assert print_file.section_totals(print_file.Section("S", items, qty_override=165, sqft_override=900.0)) == (165, 900.0)


def test_pages_are_a4_and_overflow_moves_to_the_next_page():
    few = print_file.render_pages(print_file.FileMeta(title="T"), [print_file.Section("S", [print_file.Item("A", 1, 1)] * 3)])
    many = print_file.render_pages(print_file.FileMeta(title="T"), [print_file.Section("S", [print_file.Item("A", 1, 1)] * 40),
                                                                    print_file.Section("T", [print_file.Item("B", 1, 1)])])
    assert len(few) == 1 and len(many) > 1
    assert all(p.size == (2480, 3508) for p in few + many)


def test_render_is_2480_wide_and_has_red_bar(tmp_path):
    img = print_file.render(print_file.FileMeta(title="T", date="06.10.2026"), [print_file.Section("ACP", [print_file.Item("A", 10, 4)])])
    assert img.width == 2480
    assert img.getpixel((20, 20)) == print_file.ps.RED            # the red PRINT DETAILS block


def test_render_needs_an_item():
    with pytest.raises(ValueError):
        print_file.render(print_file.FileMeta(), [print_file.Section("S", [])])


def test_many_items_make_a_multi_page_pdf_and_a_zip_of_jpegs(client):
    spec = _spec()
    spec["sections"][0]["items"] = [{"file": None, "name": f"Shop {i}", "width": 10, "height": 4, "unit": "ft"} for i in range(40)]
    r = _post(client, {**spec, "format": "pdf"}, [])
    assert r.status_code == 200 and len(re.findall(rb"/Type /Page(?![a-z])", r.content)) >= 2
    r = _post(client, {**spec, "format": "jpeg"}, [])
    assert r.headers["content-type"] == "application/zip"
    assert len(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) >= 2


def test_generate_jpeg_and_pdf_from_image_and_cdr(client):
    r = _post(client, _spec(), [("a.png", _png())])
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert "Print_File_P-1_06.10.2026.jpg" in r.headers["content-disposition"]
    assert Image.open(io.BytesIO(r.content)).width == 2480
    spec = _spec(format="pdf")
    spec["sections"][0]["items"][0]["file"] = 0
    r = _post(client, spec, [("master.cdr", _cdr())])
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_cdr_without_preview_still_renders(client):
    assert _post(client, _spec(), [("old.cdr", _cdr(False))]).status_code == 200


@pytest.mark.parametrize("mutate,code", [
    (lambda s: s.update(format="gif"), 422),
    (lambda s: s.update(sections=[]), 422),
    (lambda s: s["sections"][0]["items"][0].update(width=0), 422),
    (lambda s: s["sections"][0]["items"][0].update(unit="yd"), 422),
    (lambda s: s["sections"][0]["items"][0].update(file=5), 422),
    (lambda s: s["sections"][0].update(qty="abc"), 422),
])
def test_generate_validation(client, mutate, code):
    spec = _spec()
    mutate(spec)
    assert _post(client, spec, [("a.png", _png())]).status_code == code


def test_generate_rejects_other_file_types_and_bad_json(client):
    assert _post(client, _spec(), [("a.exe", b"MZ")]).status_code == 422
    r = client.post("/api/print-file/generate", data={"spec": "not json"})
    assert r.status_code == 422
