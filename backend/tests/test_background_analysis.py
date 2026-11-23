from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from app import background_analysis as ba, master_model as mm
from spatial_fixtures import build


def _render(tmp_path, W=1524, H=1219):
    """The sign_10x8 master drawn as a PNG: blue page, white card behind the shop name, black text bar, brown photo."""
    a = np.zeros((H, W, 3), np.uint8)
    a[:] = (20, 40, 160)                                           # page blue
    def px(x, y, w, h):                                              # mm -> pixel box (origin flip)
        s = W / 3048.0
        return int(x * s), int((2438.4 - (y + h)) * s), int((x + w) * s), int((2438.4 - y) * s)
    x0, y0, x1, y1 = px(900, 1100, 1800, 500); a[y0:y1, x0:x1] = (255, 255, 255)        # white card
    x0, y0, x1, y1 = px(1000, 1200, 1600, 300); a[y0:y1, x0:x1] = (255, 255, 255)
    x0, y0, x1, y1 = px(1100, 1280, 1400, 40); a[y0:y1, x0:x1] = (10, 10, 10)            # a dark 'glyph' stripe inside the text box
    x0, y0, x1, y1 = px(1100, 300, 900, 700)
    rng = np.random.default_rng(1); a[y0:y1, x0:x1] = rng.integers(40, 220, (y1 - y0, x1 - x0, 3), dtype=np.uint8)   # noisy photo
    p = tmp_path / "page.png"
    Image.fromarray(a).save(p)
    return p


def test_contrast_ratio_matches_wcag_extremes():
    assert ba.contrast_ratio((0, 0, 0), (255, 255, 255)) == 21.0
    assert ba.contrast_ratio((10, 20, 30), (10, 20, 30)) == 1.0


def test_text_sits_on_a_white_surface_and_contrast_uses_declared_colour(tmp_path):
    m = build()
    res = ba.analyze_background(_render(tmp_path), m.to_dict())
    t = res["objects"]["3"]
    assert t["surface"]["rgb"][0] > 240 and t["surface"]["rgb"][1] > 240            # white behind the glyph stripe
    assert t["surface"]["ring_agrees"] is True and t["surface"]["confidence"] >= 0.8
    # no declared fill on this text: the second-most dominant colour (the dark stripe) is the estimate, said so
    assert "estimate" in t["text_contrast"]["text_source"] and t["text_contrast"]["ratio"] > 10
    assert t["texture"]["kind"] == "flat"


def test_photo_is_textured_and_page_colours_are_reported(tmp_path):
    m = build()
    res = ba.analyze_background(_render(tmp_path), m.to_dict())
    assert res["objects"]["4"]["texture"]["kind"] == "textured"
    dom = res["page_dominant"][0]
    assert dom["rgb"][2] > 140 and dom["share"] > 0.3                                  # the blue page dominates
    assert res["method"] == "render" and "not whole-shape fills" in " ".join(res["notes"])


def test_declared_fill_is_kept_apart_from_visible_colour(tmp_path):
    m = build()
    res = ba.analyze_background(_render(tmp_path), m.to_dict())
    assert res["objects"]["2"]["declared"]["fill"] == "uniform"                        # COM's word
    assert res["objects"]["2"]["visible_inside"]["method"].startswith("dominant colour")      # the render's word


def test_empty_regions_avoid_objects_and_noise(tmp_path):
    m = build()
    res = ba.analyze_background(_render(tmp_path), m.to_dict())
    assert res["empty_regions"], "an open blue area exists"
    boxes = [o.box_mm for o in m.top_level() if o.role != "bg"]
    W, H = m.page_w, m.page_h
    for r in res["empty_regions"]:
        rx0, ry0, rx1, ry1 = r["x"] * W, r["y"] * H, (r["x"] + r["width"]) * W, (r["y"] + r["height"]) * H
        for b in boxes:
            ox = min(rx1, b["x"] + b["width"]) - max(rx0, b["x"])
            oy = min(ry1, b["y"] + b["height"]) - max(ry0, b["y"])
            assert not (ox > 5 and oy > 5), f"empty region overlaps {b}"


def test_attach_background_puts_entries_on_objects(tmp_path):
    m = build()
    res = ba.analyze_background(_render(tmp_path), m.to_dict())
    mm.attach_background(m, res)
    assert m.get("3").background["surface"]["rgb"][0] > 240
    assert m.background["empty_regions"] and "objects" not in m.background
    assert mm.MasterModel.from_dict(m.to_dict()).get("3").background is not None
