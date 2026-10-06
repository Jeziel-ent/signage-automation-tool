"""Aspect-ratio matrix for app/orientation_adapter.py: every fixture (both real client masters when their cached
scenes are present, plus synthetic masters) converted to nine target ratios from 1:4 to 8:1, checking the
invariants that must hold everywhere - nothing NEW collides, every foreground shape stays on the canvas, bitmaps
keep their aspect ratio, the product baseline is where it is defined to be, and a conversion is fast.

Vector/text groups are also asserted to stay within MAX_STRETCH_RATIO (the uniform-contain fallback; it was up to ~12x
on the real boards at 1:4 before the cap). Not asserted: any baseline for targets that have none (square/grid targets,
and landscape masters converted to a wide target)."""
from __future__ import annotations

import json
import math
import os
import time

import pytest

from app import orientation_adapter as oa
from app import scene_ops
from test_orientation_adapter import _badge_master, _tall_master, _wide_untagged_vector_master

MM = 25.4
RATIOS = [("1:4", 10, 40), ("1:3", 12, 36), ("1:2", 20, 40), ("1:1", 30, 30), ("4:3", 40, 30),
          ("2:1", 60, 30), ("4:1", 120, 30), ("6:1", 180, 30), ("8:1", 240, 30)]
_HERE = os.path.dirname(__file__)
_REAL = {"AL MADEENA (real)": "data/jobs_v2/16bfc025ca11/out/91a4cdb56ffe/scene/scene.json",
         "DARSHAN (real)": "data/jobs_v2/8a41177716c4/out/fe047cace239/scene/scene.json",
         "DALMIA (real, 138 loose fragments)": "data/jobs_v2/7f323b41cfaa/out/64b2bdbb7f51/scene/scene.json"}


def _scene(name):
    if name in _REAL:
        path = os.path.join(_HERE, "..", _REAL[name])
        if not os.path.exists(path):
            pytest.skip("cached real scene not present in this checkout")
        return json.load(open(path, encoding="utf-8"))
    return {"synthetic tall": _tall_master, "synthetic wide": _wide_untagged_vector_master,
            "synthetic badge master": lambda: _badge_master("bitmap")}[name]()


NAMES = list(_REAL) + ["synthetic tall", "synthetic wide", "synthetic badge master"]


def _overlap(a, b):
    ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return ox * oy if ox > 0 and oy > 0 else 0.0


def _box(scene, i):
    n = scene_ops.find_node(scene, i)
    return {"x": n["x"], "y": n["y"], "w": n["w"], "h": n["h"]}


def _has_bitmap(n):
    return n.get("type") == "bitmap" or any(_has_bitmap(c) for c in n.get("children") or [])


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("label,w_in,h_in", RATIOS)
def test_conversion_invariants_across_the_ratio_matrix(name, label, w_in, h_in):
    scene = _scene(name)
    W, H = w_in * MM, h_in * MM
    t0 = time.perf_counter()
    ops = oa.convert_orientation(scene, W, H)
    assert time.perf_counter() - t0 < 1.0                      # the "<1 minute per board" goal has huge headroom here
    out = scene_ops.apply_ops(scene, ops)                      # every op applies cleanly
    assert out["page"] == {"width": round(W, 4), "height": round(H, 4)}

    zones, _ = oa.classify_zones(scene)
    fg = [i for z in (oa.ZONE_HEADER, oa.ZONE_PRODUCT, oa.ZONE_MAIN_TEXT, oa.ZONE_FOOTER, oa.ZONE_OTHER) for i in zones[z]]
    assert fg

    for i in fg:                                               # inside the canvas [0, 1] x [0, 1], no NaN
        b = _box(out, i)
        assert not any(math.isnan(v) for v in b.values()), i
        assert b["x"] >= -1e-2, (i, label)
        assert b["y"] >= -1e-2, (i, label)
        assert b["x"] + b["w"] <= W + 1e-2, (i, label)
        assert b["y"] + b["h"] <= H + 1e-2, (i, label)

    for a in range(len(fg)):                                   # no NEW collision (overlaps already on the source are kept)
        for c in range(a + 1, len(fg)):
            if _overlap(_box(scene, fg[a]), _box(scene, fg[c])) > 1e-2:
                continue
            assert _overlap(_box(out, fg[a]), _box(out, fg[c])) <= 1e-2, (fg[a], fg[c], name, label)

    for i in fg:                                               # bitmaps are only ever scaled uniformly
        src = scene_ops.find_node(scene, i)
        if _has_bitmap(src) and src["w"] > 0 and src["h"] > 0:
            b = _box(out, i)
            assert (b["w"] / b["h"]) / (src["w"] / src["h"]) == pytest.approx(1.0, abs=1e-4), (i, name, label)

    src_idx = oa._Idx(scene_ops._index(scene))                 # vector/text stretch is capped (bitmaps: uniform, above)
    src_idx.page = (scene["page"]["width"], scene["page"]["height"])
    for i in fg:
        src = scene_ops.find_node(scene, i)
        if _has_bitmap(src) or oa._stretch_exempt(src_idx, [i]) or src["w"] <= 0 or src["h"] <= 0:
            continue
        b = _box(out, i)
        sx, sy = b["w"] / src["w"], b["h"] / src["h"]
        assert max(sx / sy, sy / sx) <= oa.MAX_STRETCH_RATIO + 1e-3, (i, name, label)

    products = zones[oa.ZONE_PRODUCT]
    if products:
        src_portrait = scene["page"]["width"] < scene["page"]["height"]
        frame = oa.calculate_zone_rects(W, H, portrait_source=src_portrait and W > H)[oa.ZONE_PRODUCT]
        lowest = min(_box(out, i)["y"] for i in products)
        if W < H:                                              # portrait target: baseline ~0.207 (0.2025-0.2098 by ratio)
            assert lowest == pytest.approx(frame["y"], abs=1e-2)
            assert 0.2 <= lowest / H <= 0.21
        elif W > H and src_portrait:                           # portrait master unfolded to a wide target: 0.13
            assert lowest == pytest.approx(frame["y"], abs=1e-2)
            assert lowest / H == pytest.approx(0.13, abs=1e-3)
        # square / grid targets and landscape masters converted to a wide target define no baseline
