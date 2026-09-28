"""The "Print Details" summary sheet (app/print_sheet.py) and POST /api/print-sheet/generate."""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from PIL import Image

from app import main, print_sheet as ps
from tests.test_dual_master import _upload
from tests.test_main_v2 import client  # noqa: F401
from tests.test_shops_edit import _import_one, _wait

META = ps.SheetMeta("ULTRATECH CEMENT LIMITED - ACP BOARD", "22071195", "26.09.2026", "CHENNAI", "ACP BOARD")


def _shops(n, image=None):
    return [ps.SheetShop(5 + i, f"SHOP {i}", 214, "in", 36, "in", image) for i in range(n)]


# ------------------------------------------------------------------ numbers and captions

def test_totals_match_the_reference_sheet():
    """The reference's three boards: 214x36 + 180x36 + 233.5x53.5 = 26676.25 sq in = 185.25 sq ft, printed "185"."""
    shops = [ps.SheetShop(5, "A", 214, "in", 36, "in"), ps.SheetShop(6, "B", 180, "in", 36, "in"),
             ps.SheetShop(7, "C", 233.5, "in", 53.5, "in")]
    qty, sqft = ps.totals(shops)
    assert qty == 3 and sqft == pytest.approx(26676.25 / 144)
    assert ps.fmt_sqft(sqft) == "185"
    assert ps.sq_feet(10, "ft", 48, "in") == pytest.approx(40.0)            # mixed units
    assert ps.fmt_sqft(2.78) == "2.8" and ps.fmt_sqft(0.0) == "0"


def test_caption_follows_the_reference_format():
    s = ps.SheetShop(5, "SRI KRISHNA ENTERPRISES", 214, "in", 36, "in")
    assert ps.caption(s, "ACP-LIT") == "05 - 214 X 36 Inch - ACP-LIT - SRI KRISHNA ENTERPRISES"
    assert ps.caption(ps.SheetShop(7, "K", 233.5, "in", 53.5, "in"), "ACP-LIT") == "07 - 233.5 X 53.5 Inch - ACP-LIT - K"
    assert ps.caption(ps.SheetShop(12, "K", 10, "ft", 48, "in"), "") == "12 - 10 Feet X 48 Inch - K"   # no board type


def test_date_from_the_picker_is_shown_day_first():
    assert ps.format_date("2026-09-26") == "26.09.2026"
    assert ps.format_date(" 26.09.2026 ") == "26.09.2026"
    assert ps.format_date("") == ""


def test_grid_counts_the_folder_card_and_uses_2_then_3_columns():
    few = ps.plan_grid(6)                                                 # the folder + 5 shops
    assert (few["cols"], few["rows"], few["per_page"], few["box_h"]) == (2, 3, 6, 150.0)
    many = ps.plan_grid(7)
    assert (many["cols"], many["rows"], many["per_page"], many["box_h"]) == (3, 3, 9, 120.0)   # the spec's ~120 pt box
    assert ps.plan_grid(500)["per_page"] == 9
    for g in (few, many):                                                 # inside the side insets and the bottom margin
        assert g["x0"] + g["cols"] * g["card_w"] + (g["cols"] - 1) * ps.GAP_PT <= ps.PAGE_PT[0] - ps.SIDE * ps.K + 0.01
        assert g["top"] + g["rows"] * g["row_h"] - ps.GAP_PT <= ps.PAGE_PT[1] - ps.BOTTOM_PT


def test_captions_wrap_to_two_lines_with_an_ellipsis():
    f = ps._font("caption", 7.5)
    long = "01 - 10 X 4 Inch - ACP BOARD - C. SIVA ANAND ANAND HARDWARES AND ELECTRICALS (ARUMANAI) BRANCH OFFICE TWO"
    lines = ps._wrap(long, f, 150 * ps._P, 2)
    assert len(lines) == 2 and lines[1].endswith("...")
    assert all(f.getlength(line) <= 150 * ps._P for line in lines)
    assert ps._wrap("CDR & PDF", f, 150 * ps._P, 2) == ["CDR & PDF"]


# ------------------------------------------------------------------ rendering

def test_render_is_a4_portrait_in_the_reference_style_with_the_folder_first(tmp_path):
    thumb = tmp_path / "t.png"
    Image.new("RGB", (1600, 640), (20, 160, 120)).save(thumb)
    page = ps.render_pages(META, _shops(3, thumb))[0]
    assert page.size == (ps.PAGE_W, ps.PAGE_H) == (2480, 3508)            # 210 x 297 mm at 300 dpi
    u = lambda x, y: page.getpixel((int(x * ps.K * ps._P), int(y * ps.K * ps._P)))   # noqa: E731 - reference units
    assert u(20, 110) == ps.RED                                           # "PRINT DETAILS" angled block
    assert u(ps.REF_W - 20, 110) == ps.GREY_BAND                          # title band, right end
    assert u(600, 110) != ps.RED and u(600, 20) == ps.RED                 # the angle: red at the top only
    assert u(20, 520) == ps.RED                                           # board-type bar
    assert u(ps.REF_W - 10, 430) == ps.DARK                               # full-width rule
    g = ps.plan_grid(4)
    px = lambda x, y: page.getpixel((int(x * ps._P), int(y * ps._P)))     # noqa: E731 - points
    # card 1 is the folder (yellow), card 2 the first shop's preview (green), bottom-aligned in its box
    folder = px(g["x0"] + g["card_w"] / 2, g["top"] + g["box_h"] - 12)
    assert folder[0] > 230 and folder[2] < 150
    x2 = g["x0"] + g["card_w"] + ps.GAP_PT + g["card_w"] / 2
    assert px(x2, g["top"] + g["box_h"] - 3) == (20, 160, 120)
    assert px(x2, g["top"] + 3) == ps.WHITE                               # a wide preview leaves the box top empty


def test_missing_preview_draws_a_placeholder_not_an_error():
    page = ps.render_pages(META, _shops(2, None))[0]
    assert page.size == (ps.PAGE_W, ps.PAGE_H)


def test_many_shops_continue_on_further_pages():
    n = ps.plan_grid(500)["per_page"]                 # with the folder card, this many shops needs a second page
    assert len(ps.render_pages(META, _shops(n))) == 2
    assert len(ps.render_pages(META, _shops(n - 1))) == 1
    with pytest.raises(ValueError):
        ps.render_pages(META, [])


def test_write_pdf_jpeg_and_multi_page_jpeg_zip(tmp_path):
    p, media = ps.write_sheet(META, _shops(2), "pdf", tmp_path / "a")
    assert media == "application/pdf" and p.read_bytes()[:5] == b"%PDF-"
    p, media = ps.write_sheet(META, _shops(2), "jpeg", tmp_path / "b")
    assert media == "image/jpeg"
    with Image.open(p) as im:
        assert im.size == (ps.PAGE_W, ps.PAGE_H) and round(im.info["dpi"][0]) == ps.DPI
    n = ps.plan_grid(500)["per_page"] + 1
    p, media = ps.write_sheet(META, _shops(n), "jpeg", tmp_path / "c")
    assert media == "application/zip" and len(zipfile.ZipFile(p).namelist()) == 2
    with pytest.raises(ValueError):
        ps.write_sheet(META, _shops(1), "gif", tmp_path / "d")


# ------------------------------------------------------------------ API

def _done_shop(client, job, w=214, h=36):
    shop = _import_one(client, job, w, h)
    client.post(f"/api/v2/shops/{shop['id']}/convert", json={})
    assert _wait(client, shop["id"])["status"] == "done"
    return shop


def test_generate_returns_a_download_for_the_selected_done_shops(client):
    job = _upload(client)["id"]
    a, b = _done_shop(client, job), _done_shop(client, job, 180, 36)
    body = {"title": "Ultratech - ACP Board", "project_no": "22071195", "date": "2026-09-26", "location": "Chennai",
            "board_type": "ACP BOARD", "shop_ids": [a["id"], b["id"], a["id"]], "numbers": {a["id"]: 5, b["id"]: 6},
            "format": "jpeg"}
    r = client.post("/api/print-sheet/generate", json=body)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert 'filename="Print_Details_22071195_26.09.2026.jpg"' in r.headers["content-disposition"]
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (ps.PAGE_W, ps.PAGE_H)
    r = client.post("/api/print-sheet/generate", json={**body, "format": "pdf"})
    assert r.status_code == 200 and r.content[:5] == b"%PDF-"


def test_generate_validates_the_request(client):
    job = _upload(client)["id"]
    done = _done_shop(client, job)
    pending = _import_one(client, job)
    post = lambda **kw: client.post("/api/print-sheet/generate", json={"shop_ids": [done["id"]], **kw})  # noqa: E731
    assert post(shop_ids=[]).status_code == 422
    assert post(format="gif").status_code == 422
    assert post(shop_ids=["nope"]).status_code == 404
    r = post(shop_ids=[done["id"], pending["id"]])
    assert r.status_code == 409 and "not been converted" in r.json()["detail"]


def test_thumbnail_prefers_the_latest_editor_export(client, tmp_path, monkeypatch):
    job = _upload(client)["id"]
    shop = _done_shop(client, job)
    row = main.db.get_shop(shop["id"])
    assert main._shop_thumbnail(row) is None or main._shop_thumbnail(row).suffix.lower() in (".png", ".jpg")
    out = main.JOBS_V2 / job / "out" / shop["id"] / "exports" / "ex1"
    out.mkdir(parents=True)
    Image.new("RGB", (40, 10)).save(out / "board.png")
    monkeypatch.setattr(main.db, "list_exports", lambda sid, limit=10: [
        {"id": "ex0", "status": "failed", "files_json": None},
        {"id": "ex1", "status": "done", "files_json": json.dumps({"cdr": "board.cdr", "png": "board.png"})}])
    assert main._shop_thumbnail(row) == out / "board.png"


def test_without_pillow_the_server_still_starts_and_the_route_says_why(client, monkeypatch):
    """print_sheet (and so Pillow) is imported inside the route, like every other Pillow use in the app: on a Python
    without Pillow the server still starts and only this route fails - with a 503 naming the fix."""
    import sys

    import app as app_pkg

    assert "print_sheet" not in vars(main)                        # not a module-level import of main.py
    monkeypatch.setitem(sys.modules, "app.print_sheet", None)     # `import app.print_sheet` raises ImportError...
    monkeypatch.delattr(app_pkg, "print_sheet", raising=False)    # ...and `from . import` cannot fall back to the attribute
    r = client.post("/api/print-sheet/generate", json={"shop_ids": ["x"]})
    assert r.status_code == 503 and "Pillow" in r.json()["detail"] and "pip install" in r.json()["detail"]
