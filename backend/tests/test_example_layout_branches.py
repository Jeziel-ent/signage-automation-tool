"""Branches of app/example_layout.py not reached by the board-copy tests: names kept clear of pictures (every side/half), the white name
panel of a centre-panel design, and a retyped English name that must ignore tiny stray text. Pure functions on synthetic data."""
import copy

import pytest

from app import example_layout as X


def _line(anchor, ax, w_cap=1000.0, cy=500.0, h=100.0):
    return {"anchor": anchor, "ax": ax, "w_cap": w_cap, "cy": cy, "h": h}


NEW_W, NEW_H = 3000.0, 1000.0
GAP = X.PICTURE_GAP_FRAC * NEW_W            # 30


def _clear(line, box):
    texts = {"t": copy.deepcopy(line)}
    X._clear_of_pictures(texts, {"p": box}, NEW_W, NEW_H)
    return texts["t"]


def test_left_anchored_line_is_shortened_by_a_picture_in_its_far_half():
    t = _clear(_line("left", 100), (900, 450, 100, 100))      # span 100..1100, picture at 900..1000 (right of the middle)
    assert t["ax"] == 100
    assert t["w_cap"] == pytest.approx(900 - GAP - 100)


def test_left_anchored_line_moves_past_a_picture_over_its_anchor():
    t = _clear(_line("left", 100), (150, 450, 100, 100))      # picture 150..250 sits over the anchored edge
    assert t["ax"] == pytest.approx(150 + 100 + GAP)
    assert t["w_cap"] == pytest.approx(1000 - (150 + 100 + GAP - 100))


def test_right_anchored_line_is_shortened_by_a_picture_in_its_far_half():
    t = _clear(_line("right", 2900), (1950, 450, 100, 100))   # span 1900..2900, picture at 1950..2050 (left of the middle)
    assert t["ax"] == 2900
    assert t["w_cap"] == pytest.approx(2900 - (1950 + 100 + GAP))


def test_right_anchored_line_moves_past_a_picture_over_its_anchor():
    t = _clear(_line("right", 2900), (2800, 450, 100, 100))   # picture 2800..2900 sits over the anchored edge
    assert t["ax"] == pytest.approx(2800 - GAP)
    assert t["w_cap"] == pytest.approx(1000 - (2900 - (2800 - GAP)))


def test_far_half_shortening_never_goes_below_a_fifth_of_the_line():
    t = _clear(_line("left", 100), (110, 450, 10, 100))       # picture right at the start: a tiny room left
    assert t["w_cap"] >= 0.2 * 1000 - 1e-9 or t["ax"] > 100  # either clamped to 20 % or the anchor moved past it


@pytest.mark.parametrize(
    "line, box",
    [
        (_line("centre", 1500), (1450, 450, 100, 100)),        # a centred line sits between pictures by design
        (_line("left", 100), (900, 800, 100, 100)),            # a picture on other rows
        (_line("left", 100), (2000, 450, 100, 100)),           # clear of the span
        (_line("left", 100), (0, 0, 2000, 1000)),              # backdrop-sized (>= half the page) is ignored
        (_line("left", 100), (900, 450, 100, 0)),              # a zero-height box
    ],
)
def test_other_cases_leave_the_line_alone(line, box):
    assert _clear(line, box) == line


# ---- the white name panel of a centre-panel design, and a retyped English name next to stray tiny text -------------------------------------

def _shape(type_, x, y, w, h, gp=None, **kw):
    return {"type": type_, "name": "", "x": x, "y": y, "w": w, "h": h, "group_path": gp or [], **kw}


def _dump(W, H, els, texts=(), clip=()):
    sh = [_shape("rectangle", 0, 0, W, H)]
    for t in texts:
        sh.append(_shape("text", t[1], t[2], t[3], t[4], text=t[0]))
    for e in els:
        sh.append(_shape(e[0], *e[1:5]))
        for _ in range(e[5] if len(e) > 5 else 0):
            sh.append(_shape("curve", e[1], e[2], 1, 1, gp=["g"]))
    for c in clip:
        sh.append(_shape(c[0], *c[1:5], gp=["rect<clip>"], clip=True))
    return {"file": "x.cdr", "page_mm": {"w": W, "h": H}, "shapes": sh}


ELS = [("group", 100, 600, 600, 300, 4), ("group", 1500, 300, 900, 600, 6), ("bitmap", 2700, 100, 150, 700)]
CLIP = [("bitmap", -100, -50, 3200, 1100), ("group", -50, -300, 3100, 500), ("group", 100, 150, 1200, 700)]


def _master():
    d = _dump(3000, 1000, ELS, texts=[("MASTER SHOP", 100, 50, 1000, 100), ("மாஸ்டர்", 1500, 50, 800, 90)], clip=CLIP)
    return X.describe_master(d, {1, 2})


def test_a_full_height_rectangle_in_the_page_clip_is_recorded_as_the_name_panel():
    texts = [("NEW SHOP", 100, 50, 1000, 100), ("நியூ", 1500, 50, 800, 90)]
    panel = ("rectangle", 900, 0, 1200, 1000)                    # full height, 40 % of the width: the white panel
    rec = X.board_record(_master(), _dump(3000, 1000, ELS, texts=texts, clip=CLIP + [panel]), "NEW SHOP", "p.cdr")
    p = rec["clip"]["panel"]
    assert (p["cx"], p["cy"], p["w"], p["h"]) == (0.5, 0.5, 0.4, 1.0)         # centred, 40 % wide, the full height
    no_panel = X.board_record(_master(), _dump(3000, 1000, ELS, texts=texts, clip=CLIP), "NEW SHOP", "q.cdr")
    assert "panel" not in no_panel["clip"]


def test_tiny_stray_text_is_not_taken_for_the_retyped_english_name():
    texts = [("POOJA STORE", 100, 50, 1000, 100), ("நியூ", 1500, 50, 800, 90),
             ("SMALL TXT", 2500, 900, 100, 20)]                 # 2000 mm2 < 0.2 % of the page: ignored, not chosen
    rec = X.board_record(_master(), _dump(3000, 1000, ELS, texts=texts, clip=CLIP), "NATTU MARUNDHU KADAI", "b.cdr")
    assert set(rec["texts"]) == {"name_ta", "name_en"}
    assert rec["texts"]["name_en"]["w"] == pytest.approx(1000 / 3000, abs=1e-3)   # the real line, not the 100 mm stray text
