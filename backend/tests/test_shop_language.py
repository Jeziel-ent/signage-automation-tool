"""Per-shop Language (Both / English Only / Tamil Only): which shop-name line(s) the board keeps."""
from __future__ import annotations

import pytest

from app.engines import CorelEngine, shop_language
from app.layout import Obj, Placed
from tests.test_main_v2 import _fake_cdr_bytes, client  # noqa: F401  (fixture re-export)
from tests.test_shop_name_binding import TAMIL, _Shape


class _Del(_Shape):
    deleted = False

    def Delete(self):
        self.deleted = True


def _pair(stacked=True):
    en = _Del(100, 134, 800, 30, text="SRI KANNIYAMMAN")
    ta = _Del(100, 57, 800, 42, text=TAMIL) if stacked else _Del(1000, 134, 700, 30, text=TAMIL)
    objs = {"0": Obj("0", "a", "text", 100, 134, 800, 30, "SRI KANNIYAMMAN"),
            "1": Obj("1", "b", "text", ta.LeftX, ta.BottomY, ta.SizeWidth, ta.SizeHeight, TAMIL)}
    placed = [Placed("0", "a", "shopname", 0, 0, 0, 0), Placed("1", "b", "shopname", 0, 0, 0, 0),
              Placed("2", "c", "logo", 0, 0, 0, 0)]
    return en, ta, objs, placed


def test_english_only_removes_the_tamil_line_and_centres_the_english_one():
    en, ta, objs, placed = _pair()
    assert CorelEngine._apply_language("en", placed, objs, {"0": en, "1": ta}, []) == 1
    assert ta.deleted
    assert not en.deleted
    assert en.BottomY + en.SizeHeight / 2 == pytest.approx((57 + 164) / 2)     # centred on the old two-line block


def test_tamil_only_removes_the_english_line():
    en, ta, objs, placed = _pair()
    assert CorelEngine._apply_language("ta", placed, objs, {"0": en, "1": ta}, []) == 1
    assert en.deleted
    assert not ta.deleted
    assert ta.BottomY + ta.SizeHeight / 2 == pytest.approx((57 + 164) / 2)


def test_side_by_side_kept_line_is_centred_horizontally():
    en, ta, objs, placed = _pair(stacked=False)                # English 100..900, Tamil 1000..1700 on one row
    CorelEngine._apply_language("ta", placed, objs, {"0": en, "1": ta}, [])
    assert en.deleted
    assert ta.BottomY == 134  # same row
    assert ta.LeftX + ta.SizeWidth / 2 == pytest.approx((100 + 1700) / 2)


def test_both_and_missing_line_change_nothing():
    en, ta, objs, placed = _pair()
    assert CorelEngine._apply_language("both", placed, objs, {"0": en, "1": ta}, []) == 0
    w = []
    assert CorelEngine._apply_language("ta", placed[:1], objs, {"0": en}, w) == 0        # no Tamil line to keep
    assert not en.deleted
    assert w


def test_tamil_only_without_a_tamil_name_falls_back_to_english():
    w = []
    assert shop_language({"language": "ta"}, w) == "en"
    assert w
    assert shop_language({"language": "ta", "shop_name_local": TAMIL}) == "ta"
    assert shop_language({}) == "both"
    assert shop_language({"language": "xx"}) == "both"


def test_nested_lines_follow_the_language():
    en = _Shape(100, 134, 800, 29, text="SRI KANNIYAMMAN")
    ta = _Del(100, 57, 800, 42, text=TAMIL)
    group = _Shape(0, 0, 1000, 1000, kids=[en, ta])
    shop = {"name": "ANISH STORES", "language": "en", "master_shop_name": "SRI KANNIYAMMAN"}
    CorelEngine._replace_nested_shopnames([group], shop, [])
    assert en.Text.Story.Text == "ANISH STORES"
    assert ta.deleted


def test_api_stores_and_passes_the_language(client):
    job = client.post("/api/v2/upload", data={"brand": "Adinn"},
                      files={"master": ("m.cdr", _fake_cdr_bytes(), "application/octet-stream")}).json()["id"]
    shop = client.post(f"/api/v2/jobs/{job}/shops", json={"name": "K", "width": 6, "height": 4, "unit": "ft", "language": "en"}).json()
    assert shop["language"] == "en"
    import app.main as main
    assert main._convert_job(shop["id"])[0]["shop"]["language"] == "en"
    r = client.patch(f"/api/v2/shops/{shop['id']}", json={"language": "both"})
    assert r.status_code == 200
    assert r.json()["language"] is None
    assert "language" not in main._convert_job(shop["id"])[0]["shop"]
    assert client.patch(f"/api/v2/shops/{shop['id']}", json={"language": "fr"}).status_code == 400
