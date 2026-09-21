"""CorelEngine._ensure_tamil_font_renders (app/engines.py) - the fix for a
real tofu-box bug found live: a real Agarpathi job (job 43ddf0d9e704 / shop
638555963892) that never requested shop-name/contact replacement left its
master's own Tamil shopname text untouched, still tagged "Arial" (Windows
font-linking normally substitutes a real Tamil font at render time for
this - see CLAUDE.md "Shop name replacement" - but that substitution did
not happen for this export path on this machine), and it rendered as tofu
boxes in both the exported PNG and the editor's scene-export slice.

Pure Python against a minimal fake COM shape - no CorelDRAW needed, same
"model exactly the verified behaviour" approach test_export_replay.py
uses (CorelDRAW silently keeps a shape's old font if the one just set
isn't "installed", never raises).
"""
from __future__ import annotations

from app.engines import CorelEngine
from app.layout import TAMIL_FONT

TAMIL_TEXT = "அல் மதீனா"  # "அல் மதீனா" (Tamil)
ENGLISH_TEXT = "AL MADEENA POOJA STORE"


class FakeStory:
    def __init__(self, font, installed):
        self._font = font
        self._installed = installed

    @property
    def Font(self):
        return self._font

    @Font.setter
    def Font(self, v):
        if v in self._installed:  # CorelDRAW silently keeps the old font otherwise
            self._font = v


class FakeShape:
    def __init__(self, font, installed=(TAMIL_FONT,)):
        self.Text = type("T", (), {"Story": FakeStory(font, installed)})()


def test_leaves_non_tamil_text_alone():
    shape = FakeShape(font="Arial")
    warnings = []
    CorelEngine._ensure_tamil_font_renders(shape, ENGLISH_TEXT, warnings)
    assert shape.Text.Story.Font == "Arial"
    assert warnings == []


def test_leaves_already_good_tamil_font_alone():
    shape = FakeShape(font="Nirmala UI")
    warnings = []
    CorelEngine._ensure_tamil_font_renders(shape, TAMIL_TEXT, warnings)
    assert shape.Text.Story.Font == "Nirmala UI"
    assert warnings == []


def test_leaves_nirmala_text_alone_too():
    # "Nirmala Text" is the other family member already trusted - see
    # CorelEngine._KNOWN_TAMIL_FONTS.
    shape = FakeShape(font="Nirmala Text", installed=("Nirmala Text",))
    warnings = []
    CorelEngine._ensure_tamil_font_renders(shape, TAMIL_TEXT, warnings)
    assert shape.Text.Story.Font == "Nirmala Text"
    assert warnings == []


def test_fixes_tamil_text_tagged_arial_to_the_tamil_font():
    # The exact real-world case: Windows font-linking normally makes "Arial"
    # -tagged Tamil content render fine, but that can't be relied on (see
    # module docstring) - this is the fix when it doesn't.
    shape = FakeShape(font="Arial", installed=(TAMIL_FONT,))
    warnings = []
    CorelEngine._ensure_tamil_font_renders(shape, TAMIL_TEXT, warnings)
    assert shape.Text.Story.Font == TAMIL_FONT
    assert warnings == []


def test_warns_without_crashing_when_the_font_does_not_stick():
    # CorelDRAW never raises for an unrecognized font - it silently keeps
    # the old one (see CLAUDE.md) - so if TAMIL_FONT itself isn't installed
    # on some machine, this must warn rather than silently claim success.
    shape = FakeShape(font="Arial", installed=())  # nothing is "installed"
    warnings = []
    CorelEngine._ensure_tamil_font_renders(shape, TAMIL_TEXT, warnings)
    assert shape.Text.Story.Font == "Arial"  # unchanged - the write didn't stick
    assert len(warnings) == 1
    assert "tofu" in warnings[0]


def test_swallows_exceptions_from_a_shape_with_no_readable_font():
    # e.g. a text run mixing fonts/sizes internally raises reading Story.Font
    # (see dump_objects.py's own _text_info handling of this) - must not crash.
    class BrokenShape:
        @property
        def Text(self):
            raise RuntimeError("mixed font run")

    warnings = []
    CorelEngine._ensure_tamil_font_renders(BrokenShape(), TAMIL_TEXT, warnings)
    assert len(warnings) == 1


def test_none_and_empty_text_are_not_tamil():
    shape = FakeShape(font="Arial")
    warnings = []
    CorelEngine._ensure_tamil_font_renders(shape, None, warnings)
    CorelEngine._ensure_tamil_font_renders(shape, "", warnings)
    assert shape.Text.Story.Font == "Arial"
    assert warnings == []
