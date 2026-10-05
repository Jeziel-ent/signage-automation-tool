"""Brand-agnostic layout from the designers' own boards ("example library").

Why: a master scaled uniformly does not look like what a designer makes for another size. Designers build a few STYLES (where
each picture, badge and the shop name sit) and repeat them; boards of the same size are identical to the millimetre. So the
engine copies the nearest designer board instead of inventing a layout (measured on 62 Agarpathi boards: the rule engine put
7 within 2 % of the page on the artwork, the nearest-board copy 36 - tools/example_loo.py).

Everything here is generic - nothing names a brand, a shape or a picture:

  library  (backend/brand_data/<brand>/library.json, built by tools/build_example_library.py from COM dumps of the master(s) and
            the designer files)
      masters : per master, its art ELEMENTS as signatures (kind, aspect ratio, number of nested shapes) - a group, a bitmap, a
                lone shape, or a CLUSTER of loose shapes that form one picture - plus its page-clip composite proportions
      boards  : per designer board, where each master element went (box as a fraction of the page), the page-clip boxes and
                the shop-name lines

  plan()   a master opened at run time is matched to a library master by the same signatures; the nearest designer board
           (aspect ratio + height) whose pictures the master can supply is chosen; its boxes become the layout:
             boxes   {object id: (x, y, w, h, key)}   mm, bottom-left origin (a cluster's members keep their place in it)
             clip    {backdrop|band|composite: (x, y, w, h)}   contents of the page-sized background PowerClip
             texts   {name_en|name_ta: spec}           how a shop-name line is sized and anchored
  returns None when nothing fits - the caller then uses the ordinary rules.

Pure Python (no CorelDRAW): dumps in, numbers out.
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "brand_data"
LANDSCAPE_MIN_RATIO = 1.25      # same split as master routing (orientation_adapter.target_orientation)
H_WEIGHT = 0.35                 # how much board HEIGHT counts next to the aspect ratio when ranking designer boards
PANEL_TA_FIT = 0.92             # a Tamil name (at the top of the white panel) fills this share of the panel
PANEL_EN_FIT = 0.66             # an English name (low, between the pictures that overlap the panel corners) this share - her single English
                                # lines measure 0.71 of the panel on every wide board (Sri Ganesha 0.191 / 0.269, Jeeva 0.294 / 0.412)
PAGE_FIT = 0.96                 # a name line is never wider than this share of the page (a designer's own line can run off hers)
SAME_SHAPE_TOL = 0.08           # an uploaded master whose width / height is within 8 % of the target's is the design for that shape
EXACT_TOL = 0.01                # width / height within 1 % = "the same size"
SIG_ASPECT_TOL = 0.30           # |ln(aspect ratio)| allowed for a strict signature match
LOOSE_ASPECT_TOL = 0.55         # ... for a loose one (a re-drawn group of the same kind)
COMPOSITE_TOL = 0.45            # page-clip composite proportions must stay within this of the master's
BG_AREA = 0.9                   # a shape covering this much of the page is the background, not an element
FRAGMENT_GAP_FRAC = 0.012       # loose shapes closer than this share of the page's long side belong to one picture
TAMIL = re.compile(r"[஀-௿]")


# ------------------------------------------------------------------------------------------------ dump helpers
def kind_of(t: str) -> str:
    return {"text": "text", "group": "group", "bitmap": "bitmap"}.get(t, "shape")


def top_level(dump: dict) -> list[dict]:
    """Top-level shapes of a dump, each with `n_desc` (shapes nested in it, not counting PowerClip contents) and `kind`."""
    sh = dump["shapes"]
    out = []
    for i, s in enumerate(sh):
        if s.get("group_path"):
            continue
        n = 0
        for t in sh[i + 1:]:
            gp = t.get("group_path") or []
            if not gp or t.get("clip"):
                break
            n += 1
        out.append({**s, "n_desc": n, "kind": kind_of(s["type"])})
    return out


def clip_children(dump: dict) -> list[dict]:
    return [s for s in dump["shapes"] if s.get("clip") and len(s["group_path"]) == 1]


def _frac(s: dict, W: float, H: float) -> dict:
    return {"cx": (s["x"] + s["w"] / 2) / W, "cy": (s["y"] + s["h"] / 2) / H, "w": s["w"] / W, "h": s["h"] / H,
            "x": s["x"] / W}


def _is_bg(s: dict, W: float, H: float) -> bool:
    return s["kind"] != "text" and s["w"] * s["h"] >= BG_AREA * W * H


def clip_classes(kids: list[dict], W: float, H: float) -> dict:
    """Split the page clip's children into backdrop (full-bleed picture), band (a wide, low vector shape) and the rest (which
    moves as one composite). Returns {"backdrop": shape|None, "band": shape|None, "rest": [shapes]}."""
    full = [s for s in kids if s["type"] in ("bitmap", "rectangle") and s["w"] * s["h"] >= BG_AREA * W * H]
    backdrop = max(full, key=lambda s: s["w"] * s["h"]) if full else None
    rest = [s for s in kids if s is not backdrop]
    bands = [s for s in rest if s["type"] != "bitmap" and s["x"] <= 0.1 * W and s["x"] + s["w"] >= 0.9 * W
             and s["y"] + s["h"] / 2 < 0.45 * H]
    band = max(bands, key=lambda s: s["w"] * s["h"]) if bands else None   # the big colour block, not a thin rule
    comp = [s for s in rest if s is not band and s["w"] < 0.97 * W]
    return {"backdrop": backdrop, "band": band, "rest": comp}


def clip_bitmaps(dump: dict) -> list[dict]:
    """Every bitmap inside the page clip at any depth (a picture composite is usually a group of them)."""
    return [s for s in dump["shapes"] if s["type"] == "bitmap"
            and (s.get("clip") or any(g.endswith("<clip>") for g in (s.get("group_path") or [])))]


def match_clip_bitmaps(master_bms: list[dict], bms: list[dict], W: float, H: float, tol: float = 0.15) -> dict[str, dict]:
    """master clip-bitmap key -> the board's bitmap of (nearly) the same proportions: the pictures are scaled, not redrawn,
    so aspect ratio identifies them (sticks, box, table, backdrop)."""
    out, used = {}, set()
    for mb in sorted(master_bms, key=lambda m: -m["area"]):
        best = None
        for i, s in enumerate(bms):
            if i in used or s["h"] <= 0:
                continue
            if mb["backdrop"] != (s["w"] * s["h"] >= 0.5 * W * H):    # the backdrop is told from a picture by its size
                continue
            d = abs(math.log(max(s["w"] / s["h"], 1e-9) / mb["aspect"]))
            if d <= tol and (best is None or d < best[0]):
                best = (d, i)
        if best:
            used.add(best[1])
            s = bms[best[1]]
            out[mb["k"]] = {"cx": round((s["x"] + s["w"] / 2) / W, 5), "cy": round((s["y"] + s["h"] / 2) / H, 5),
                            "w": round(s["w"] / W, 5), "h": round(s["h"] / H, 5), "bd": mb["backdrop"]}
    return out


def union_box(shapes: list[dict]) -> dict | None:
    if not shapes:
        return None
    x0 = min(s["x"] for s in shapes); y0 = min(s["y"] for s in shapes)
    x1 = max(s["x"] + s["w"] for s in shapes); y1 = max(s["y"] + s["h"] for s in shapes)
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


# ------------------------------------------------------------------------------------------------ units
class Unit:
    """One placeable thing: a group, a bitmap, a lone shape - or a cluster of loose shapes that form one picture (real masters
    often leave a logo as 100+ ungrouped curves). `members` are the underlying objects (dump dicts or layout.Obj)."""

    def __init__(self, kind, members, x, y, w, h, n_desc):
        self.kind, self.members, self.x, self.y, self.w, self.h, self.n_desc = kind, members, x, y, w, h, n_desc
        self.id = "+".join(str(m.id if hasattr(m, "id") else m.get("idx")) for m in members)

    @property
    def aspect(self) -> float:
        return self.w / self.h if self.h > 0 else 1.0


def _cluster(boxes: list[tuple], gap: float) -> list[list[int]]:
    """Union-find over bounding boxes closer than `gap` (touching and overlapping included)."""
    n = len(boxes)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        xi, yi, wi, hi = boxes[i]
        for j in range(i + 1, n):
            xj, yj, wj, hj = boxes[j]
            if xi - gap <= xj + wj and xj - gap <= xi + wi and yi - gap <= yj + hj and yj - gap <= yi + hi:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: g[0])


def make_units(items: list, W: float, H: float, get) -> list[Unit]:
    """`items`: top-level shapes (dump dicts or Obj) already stripped of names and background; `get(item)` -> (kind, x, y, w,
    h, n_desc). Loose `shape`s are clustered, everything else is its own unit."""
    units: list[Unit] = []
    loose = [(it, get(it)) for it in items if get(it)[0] == "shape"]
    others = [(it, get(it)) for it in items if get(it)[0] != "shape"]
    for grp in _cluster([(g[1], g[2], g[3], g[4]) for _, g in loose], FRAGMENT_GAP_FRAC * max(W, H)):
        mem = [loose[i] for i in grp]
        if len(mem) == 1:
            _, (_k, x, y, w, h, _n) = mem[0]
            units.append(Unit("shape", [mem[0][0]], x, y, w, h, 0))
            continue
        x0 = min(g[1] for _, g in mem); y0 = min(g[2] for _, g in mem)
        x1 = max(g[1] + g[3] for _, g in mem); y1 = max(g[2] + g[4] for _, g in mem)
        units.append(Unit("cluster", [m for m, _ in mem], x0, y0, x1 - x0, y1 - y0, len(mem)))
    for it, (k, x, y, w, h, n) in others:
        units.append(Unit(k, [it], x, y, w, h, n))
    return units


def dump_units(dump: dict, skip_idx: set[int] | None = None) -> list[Unit]:
    W, H = dump["page_mm"]["w"], dump["page_mm"]["h"]
    items = []
    for i, s in enumerate(top_level(dump)):
        if (skip_idx and i in skip_idx) or s["kind"] == "text" or _is_bg(s, W, H):
            continue
        items.append({**s, "idx": i})
    return make_units(items, W, H, lambda s: (s["kind"], s["x"], s["y"], s["w"], s["h"], s["n_desc"]))


def obj_units(objs, W: float, H: float, name_ids: set[str]) -> list[Unit]:
    items = [o for o in objs if o.id not in name_ids and o.kind != "text" and o.h > 0 and o.w * o.h < BG_AREA * W * H]
    return make_units(items, W, H, lambda o: (o.kind, o.x, o.y, o.w, o.h, getattr(o, "n_desc", 0)))


# ------------------------------------------------------------------------------------------------ library build
def describe_master(dump: dict, name_ids: set[int] | None = None) -> dict:
    """The master's art elements as signatures. `name_ids`: indexes (into top_level(dump)) of the shop-name lines."""
    W, H = dump["page_mm"]["w"], dump["page_mm"]["h"]
    els = []
    for u in dump_units(dump, name_ids):
        els.append({"k": f"e{len(els)}", "kind": u.kind, "aspect": round(u.aspect, 4), "n_desc": u.n_desc,
                    "area": round(u.w * u.h / (W * H), 5), "cx": round((u.x + u.w / 2) / W, 4)})
    cc = clip_classes(clip_children(dump), W, H)
    comp = union_box(cc["rest"])
    bms = sorted(clip_bitmaps(dump), key=lambda s: -(s["w"] * s["h"]))
    top = top_level(dump)
    stamps = sorted({re.sub(r"[^A-Z]", "", (t.get("text") or "").upper()) for i, t in enumerate(top)
                     if t["kind"] == "text" and i not in (name_ids or set()) and not TAMIL.search(t.get("text") or "")} - {""})
    return {"page": [round(W, 2), round(H, 2)], "elements": els, "stamps": stamps,
            "composite_aspect": round(comp["w"] / comp["h"], 4) if comp else None,
            "has_band": cc["band"] is not None,
            "clip_bitmaps": [{"k": f"b{i}", "aspect": round(s["w"] / s["h"], 4), "area": round(s["w"] * s["h"] / (W * H), 5),
                              "backdrop": s["w"] * s["h"] >= BG_AREA * W * H} for i, s in enumerate(bms)]}


def _ln(a: float, b: float) -> float:
    return abs(math.log(max(a, 1e-9) / max(b, 1e-9)))


def match_elements(master: dict, units: list[Unit], W: float, H: float) -> dict[str, tuple[Unit, bool]]:
    """master element key -> (unit, loose?). Strict first (same kind, nested count, aspect), then loose (same kind, any
    proportions within reason) for what is left - a designer re-drawing a badge keeps its kind but not its shape."""
    used: set[int] = set()
    out: dict[str, tuple[Unit, bool]] = {}
    for loose in (False, True):
        for e in sorted(master["elements"], key=lambda e: -e["area"]):
            if e["k"] in out:
                continue
            best = None
            for ui, u in enumerate(units):
                if ui in used or u.kind != e["kind"]:
                    continue
                d = _ln(u.aspect, e["aspect"])
                if not loose and (d > SIG_ASPECT_TOL or (e["kind"] in ("group", "cluster") and u.n_desc != e["n_desc"])):
                    continue
                if loose and d > LOOSE_ASPECT_TOL * (1 if e["kind"] == "group" else 0.45):
                    continue
                key = (d, abs((u.x + u.w / 2) / W - e["cx"]))
                if best is None or key < best[0]:
                    best = (key, ui)
            if best:
                out[e["k"]] = (units[best[1]], loose)
                used.add(best[1])
    return out


def is_name_text(name: str | None, text: str | None) -> bool:
    """Is this English text the shop's name line? The file name's shop name inside it (exact), or most of its words: designers retype
    a name ('SRI THANGAVILAS SODA FACTORY' for 'Sri thanga Vilas soda factory', 'NEW HAPPY IYENGAR BAKERY & SWEETS')."""
    norm = lambda t: re.sub(r"[^A-Z0-9]", "", (t or "").upper())
    if not name or not norm(name):
        return False
    if norm(name) in norm(text):
        return True
    if TAMIL.search(text or "") or re.search(r"\d{2}/\d{2}", text or ""):
        return False
    words = lambda t: {w for w in re.findall(r"[A-Z]+", (t or "").upper()) if len(w) >= 3}
    a, b = words(name), words(text)
    return len(a) >= 2 and len(b) >= 2 and len(a & b) >= 0.6 * min(len(a), len(b)) and len(a & b) >= 2


COPY_NESTED_TOL = 0.15


def _same_nested(a: int, b: int) -> bool:
    """A repeated logo is the master's logo, but the designer often adds or drops a shape in the copy (13 shapes against 12): the
    nested count may differ by 15 % (at least 1)."""
    return abs(a - b) <= max(1, round(COPY_NESTED_TOL * max(a, b)))


def board_record(master: dict, dump: dict, name_en: str | None, file: str) -> dict | None:
    """One designer file expressed against `master`; None when it is not built from it (too few elements match)."""
    W, H = dump["page_mm"]["w"], dump["page_mm"]["h"]
    top = top_level(dump)
    names = {}
    for i, s in enumerate(top):
        if s["kind"] != "text":
            continue
        if TAMIL.search(s.get("text") or ""):
            names.setdefault("name_ta", i)
        elif is_name_text(name_en, s.get("text")):
            names["name_en"] = i
    if "name_en" not in names and "name_ta" in names:
        # the designer retyped the name ('N N POOJA STORE' for 'N N NATTU MARUNDHU KADAI'): with a Tamil name line present, the biggest
        # remaining Latin text that is no stamp of the master (and no date code) is the English line
        stamps = set(master.get("stamps") or ())
        best = None
        for i, s in enumerate(top):
            t = (s.get("text") or "")
            if s["kind"] != "text" or i in names.values() or TAMIL.search(t) or re.search(r"\d", t) or len(re.findall(r"[A-Za-z]", t)) < 3:
                continue
            if re.sub(r"[^A-Z]", "", t.upper()) in stamps or s["w"] * s["h"] < 0.002 * W * H:
                continue
            if best is None or s["w"] * s["h"] > best[0]:
                best = (s["w"] * s["h"], i)
        if best:
            names["name_en"] = best[1]
    units = dump_units(dump, set(names.values()))
    matches = match_elements(master, units, W, H)
    if not master["elements"] or len(matches) < 0.6 * len(master["elements"]):
        return None
    used = {id(u) for u, _ in matches.values()}
    copies: dict[str, list[dict]] = {}      # the designer repeated a master element (a wide board shows the logo twice)
    extra = 0                               # art on her board that is no master element at all (a new picture)
    for u in units:
        if id(u) in used:
            continue
        cand = None
        for e in master["elements"]:
            if e["kind"] != u.kind:
                continue
            d = _ln(u.aspect, e["aspect"])
            if d <= SIG_ASPECT_TOL and (e["kind"] not in ("group", "cluster") or _same_nested(u.n_desc, e["n_desc"]))                     and (cand is None or d < cand[0]):
                cand = (d, e["k"])
        if cand and cand[1] in matches:
            copies.setdefault(cand[1], []).append(
                {"cx": round((u.x + u.w / 2) / W, 5), "cy": round((u.y + u.h / 2) / H, 5), "w": round(u.w / W, 5),
                 "h": round(u.h / H, 5)})
        else:
            extra += 1
    els = {}
    for k, (u, loose) in matches.items():
        els[k] = {"cx": round((u.x + u.w / 2) / W, 5), "cy": round((u.y + u.h / 2) / H, 5), "w": round(u.w / W, 5),
                  "h": round(u.h / H, 5), "x": round(u.x / W, 5), "loose": loose, "aspect": round(u.aspect, 4),
                  "n_desc": u.n_desc}
    rec = {"file": file, "W": round(W, 2), "H": round(H, 2), "els": els,
           "strict": sum(1 for _, loose in matches.values() if not loose),
           "coverage": round(len(matches) / len(master["elements"]), 3), "extra": extra, "copies": copies}
    cc = clip_classes(clip_children(dump), W, H)
    clip = {}
    for k, s in (("backdrop", cc["backdrop"]), ("band", cc["band"]), ("composite", union_box(cc["rest"]))):
        if s:
            clip[k] = {a: round(b, 5) for a, b in _frac(s, W, H).items()}
    panel = [k for k in clip_children(dump) if k["type"] in ("rectangle", "curve") and k["h"] >= 0.9 * H and 0.1 * W <= k["w"] <= 0.8 * W]
    if len(panel) == 1:                       # the white name panel of a centre-panel design (a full-height rectangle in the page clip)
        clip["panel"] = {a: round(b, 5) for a, b in _frac(panel[0], W, H).items()}
    rec["clip"] = clip
    rec["cbm"] = match_clip_bitmaps(master.get("clip_bitmaps", []), clip_bitmaps(dump), W, H)
    rec["texts"] = {k: {**{a: round(b, 5) for a, b in _frac(top[i], W, H).items()},
                        "lines": len([ln for ln in re.split(r"[\r\n\v]+", top[i].get("text") or "") if ln.strip()]) or 1}
                    for k, i in names.items()}
    return rec


# ------------------------------------------------------------------------------------------------ run time
@lru_cache(maxsize=16)
def library(brand: str) -> dict | None:
    p = DATA / brand.strip().lower() / "library.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def candidate_libraries(brand: str | None) -> list[dict]:
    """The brand's own library, then every other library whose brand rule does not switch the feature off. A master is recognised
    by its shapes, not by the brand label it was uploaded under: the Hangyo masters uploaded as brand "Adinn" still get Hangyo's
    layout. A wrong cross-brand hit needs every library element present and no leftover shape, so it does not happen by chance."""
    out, seen = [], set()
    own = library(brand) if brand else None
    if own:
        out.append(own)
        seen.add(brand.strip().lower())
    for d in sorted(DATA.glob("*/library.json")):
        name = d.parent.name
        if name in seen or not _enabled(name):
            continue
        lib = library(name)
        if lib:
            out.append(lib)
    return out


def _enabled(brand: str) -> bool:
    """False when the brand's rule file says "example_library": false (dalmia keeps its hand-tuned rules)."""
    p = Path(__file__).resolve().parent / "brand_rules" / f"{brand}.json"
    try:
        return bool(json.loads(p.read_text(encoding="utf-8")).get("example_library", True)) if p.exists() else True
    except Exception:
        return True


def family(w: float, h: float) -> str:
    return "L" if w / h >= LANDSCAPE_MIN_RATIO else "P"


def find_master(lib: dict, objs, page_w: float, page_h: float, name_ids: set[str]):
    """The library master whose art elements are all present (by signature) among the opened master's top-level objects:
    returns (master record, {element key: Unit}) or (None, None)."""
    units = obj_units(objs, page_w, page_h, name_ids)
    best = None
    for m in lib["masters"]:
        used, mapping = set(), {}
        for e in sorted(m["elements"], key=lambda e: -e["area"]):
            pick = None
            for ui, u in enumerate(units):
                if ui in used or u.kind != e["kind"]:
                    continue
                d = _ln(u.aspect, e["aspect"])
                if d > SIG_ASPECT_TOL or (e["kind"] in ("group", "cluster") and u.n_desc != e["n_desc"]):
                    continue
                if pick is None or d < pick[0]:
                    pick = (d, ui, u)
            if pick:
                mapping[e["k"]] = pick[2]
                used.add(pick[1])
        # every library element found AND nothing left over: a master that is a superset of another library master (the 9x3 ft
        # master holds every picture of the 125x48 one plus the flat box) must not be taken for the smaller one
        score = len(mapping) / max(len(m["elements"]), len(units), 1)
        if len(mapping) == len(m["elements"]) and score >= 0.99 and (best is None or score > best[0]):
            best = (score, m, mapping)
    return (best[1], best[2]) if best else (None, None)


def _t_aspect(b: dict, k: str) -> float:
    e = b["els"][k]
    return (e["w"] * b["W"]) / (e["h"] * b["H"])


def compatible(b: dict, master: dict) -> bool:
    """A designer board can be rebuilt from THIS master only if the pictures it places are the master's: a bitmap that was
    replaced by another picture (loose match), or a changed table/box composite, would be stretched wrongly."""
    for e in master["elements"]:
        be = b["els"].get(e["k"])
        if be is None:
            continue
        if e["kind"] == "bitmap" and (be["loose"] or _ln(_t_aspect(b, e["k"]), e["aspect"]) > SIG_ASPECT_TOL + 0.2):
            return False
    mb = master.get("clip_bitmaps", [])
    if mb and all(m["k"] in (b.get("cbm") or {}) for m in mb if not m["backdrop"]):
        return True                    # every clipped picture is the master's and is placed individually
    comp, ma = b["clip"].get("composite"), master.get("composite_aspect")
    if comp and ma and _ln((comp["w"] * b["W"]) / (comp["h"] * b["H"]), ma) > COMPOSITE_TOL:
        return False
    return True


def norm_type(t: str | None) -> str:
    """A board type compared loosely: 'Double Side GSB' -> 'doublesidegsb', the designers' 'Frotlit' typo = 'frontlit'."""
    k = re.sub(r"[^a-z]", "", (t or "").lower())
    return "frontlit" if k in ("frotlit", "frontlt", "frontllit") else k


def board_kind(file: str) -> str:
    """The type segment of a designer file name '<S.No> - <W> X <H> <unit> - <TYPE> - <shop>.cdr' ('' when it has none)."""
    parts = re.split(r"\s+-\s+", Path(file).stem)  # NOSONAR - bounded, human-entered strings (file names / emails); no ReDoS exposure, rewrite would risk parsing changes
    return norm_type(parts[2]) if len(parts) >= 4 else ""


def select_board(lib: dict, master: dict, new_w: float, new_h: float, exclude=None, board_type: str | None = None):
    fam = family(new_w, new_h)
    asp = new_w / new_h
    skip = {exclude} if isinstance(exclude, str) else set(exclude or ())
    cands = [b for b in lib["boards"] if b["master"] == master["id"] and family(b["W"], b["H"]) == fam
             and b["file"] not in skip and compatible(b, master)]
    if not cands:
        return None
    # nearest first; among boards of (practically) the same size the most complete one - every master element placed, both
    # shop-name lines present - because designers sometimes drop a badge or a line on one board of a size
    def rank(b):
        d = _ln(b["W"] / b["H"], asp) + H_WEIGHT * _ln(b["H"], new_h)
        # `board_type` (the shop's TYPE OF BOARD) only separates boards of the same size: a 8x4 ft GSB and a 8x4 ft Double Side GSB are
        # different designs, the plain distance cannot tell them apart
        mismatch = 1 if want_type and board_kind(b["file"]) and board_kind(b["file"]) != want_type else 0
        return (round(d, 3), mismatch, -len(b["els"]), -len(b["texts"]), -sum(1 for e in b["els"].values() if not e["loose"]))
    want_type = norm_type(board_type)
    return min(cands, key=rank)


def _same_size(b: dict, w: float, h: float) -> bool:
    return abs(b["W"] / w - 1) < EXACT_TOL and abs(b["H"] / h - 1) < EXACT_TOL


def select_text_board(lib: dict, new_w: float, new_h: float, board_type: str | None = None, exclude=None):
    """The nearest designer board of ANY master that carries shop-name text: where the master's own boards are all of another size
    (Hangyo: a 20-26 ft board is drawn from the 10 X 3 master, but the designer made those wide boards from the 6 X 3 one) the name
    block of a board of the target's own size says how big the name is on a wide board; the 10 X 3 boards say nothing about it.
    Same ranking as `select_board` (distance, then TYPE OF BOARD), without the master or picture-compatibility conditions."""
    fam, asp = family(new_w, new_h), new_w / new_h
    want_type = norm_type(board_type)
    skip = {exclude} if isinstance(exclude, str) else set(exclude or ())
    cands = [b for b in lib["boards"] if family(b["W"], b["H"]) == fam and b["file"] not in skip
             and any(k in (b.get("texts") or {}) for k in ("name_en", "name_ta"))]
    if not cands:
        return None
    def rank(b):
        d = _ln(b["W"] / b["H"], asp) + H_WEIGHT * _ln(b["H"], new_h)
        mismatch = 1 if want_type and board_kind(b["file"]) and board_kind(b["file"]) != want_type else 0
        return (round(d, 3), mismatch, -len(b["texts"]))
    return min(cands, key=rank)


def _text_spec(lib: dict, master: dict | None, board: dict, key: str, new_w: float, new_h: float, cap: float | None = None,
               fill: list | None = None) -> dict | None:
    """`cap`: an extra upper bound on the name width, as a share of the page (see `plan`). `fill`: designer boards of the same design and
    height - the name block may be as tall as the biggest of theirs (she sets a short name big on two lines and a long one smaller on
    one line to fill the panel; one board's block only knows its own name's length)."""
    group = [b for b in lib["boards"] if (master is None or b["master"] == master["id"]) and _same_size(b, board["W"], board["H"])
             and b["texts"].get(key)]
    if not group:
        return None
    kind = board_kind(board["file"])
    mine = [b for b in group if board_kind(b["file"]) == kind] if kind else []
    # boards of one size but another TYPE can be a different design (an 8x4 ft Double Side GSB has one big name line, the GSB board two
    # lines in a banner): when their lines sit visibly elsewhere (> 4 % of the height) they must not be averaged with this board's
    if mine and len(mine) < len(group) and any(abs(b["texts"][key]["cy"] - board["texts"].get(key, b["texts"][key])["cy"]) > 0.04
                                               for b in group if b not in mine):
        group = mine
    box = board["texts"].get(key) or group[0]["texts"][key]
    h_frac = max(b["texts"][key]["h"] for b in group)
    w_cap = max(b["texts"][key]["w"] for b in group)
    lines = max(b["texts"][key].get("lines", 1) for b in group)
    fill = [b for b in (fill or ()) if b["texts"].get(key)]
    if fill:
        h_frac = max(h_frac, max(b["texts"][key]["h"] for b in fill))
        lines = max(lines, max(b["texts"][key].get("lines", 1) for b in fill))
        if cap:
            w_cap = cap                  # the panel's own width limits a line, not the length of the names that board happened to have
    lhs = [b["texts"][key]["h"] / max(b["texts"][key].get("lines", 1), 1) for b in group]
    line_h = max(lhs)                                                       # her font size, as a line height
    # boards of this size that agree on the font size keep it ("font"); where it varies with the name she fits each name to its
    # text block ("block"), and a lone example cannot tell the two apart
    mode = "font" if len(lhs) >= 2 and max(lhs) / max(min(lhs), 1e-9) <= 1.15 and not fill else "block"
    lefts = [b["texts"][key]["x"] for b in group]
    rights = [b["texts"][key]["x"] + b["texts"][key]["w"] for b in group]
    centres = [b["texts"][key]["cx"] for b in group]
    # anchor: the edge (or centre) that stays put across boards of this size; a single board decides by position
    if len(group) >= 2:
        spread = lambda v: max(v) - min(v)
        anchor = min((spread(lefts), "left"), (spread(rights), "right"), (spread(centres), "center"))[1]
    else:
        anchor = "center" if abs(box["cx"] - 0.5) < 0.06 else ("left" if box["cx"] < 0.5 else "right")
    ax = {"left": sum(lefts) / len(lefts), "right": sum(rights) / len(rights), "center": sum(centres) / len(centres)}[anchor]
    cy = sum(b["texts"][key]["cy"] for b in group) / len(group)
    return {"anchor": anchor, "ax": ax * new_w, "cy": cy * new_h, "h": h_frac * new_h, "w_cap": min(w_cap, PAGE_FIT, cap or PAGE_FIT) * new_w, "page_w": new_w,
            "line_h": line_h * new_h, "max_lines": lines, "mode": mode,
            "pref_lines": int(box.get("lines") or 0) if (fill and key == "name_en") else 0}


BLOCK_GAP_FRAC = 0.06           # clear space between a stacked Tamil and English block, as a share of the board height
BLOCK_EDGE_FRAC = 0.04          # a borrowed name block keeps this share of the height clear of the top and bottom edges
BLOCK_MAX_FRAC = {"name_ta": 0.56, "name_en": 0.36}   # tallest a borrowed block may be (share of the board height)


def _fit_blocks_in_panel(texts: dict, new_h: float) -> None:
    """Name blocks borrowed from the tallest boards of this height must stay on the page and clear of each other: a block is at most as
    tall as the room above / below its own centre (and `BLOCK_MAX_FRAC` of the height), and a stacked pair (Tamil over English) is
    scaled down until `BLOCK_GAP_FRAC` of the height is left between them - the glyphs of a wrapped line reach beyond the block."""
    margin = BLOCK_EDGE_FRAC * new_h
    for k, t in texts.items():
        t["h"] = min(t["h"], 2 * min(t["cy"], new_h - t["cy"]) * 0.96, BLOCK_MAX_FRAC.get(k, 0.5) * new_h)
        t["cy"] = min(max(t["cy"], t["h"] / 2 + margin), new_h - t["h"] / 2 - margin)       # the block keeps clear of the board's edges
    if "name_en" in texts and "name_ta" in texts:
        lo, hi = sorted((texts["name_en"], texts["name_ta"]), key=lambda t: t["cy"])
        need = (lo["h"] + hi["h"]) / 2
        room = hi["cy"] - lo["cy"] - BLOCK_GAP_FRAC * new_h
        if need > 0 and 0 < room < need:
            f = room / need
            lo["h"] *= f
            hi["h"] *= f


PANEL_FULL_EN_W = 0.33           # panels at least this wide (share of the page) get the full English width ratio
PICTURE_GAP_FRAC = 0.01          # clear space between a side-anchored name line and a picture next to it, as a share of the page width


def _clear_of_pictures(texts: dict, bitmaps: dict, new_w: float, new_h: float) -> None:
    """A name line anchored at its LEFT or RIGHT edge (not a centred one - those sit between pictures by design) must not run under a
    clipped picture beside it. A picture overlapping the line's span (same rows) in its far half shortens the line; one in its near half
    (over the anchored edge) moves the anchor past the picture."""
    gap = PICTURE_GAP_FRAC * new_w
    for t in texts.values():
        if t["anchor"] not in ("left", "right"):
            continue
        lo, hi = t["cy"] - t["h"] / 2, t["cy"] + t["h"] / 2
        for box in bitmaps.values():
            x, y, w, h = box[:4]
            if h <= 0 or y + h <= lo or y >= hi or w >= 0.5 * new_w:                  # not beside this line / a backdrop-sized picture
                continue
            a0, a1 = (t["ax"], t["ax"] + t["w_cap"]) if t["anchor"] == "left" else (t["ax"] - t["w_cap"], t["ax"])
            if x + w <= a0 or x >= a1:                                                # clear of the span already
                continue
            far_half = (x + w / 2) > (a0 + a1) / 2 if t["anchor"] == "left" else (x + w / 2) < (a0 + a1) / 2
            if t["anchor"] == "left":
                if far_half:
                    t["w_cap"] = max(x - gap - t["ax"], 0.2 * t["w_cap"])
                else:
                    t["w_cap"] -= x + w + gap - t["ax"]
                    t["ax"] = x + w + gap
            else:
                if far_half:
                    t["w_cap"] = max(t["ax"] - (x + w + gap), 0.2 * t["w_cap"])
                else:
                    t["w_cap"] -= t["ax"] - (x - gap)
                    t["ax"] = x - gap


def _separate_name_lines(texts: dict, board: dict, new_w: float) -> None:
    """Two name lines side by side on the designer's board (Tamil left, English right): the left line is held to the room left of the
    other one, so a longer name never runs into it - whether or not the recorded boxes overlap (the width cap of a spec can come from
    another board's longer name). When the recorded boxes DO overlap (a Tamil text shape carrying trailing spaces or a divider) the left
    line is also anchored at the board's own left edge. Boards whose two lines do not share a row are untouched."""
    bt = board.get("texts") or {}
    if "name_en" not in texts or "name_ta" not in texts or "name_en" not in bt or "name_ta" not in bt:
        return
    a, b = sorted((bt["name_en"], bt["name_ta"]), key=lambda t: t["x"])           # a = the left one
    same_row = abs(a["cy"] - b["cy"]) < (a["h"] + b["h"]) / 2 * 0.8
    if not same_row:
        return
    key = "name_en" if a is bt["name_en"] else "name_ta"
    other = "name_ta" if key == "name_en" else "name_en"
    gap = 0.03 * new_w
    o = texts[other]                                                                # where the right-hand line really starts (its planned width)
    o_left = {"left": o["ax"], "center": o["ax"] - o["w_cap"] / 2, "right": o["ax"] - o["w_cap"]}[o["anchor"]]
    edge = min(b["x"] * new_w, o_left) - gap
    t = texts[key]
    if a["x"] + a["w"] > b["x"]:                                                    # overlapping boxes: anchor at the board's left edge
        room = edge - a["x"] * new_w
        if room > 0:
            texts[key] = {**t, "anchor": "left", "ax": a["x"] * new_w, "w_cap": min(t["w_cap"], room)}
        return
    room = {"left": edge - t["ax"], "center": 2 * (edge - t["ax"]), "right": 0}[t["anchor"]]   # not overlapping: only the width cap follows the room
    if t["anchor"] != "right" and 0 < room < t["w_cap"]:
        texts[key] = {**t, "w_cap": room}


def plan(brand: str | None, objs, page_w: float, page_h: float, new_w: float, new_h: float,
         name_ids: set[str] | None = None, exclude=None, board_type: str | None = None) -> dict | None:
    """See the module docstring. `exclude`: designer files to ignore (leave-one-out testing only)."""
    master = mapping = lib = None
    for cand in candidate_libraries(brand):             # the brand's own library first, then any other (see candidate_libraries)
        master, mapping = find_master(cand, objs, page_w, page_h, set(name_ids or ()))
        if master is not None:
            lib = cand
            break
    if master is None:
        return None
    board = select_board(lib, master, new_w, new_h, exclude, board_type)
    if board is None:
        return None
    same_aspect = abs((board["W"] / board["H"]) / (new_w / new_h) - 1) < 0.005
    boxes = {}
    for k, u in mapping.items():
        be = board["els"].get(k)
        if be is None:
            continue
        tw, th = be["w"] * new_w, be["h"] * new_h
        if same_aspect and not be["loose"]:
            w, h = tw, th
        else:                       # keep the master element's own proportions, never past either target extent
            sc = min(tw / u.w, th / u.h)
            w, h = u.w * sc, u.h * sc
        x0, y0 = be["cx"] * new_w - w / 2, be["cy"] * new_h - h / 2
        if len(u.members) == 1:
            boxes[u.id] = (x0, y0, w, h, k)
        else:                       # a cluster of loose shapes moves as one picture: every member keeps its place in it
            kx, ky = w / u.w, h / u.h
            for m in u.members:
                boxes[m.id] = (x0 + (m.x - u.x) * kx, y0 + (m.y - u.y) * ky, m.w * kx, m.h * ky, k)
    for k, cps in (board.get("copies") or {}).items():
        u = mapping.get(k)
        be = board["els"].get(k)
        if u is None or be is None:
            continue
        for i, t in enumerate(cps, start=1):                      # each copy: a duplicate of the master element (`_tile<i>` ids)
            tw, th = t["w"] * new_w, t["h"] * new_h
            if same_aspect:
                w, h = tw, th
            else:
                sc = min(tw / u.w, th / u.h)
                w, h = u.w * sc, u.h * sc
            x0, y0 = t["cx"] * new_w - w / 2, t["cy"] * new_h - h / 2
            kx, ky = w / u.w, h / u.h
            for m in u.members:
                mid = m.id if hasattr(m, "id") else m.get("idx")
                boxes[f"{mid}_tile{i}"] = (x0 + (m.x - u.x) * kx, y0 + (m.y - u.y) * ky, m.w * kx, m.h * ky, k)
    clip = {}
    for k, t in board["clip"].items():
        clip[k] = (t["cx"] * new_w - t["w"] * new_w / 2, t["cy"] * new_h - t["h"] * new_h / 2, t["w"] * new_w, t["h"] * new_h)
    # the pictures of the page clip, each by its own proportions (when the board places every one of them the engine moves
    # them one by one instead of as a single composite)
    mb = {b["k"]: b for b in master.get("clip_bitmaps", [])}
    cbm = board.get("cbm") or {}
    if mb and all(k in cbm for k, b in mb.items() if not b["backdrop"]):
        clip["bitmaps"] = {k: (t["cx"] * new_w - t["w"] * new_w / 2, t["cy"] * new_h - t["h"] * new_h / 2, t["w"] * new_w,
                               t["h"] * new_h, mb[k]["aspect"], bool(mb[k]["backdrop"])) for k, t in cbm.items()}
    tboard, tmaster = board, master
    mcap = master.get("name_max_page_frac")
    if lib.get("wide_boards_use_widest_master") and not _same_size(board, new_w, new_h):
        # no board of this size from this master: the name block comes from the nearest designer board that has one (any master)
        alt = select_text_board(lib, new_w, new_h, board_type, exclude)
        if alt is not None:
            tboard, tmaster = alt, None
    caps = {}
    skip_files = {exclude} if isinstance(exclude, str) else set(exclude or ())
    bpanel, tpanel = (board.get("clip") or {}).get("panel"), (tboard.get("clip") or {}).get("panel")
    if bpanel and tpanel:
        # a centre-panel design: the white panel is as wide as the designer's board of this size made it (0.27 - 0.41 of the page,
        # not the master's third), the pictures on its edges move with the edge they sit on, and the names fill it
        clip["panel"] = (tpanel["cx"] * new_w - tpanel["w"] * new_w / 2, tpanel["cy"] * new_h - tpanel["h"] * new_h / 2,
                         tpanel["w"] * new_w, tpanel["h"] * new_h)
        if tboard is not board and clip.get("bitmaps"):
            moved = {}
            for k, (x, y, w, h, asp, bd) in clip["bitmaps"].items():
                left = (x + w / 2) / new_w < bpanel["cx"]
                old_edge = bpanel["x"] if left else bpanel["x"] + bpanel["w"]
                new_edge = tpanel["x"] if left else tpanel["x"] + tpanel["w"]
                moved[k] = (x if bd else x + (new_edge - old_edge) * new_w, y, w, h, asp, bd)
            clip["bitmaps"] = moved
        # on a panel narrower than a third of the page the pictures on its corners take a larger share of the low English line: it narrows
        # in step (26x3 ft: panel 0.269 -> 0.54 of her ratio) so it stays between them
        caps = {"name_ta": PANEL_TA_FIT * tpanel["w"], "name_en": PANEL_EN_FIT * tpanel["w"] * min(1.0, tpanel["w"] / PANEL_FULL_EN_W)}
    elif mcap:
        # a master whose white panel stretches with the page records how wide its name zone is (a share of the page)
        caps = {"name_ta": mcap, "name_en": mcap}
    fill = []
    if bpanel and tpanel:
        fill = [b for b in lib["boards"] if (b.get("clip") or {}).get("panel") and abs(b["H"] / tboard["H"] - 1) < 0.02 and b["file"] not in skip_files]
    texts = {k: s for k in ("name_en", "name_ta") if (s := _text_spec(lib, tmaster, tboard, k, new_w, new_h, caps.get(k), fill))}
    if fill:
        _fit_blocks_in_panel(texts, new_h)
    _separate_name_lines(texts, tboard, new_w)
    pics = dict(clip.get("bitmaps") or {})
    kinds = {e["k"]: e["kind"] for e in master["elements"]}
    for oid, (bx, by, bw, bh, k) in boxes.items():            # a bitmap that is an element of the master itself (Agarpathi's product pack)
        if kinds.get(k) == "bitmap" and "_tile" not in oid:
            pics["el_" + oid] = (bx, by, bw, bh)
    if pics:
        _clear_of_pictures(texts, pics, new_w, new_h)
    dist = _ln(board["W"] / board["H"], new_w / new_h) + H_WEIGHT * _ln(board["H"], new_h)
    return {"template": {"file": board["file"], "W": board["W"], "H": board["H"], "exact": _same_size(board, new_w, new_h),
                         "distance": round(dist, 4), "extra": board.get("extra", 0)},
            "boxes": boxes, "clip": clip, "texts": texts, "board_scripts": sorted(tboard.get("texts") or {}),
            "nested_names": bool(lib.get("nested_names_by_spec")), "name_case": lib.get("name_case")}


def preferred_master_file(brand: str | None, new_w: float, new_h: float, available: set[str] | None = None,
                          board_type: str | None = None) -> str | None:
    """Which master FILE the nearest designer board of this brand was built from (a brand can hold several masters, one per design
    style: the standing-box style and the flat-box style of the same shop). Used to pick the registered master before a conversion;
    None when the brand has no library or no board of the target's orientation.
    `available` = the file names of the masters actually uploaded. Given, the search also covers the libraries of other brands (a master
    is recognised by its file / shapes, not by the brand label it was uploaded under) and only boards whose master file is uploaded
    count. Without it the brand's own library alone is used, as before."""
    libs = candidate_libraries(brand) if available is not None else [library(brand)] if brand else []
    have = {a.strip().lower() for a in available} if available is not None else None
    fam, asp = family(new_w, new_h), new_w / new_h
    want_type = norm_type(board_type)
    best = None
    for lib in libs:
        if not lib:
            continue
        if have is not None:
            # an uploaded master drawn at (about) the target's own shape IS the design for that size (10 X 3 for a 10x3 ft board)
            # only for a library that says so (measured per brand: Hangyo's masters ARE one design per shape). Where a brand keeps
            # different DESIGN STYLES at one shape (Agarpathi: a 9 X 3 flat-box master next to the 125 X 48 one, both ~3:1) a master of
            # the same shape is not necessarily the design that board was made from, so the nearest designer board decides there.
            same = [m for m in lib["masters"] if lib.get("wide_boards_use_widest_master") and m["file"].strip().lower() in have
                    and m.get("page") and any(b["master"] == m["id"] for b in lib["boards"])
                    # a board AT LEAST as wide as a master is that master stretched wider - the widest such master, not the narrower
                    # design that merely has a board of that size
                    and asp >= m["page"][0] / m["page"][1] * (1 - SAME_SHAPE_TOL * 1.5)]
            if same:
                return max(same, key=lambda m: m["page"][0] / m["page"][1])["file"]
        for b in lib["boards"]:
            if family(b["W"], b["H"]) != fam:
                continue
            m = next((m for m in lib["masters"] if m["id"] == b["master"]), None)
            if m is None or not compatible(b, m):
                continue
            if have is not None and m["file"].strip().lower() not in have:
                continue
            d = _ln(b["W"] / b["H"], asp) + H_WEIGHT * _ln(b["H"], new_h)
            d = (round(d, 3), 1 if want_type and board_kind(b["file"]) and board_kind(b["file"]) != want_type else 0)
            if best is None or d < best[0]:
                best = (d, m["file"])
    return best[1] if best else None
