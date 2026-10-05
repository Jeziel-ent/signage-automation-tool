"""Text formatting (align, bold/italic/underline, line + letter spacing): read from CorelDRAW by the scene export, replayed
onto CorelDRAW's Story on export with read-back warnings and the text kept where the editor shows it."""
from __future__ import annotations

from app import export_replay as er
from app import scene_export
from tests.test_export_replay import fresh, ids_of, run


class FmtStory:
    """CorelDRAW's Story as far as formatting goes (enum values from the v21 typelib: Right = 2, Center = 3)."""

    def __init__(self, shape, text="SHOP", bold_face=True):
        self._shape, self.Text, self.Font, self.Size = shape, text, "Arial", 20.0
        self._align, self._bold, self.Italic, self.Underline = 1, False, False, 0
        self.LineSpacingType, self.LineSpacing, self.CharSpacing = 0, 100.0, 0.0
        self._bold_face = bold_face

    @property
    def Alignment(self):
        return self._align

    @Alignment.setter
    def Alignment(self, v):
        # artistic text is anchored at its insertion point: re-aligning moves the box around it (left -> centre moves it w/2 left)
        shift = {1: 0.0, 3: -0.5, 2: -1.0}
        self._shape._x += (shift.get(v, 0.0) - shift.get(self._align, 0.0)) * self._shape._w
        self._align = v

    @property
    def Bold(self):
        return self._bold

    @Bold.setter
    def Bold(self, v):
        if self._bold_face:                 # a font without a bold face: CorelDRAW ignores it silently
            self._bold = bool(v)


def _text_shape(doc):
    shape = doc.ActivePage._layers[1]._kids[0]
    if not hasattr(type(shape), "RightX"):
        type(shape).RightX = property(lambda s: s._box()[0] + s._box()[2])
    if not hasattr(type(shape), "TopY"):
        type(shape).TopY = property(lambda s: s._box()[1] + s._box()[3])
    return shape


def test_scene_export_reads_the_formatting():
    doc, _ = fresh()
    shape = _text_shape(doc)
    st = FmtStory(shape)
    st._align, st._bold, st.Underline, st.LineSpacing, st.CharSpacing = 3, True, 1, 120.0, -5.0
    assert scene_export.text_format(st) == {"align": "center", "bold": True, "italic": False, "underline": True,
                                            "line_spacing": 120.0, "char_spacing": -5.0}
    st._align, st.LineSpacingType = 6, 1          # mixed alignment, line spacing in points: reported as unknown, not guessed
    fmt = scene_export.text_format(st)
    assert "align" not in fmt
    assert "line_spacing" not in fmt


def test_replay_applies_every_field_and_keeps_the_text_in_place():
    doc, scene = fresh()
    shape = _text_shape(doc)
    shape.Text.Story = FmtStory(shape)
    t = ids_of(scene)[2]["id"]
    left, top = shape.LeftX, shape.TopY
    ops = [{"op": "text", "id": t, "align": "left", "bold": True, "italic": True, "underline": True,
            "line_spacing": 150, "char_spacing": 25}]
    _, r, _, v = run(ops, doc, scene)
    st = shape.Text.Story
    assert (st.Alignment, st.Bold, st.Italic, st.Underline, st.LineSpacing, st.CharSpacing) == (1, True, True, 1, 150.0, 25.0)
    assert r.warnings == [] and v["ok"], (r.warnings, v)
    assert (shape.LeftX, shape.TopY) == (left, top)


def test_right_alignment_keeps_the_right_edge_where_the_editor_shows_it():
    doc, scene = fresh()
    shape = _text_shape(doc)
    shape.Text.Story = FmtStory(shape)
    t = ids_of(scene)[2]["id"]
    right = shape.RightX
    _, r, _, v = run([{"op": "text", "id": t, "align": "right"}], doc, scene)
    assert shape.Text.Story.Alignment == 2                     # cdrRightAlignment (not 3 - that is centre)
    assert abs(shape.RightX - right) < 1e-6 and v["ok"], v


def test_an_ignored_setting_is_reported():
    doc, scene = fresh()
    shape = _text_shape(doc)
    shape.Text.Story = FmtStory(shape, bold_face=False)
    t = ids_of(scene)[2]["id"]
    _, r, _, _ = run([{"op": "text", "id": t, "bold": True}], doc, scene)
    assert any("bold" in w and "not applied" in w for w in r.warnings), r.warnings


def test_unchanged_values_are_not_rewritten():
    doc, scene = fresh()
    shape = _text_shape(doc)
    shape.Text.Story = FmtStory(shape)
    t = ids_of(scene)[2]["id"]
    x = shape.LeftX
    _, r, _, v = run([{"op": "text", "id": t, "align": "left", "bold": False, "line_spacing": 100}], doc, scene)
    assert shape.LeftX == x
    assert r.warnings == []
    assert v["ok"]
