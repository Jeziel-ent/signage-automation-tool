"""Stage 2 typography: a family registered through one named instance (variable fonts) is recognised, the brand's Tamil font is used when the master's cannot
draw Tamil, nothing is forced onto a master font that already can, a missing font is reported - all with fake registry / fake COM objects (no CorelDRAW)."""
from __future__ import annotations

import pytest

from app import corel_util, fonts, typography


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    corel_util._tamil_capable.clear()
    corel_util.set_tamil_preference(None)
    monkeypatch.delenv("SIGNAGE_TYPOGRAPHY_V2", raising=False)
    yield
    corel_util._tamil_capable.clear()
    corel_util.set_tamil_preference(None)


ENTRIES = [("Anek Tamil Medium (TrueType)", r"C:\Users\x\AppData\Local\Microsoft\Windows\Fonts\ANEKTAMIL-VARIABLEFONT_WDTH,WGHT.TTF"),
           ("Nirmala UI (TrueType)", "Nirmala.ttc"), ("Arial (TrueType)", "arial.ttf"), ("Arial Bold (TrueType)", "arialbd.ttf"), ("Segoe UI Semilight (TrueType)", "segoeuisl.ttf")]


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(fonts, "sys", type("S", (), {"platform": "win32"})(), raising=False)
    monkeypatch.setattr(fonts, "_registry_entries", lambda: (ENTRIES, [r"C:\Windows\Fonts"]))
    monkeypatch.setattr(fonts, "font_has_tamil", lambda p: "ANEKTAMIL" in str(p).upper() or "NIRMALA" in str(p).upper())
    monkeypatch.setattr(fonts, "installed_fonts", lambda refresh=False: {"available": True, "fonts": ["Anek Tamil", "Nirmala UI", "Arial", "Segoe UI"], "source": "test"})


def test_resolve_font_file_cannot_find_a_variable_font_by_its_family_name(registry):
    """The root cause of 'Anek Tamil -> Nirmala UI' on every Hangyo board."""
    assert fonts.resolve_font_file("Anek Tamil", ENTRIES, r"C:\Windows\Fonts") is None
    assert fonts.resolve_font_file("Anek Tamil Medium", ENTRIES, r"C:\Windows\Fonts") is not None


def test_family_files_finds_every_face_of_a_family(registry):
    assert [p.endswith("ANEKTAMIL-VARIABLEFONT_WDTH,WGHT.TTF") for p in fonts.family_files("Anek Tamil")] == [True]
    assert len(fonts.family_files("Arial")) == 2 and fonts.family_files("Amaranth") == []
    assert fonts.family_files("Segoe UI")                                  # a family registered only through a "Semilight" face


def test_font_renders_tamil_recognises_the_family_when_typography_v2_is_on(registry, monkeypatch):
    assert corel_util.font_renders_tamil("Anek Tamil") is True
    assert corel_util.font_renders_tamil("Arial") is False and corel_util.font_renders_tamil("Amaranth") is False
    corel_util._tamil_capable.clear()
    monkeypatch.setenv("SIGNAGE_TYPOGRAPHY_V2", "0")
    assert corel_util.font_renders_tamil("Anek Tamil") is False             # the pre-Stage-2 behaviour, reproducible for evaluation


class Story:
    def __init__(self, font, accepts=("Anek Tamil", "Nirmala UI", "Arial")):
        self._font, self.accepts = font, set(accepts)

    @property
    def Font(self):
        return self._font

    @Font.setter
    def Font(self, v):
        if v in self.accepts:
            self._font = v                              # CorelDRAW keeps the old font silently for a name it does not know


class Shape:
    def __init__(self, story):
        self.Text = type("T", (), {"Story": story})()


def test_a_master_font_that_already_draws_tamil_is_kept(registry):
    st = Story("Anek Tamil")
    w: list[str] = []
    corel_util.ensure_tamil_font_renders(Shape(st), "ஸ்ரீ சாய்", w)
    assert st.Font == "Anek Tamil" and not w                                # before Stage 2 this was forced to Nirmala UI


def test_a_font_without_tamil_gets_the_brand_font_then_the_legacy_default(registry):
    corel_util.set_tamil_preference("Anek Tamil")
    st = Story("Amaranth", accepts=("Anek Tamil", "Nirmala UI"))
    w: list[str] = []
    corel_util.ensure_tamil_font_renders(Shape(st), "ஸ்ரீ சாய்", w)
    assert st.Font == "Anek Tamil" and any("brand's Tamil font" in x for x in w)
    st2 = Story("Amaranth", accepts=("Nirmala UI",))                           # the brand font does not stick -> the legacy default
    corel_util.ensure_tamil_font_renders(Shape(st2), "ஸ்ரீ சாய்", [])
    assert st2.Font == "Nirmala UI"
    corel_util.set_tamil_preference(None)
    st3 = Story("Amaranth", accepts=("Anek Tamil", "Nirmala UI"))
    corel_util.ensure_tamil_font_renders(Shape(st3), "ஸ்ரீ சாய்", [])
    assert st3.Font == "Nirmala UI"                                           # no brand preference: exactly the old behaviour


def test_english_text_is_never_touched(registry):
    corel_util.set_tamil_preference("Anek Tamil")
    st = Story("Amaranth")
    corel_util.ensure_tamil_font_renders(Shape(st), "SRI SAI CAFE", [])
    assert st.Font == "Amaranth"


def test_brand_typography_only_for_brands_that_have_it_and_when_installed(registry, monkeypatch):
    assert typography.tamil_font("hangyo") == "Anek Tamil" and typography.tamil_font("dalmia") is None and typography.tamil_font(None) is None
    monkeypatch.setattr(typography, "is_installed", lambda f: False)
    assert typography.tamil_font("hangyo") is None                              # not installed here: the legacy default applies
    monkeypatch.setenv("SIGNAGE_TYPOGRAPHY_V2", "0")
    monkeypatch.setattr(typography, "is_installed", lambda f: True)
    assert typography.tamil_font("hangyo") is None


def test_font_status_reports_missing_fonts_and_never_claims_equivalence(registry):
    rep = typography.font_status("hangyo", {"Amaranth": 2, "Anek Tamil": 1})
    assert rep["fonts_used"]["Amaranth"]["installed"] is False and rep["fonts_used"]["Anek Tamil"]["draws_tamil"] is True
    assert rep["missing_on_this_machine"] == ["Amaranth"] and rep["designer_equivalent"] is False and "NOT designer-equivalent" in rep["note"]
    ok = typography.font_status("hangyo", {"Anek Tamil": 1, "Arial": 1})
    assert ok["missing_on_this_machine"] == [] and ok["brand_intended"] == {"tamil": "Anek Tamil", "english": "Amaranth"}
    plain = typography.font_status("dalmia", {"Arial": 1})
    assert plain["designer_equivalent"] is False and plain["note"] == "no brand typography configured"


def test_the_brand_file_is_valid_and_isolated():
    spec = typography.load("hangyo")
    assert spec["tamil"]["family"] == "Anek Tamil" and spec["english"]["family"] == "Amaranth"
    assert typography.load("nonexistent") is None
