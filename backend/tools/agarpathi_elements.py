"""Per-board element placement of the Agarpathi (Darshan) designer files, from the cached COM dumps
(dataset_analysis/real_dumps_cache/agarpathi, made with SIGNAGE_DUMP_CLIPS=1).

Elements (found the same way on every board):
  roof        the top-level group holding the DARSHAN / AGARBATHI texts
  sugandha    wide, short top-level group (aspect > 3)      [the red "Sugandha Swarna" badge]
  blackstone  the remaining top-level group                  [the BLACK STONE badge]
  stick       a top-level bitmap                              [landscape boards: the black product box]
  clip:*      the biggest direct children of the page-sized PowerClip (table/box composite etc.)
  name_en / name_ta   the shop-name texts (Tamil by script)
Boxes are returned as fractions of the page: cx, cy (bottom origin), w, h.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

CACHE = Path(__file__).resolve().parents[1] / "dataset_analysis" / "real_dumps_cache" / "agarpathi"


def is_tamil(t): return bool(re.search(r"[\u0B80-\u0BFF]", t or ""))


def frac(s, W, H):
    return {"cx": (s["x"] + s["w"] / 2) / W, "cy": (s["y"] + s["h"] / 2) / H, "w": s["w"] / W, "h": s["h"] / H,
            "x": s["x"] / W, "y": s["y"] / H}


def elements(dump: dict) -> dict:
    W, H = dump["page_mm"]["w"], dump["page_mm"]["h"]
    sh = dump["shapes"]
    top = [s for s in sh if not s.get("group_path")]
    out = {"W": W, "H": H, "aspect": W / H}
    texts = [s for s in top if s["type"] == "text"]
    names = [s for s in texts if not re.search(r"DARSHAN|AGARBATHI|FROM THE", s.get("text") or "")]
    for s in names:
        out["name_ta" if is_tamil(s.get("text")) else "name_en"] = {**frac(s, W, H), "size": s.get("font_size"),
                                                                    "text": s.get("text")}
    groups = [s for s in top if s["type"] == "group"]

    def n_desc(g):                      # shapes nested in this top-level group (dump order is depth first)
        i = sh.index(g); n = 0
        for t in sh[i + 1:]:
            gp = t.get("group_path") or []
            if not gp or t.get("clip"):
                break
            n += 1
        return n
    # the three artworks have fixed shape counts on every board: roof 15, BLACK STONE 14 (landscape) / 16 (portrait),
    # Sugandha 5 (one line) / 8 (the stacked variant some designer boards use)
    roof = next((g for g in groups if n_desc(g) == 15), None)
    bs = next((g for g in groups if n_desc(g) in (14, 16)), None)
    sug = next((g for g in groups if n_desc(g) in (5, 8)), None)
    if roof: out["roof"] = frac(roof, W, H)
    if sug: out["sugandha"] = {**frac(sug, W, H), "variant": n_desc(sug)}
    if bs: out["blackstone"] = frac(bs, W, H)
    bm = [s for s in top if s["type"] == "bitmap"]
    if bm: out["stick"] = frac(max(bm, key=lambda s: s["w"] * s["h"]), W, H)
    clip = [s for s in sh if s.get("clip") and len(s["group_path"]) == 1]
    if clip:
        full = [s for s in clip if s["type"] in ("bitmap", "rectangle") and s["w"] * s["h"] >= 0.9 * W * H]
        backdrop = max(full, key=lambda s: s["w"] * s["h"]) if full else None
        if backdrop:
            out["backdrop"] = frac(backdrop, W, H)
        rest = [s for s in clip if s is not backdrop]
        # the maroon footer band: a vector (non-bitmap) shape as wide as the page and sitting low
        bands = [s for s in rest if s["type"] != "bitmap" and s["x"] <= 0.1 * W and s["x"] + s["w"] >= 0.9 * W
                 and s["y"] + s["h"] / 2 < 0.45 * H]
        band = max(bands, key=lambda s: s["w"]) if bands else None
        if band:
            out["band"] = frac(band, W, H)
        comp = [s for s in rest if s is not band and s["w"] < 0.97 * W]
        if comp:
            x0 = min(s["x"] for s in comp); y0 = min(s["y"] for s in comp)
            x1 = max(s["x"] + s["w"] for s in comp); y1 = max(s["y"] + s["h"] for s in comp)
            out["composite"] = frac({"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}, W, H)
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for f in sorted(CACHE.glob("*.json")):
        e = elements(json.load(open(f, encoding="utf-8")))
        print(f.name[:28], round(e["aspect"], 2), {k: [round(v[x], 3) for x in ("cx", "cy", "w", "h")] for k, v in e.items() if isinstance(v, dict)})
