"""English / Tamil shop-name fonts: the installed-font API with Tamil detection, the per-shop choice reaching the engine, and
the engine setting it on the replaced texts. Also the Excel sheet name stored per shop."""
from __future__ import annotations

import pytest

from app import engines, fonts, main
from app.layout import TAMIL_FONT, Placed
from tests.test_dual_master import _upload
from tests.test_main_v2 import client  # noqa: F401


def test_classify_tamil_uses_the_font_file_and_keeps_legacy_fonts_apart(monkeypatch):
    has = {"a.ttf": True, "b.ttf": False, "c.ttf": False}
    monkeypatch.setattr(fonts, "font_has_tamil", lambda path: has.get(path))
    files = {"Nirmala UI": "a.ttf", "Arial": "b.ttf", "Bamini": "c.ttf"}
    tamil, legacy, how = fonts.classify_tamil(["Arial", "Bamini", "Nirmala UI"], files.get)
    assert (tamil, legacy, how) == (["Nirmala UI"], ["Bamini"], "cmap")
    # no readable file: judged by a known Unicode-Tamil family name
    tamil, _, how = fonts.classify_tamil(["Latha", "Verdana"], lambda f: None)
    assert (tamil, how) == (["Latha"], "cmap+names")


def test_font_has_tamil_reads_a_real_font_when_available():
    pytest.importorskip("fontTools")
    import os
    windir = os.environ.get("WINDIR", r"C:\Windows")
    nirmala, arial = os.path.join(windir, "Fonts", "Nirmala.ttc"), os.path.join(windir, "Fonts", "arial.ttf")
    if not (os.path.isfile(nirmala) and os.path.isfile(arial)):
        pytest.skip("Windows fonts not present")
    assert fonts.font_has_tamil(nirmala) is True
    assert fonts.font_has_tamil(arial) is False


def test_v2_fonts_endpoint(client, monkeypatch):
    monkeypatch.setattr(fonts, "_script_cache", None)
    monkeypatch.setattr(fonts, "installed_fonts", lambda refresh=False: {"available": True, "fonts": ["Arial", "Latha"], "source": "test"})
    monkeypatch.setattr(fonts, "_registry_entries", lambda: ([], [""]))
    r = client.get("/api/v2/fonts?refresh=1").json()
    assert r["all_fonts"] == ["Arial", "Latha"] and r["english_fonts"] == ["Arial", "Latha"] and r["tamil_fonts"] == ["Latha"]
    monkeypatch.setattr(fonts, "_script_cache", None)


def test_replaced_names_keep_the_master_font_unless_explicitly_overridden():
    assert engines.name_font("SRI KUMAR", {}) is None                         # keep the master's font
    assert engines.name_font("ஸ்ரீ குமார்", {}) is None                        # ... also for Tamil (no forced Nirmala UI)
    shop = {"font_en": "Arial Black", "font_ta": "Latha"}                     # an explicit API override still works
    assert engines.name_font("SRI KUMAR", shop) == "Arial Black"
    assert engines.name_font("ஸ்ரீ குமார்", shop) == "Latha"
    placed = [Placed("1", "t", "shopname", 0, 0, 1, 1, text="SRI KUMAR"), Placed("2", "t", "shopname", 0, 0, 1, 1, text="ஸ்ரீ", font=TAMIL_FONT),
              Placed("3", "t", "text", 0, 0, 1, 1, text="Phone No. 1")]
    engines.apply_name_fonts(placed, {})
    assert [p.font for p in placed] == [None, None, None]     # layout's Tamil default is dropped: only the text changes


def test_tamil_guard_keeps_a_tamil_capable_master_font(monkeypatch):
    from app import corel_util
    monkeypatch.setattr(corel_util, "_tamil_capable", {"arima madurai black": True, "arial": False})

    class Story:
        def __init__(self, font):
            self._f = font

        Font = property(lambda s: s._f, lambda s, v: setattr(s, "_f", v))

    class Shape:
        def __init__(self, font):
            self.Text = type("T", (), {"Story": Story(font)})()

    keep, fix, w = Shape("Arima Madurai Black"), Shape("Arial"), []
    corel_util.ensure_tamil_font_renders(keep, "ஸ்ரீ குமார்", w)
    corel_util.ensure_tamil_font_renders(fix, "ஸ்ரீ குமார்", w)
    assert keep.Text.Story.Font == "Arima Madurai Black"      # the master's Tamil font is preserved
    assert fix.Text.Story.Font == TAMIL_FONT                   # a font with no Tamil letters would print tofu boxes


def test_queue_fonts_are_not_forwarded_to_the_engine(client):
    """Rows keep font columns from the old queue pickers, but a conversion never sends them: master fonts are kept."""
    job = _upload(client)["id"]
    shop = client.post(f"/api/v2/jobs/{job}/shops", json={
        "name": "Sri Kumar", "shop_name_local": "ஸ்ரீ குமார்", "width": 10, "height": 4, "unit": "ft",
        "font_en": "Arial Black", "font_ta": "Latha", "sheet_name": "Chennai"}).json()
    assert shop["sheet_name"] == "Chennai"
    spec = main._convert_job(shop["id"])[0]["shop"]
    assert "font_en" not in spec and "font_ta" not in spec and spec["shop_name_local"] == "ஸ்ரீ குமார்"
