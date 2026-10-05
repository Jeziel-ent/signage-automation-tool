"""Name-placement check on a generated board's shape dump (the same dump `tools/dump_objects.py` writes): flags a shop-name text that
runs off the page, lies on top of another text, or is largely covered by a picture. The similarity score of the deck cannot see these
(a clipped name barely moves it), so this is the check that catches a visibly broken name.

check_names(dump) -> {"ok": bool, "issues": [str, ...]}   (pure Python, no CorelDRAW)"""
from __future__ import annotations

import re

TEXT_OVERLAP = 0.05          # two texts may share at most this part of the smaller box
PICTURE_COVER = 0.25         # a text may have at most this part of its box under one picture (bitmap) box
PAGE_MARGIN = 0.0            # share of the page a text must stay inside of (0 = only "not off the page")
STAMP = re.compile(r"\d{2}/\d{2}")


def _box(s: dict) -> tuple[float, float, float, float]:
    return s["x"], s["y"], s["x"] + s["w"], s["y"] + s["h"]


def _inter(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def check_names(dump: dict) -> dict:
    W, H = dump["page_mm"]["w"], dump["page_mm"]["h"]
    shapes = dump.get("shapes") or []
    texts = [s for s in shapes if s.get("type") == "text" and (s.get("text") or "").strip() and not STAMP.search(s["text"])
             and s["w"] > 0 and s["h"] > 0]
    pics = [s for s in shapes if s.get("type") == "bitmap" and s["w"] * s["h"] < 0.5 * W * H]
    issues: list[str] = []
    label = lambda s: "'" + " ".join((s["text"] or "").split())[:24] + "'"
    for s in texts:
        x0, y0, x1, y1 = _box(s)
        if x0 < -PAGE_MARGIN * W or y0 < -PAGE_MARGIN * H or x1 > W * (1 + PAGE_MARGIN) or y1 > H * (1 + PAGE_MARGIN):
            issues.append(f"{label(s)} runs off the page")
        area = s["w"] * s["h"]
        for p in pics:
            if _inter(_box(s), _box(p)) > PICTURE_COVER * area:
                issues.append(f"{label(s)} is covered by a picture")
                break
    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            if _inter(_box(a), _box(b)) > TEXT_OVERLAP * min(a["w"] * a["h"], b["w"] * b["h"]):
                issues.append(f"{label(a)} overlaps {label(b)}")
    return {"ok": not issues, "issues": issues}
