"""Correction memory, step 1 (capture): what the designer changed in the editor, as page-fraction boxes.

The engine places art by copying the nearest designer board (`example_layout`). When a designer then fixes the result in the editor,
the fix is knowledge the engine should reuse for the next board of the same size. This module only CAPTURES it: it compares the
scene the engine produced (`base`) with the same scene after the saved edit list (`edited`) and records, for every top-level object
that was moved, resized, hidden or deleted, where it was and where the designer put it - as fractions of the page (origin bottom-left,
like the scene), so the record is independent of the page size and can later be matched against `library.json` boards.

Text edits are not learned (Tamil spelling and names come from the sheet) - they are only counted. Nothing here changes how a board is
generated. Every stored correction is applied to later boards of the same size (there is no approval step).
"""
from __future__ import annotations

import math
import re

MIN_SHIFT_FRAC = 0.002     # a move / resize smaller than this share of the page is nudging noise, not a correction


def _page(scene: dict) -> tuple[float, float]:
    p = scene["page"]
    return float(p["width"]), float(p["height"])


def _frac_box(n: dict, pw: float, ph: float) -> dict:
    return {"cx": round((n["x"] + n["w"] / 2) / pw, 5), "cy": round((n["y"] + n["h"] / 2) / ph, 5),
            "w": round(n["w"] / pw, 5), "h": round(n["h"] / ph, 5)}


def _count_nested(n: dict) -> int:
    return sum(1 + _count_nested(c) for c in n.get("children") or [])


def _signature(n: dict) -> dict:
    """What identifies the object across boards (the same fields example_layout matches masters by): kind, aspect ratio, nesting."""
    h = n["h"] or 1e-9
    return {"kind": n.get("kind") or n.get("type"), "aspect": round(n["w"] / h, 4), "n_desc": _count_nested(n)}


def _top_nodes(scene: dict) -> dict[str, dict]:
    return {n["id"]: n for layer in scene["layers"] for n in layer.get("children", [])}


def _all_nodes(scene: dict) -> dict[str, dict]:
    """Every node at any depth: an object the designer grouped is still there (nested), not deleted. Boxes are absolute."""
    out: dict[str, dict] = {}

    def walk(children):
        for n in children:
            out[n["id"]] = n
            walk(n.get("children") or [])

    for layer in scene["layers"]:
        walk(layer.get("children", []))
    return out


MOVED_RESIZED = "moved+resized"


def _action(moved: bool, resized: bool) -> str:
    if moved and resized:
        return MOVED_RESIZED
    return "moved" if moved else "resized"


def _survivors(node: dict, alive: set[str]) -> bool:
    """True when a descendant of `node` is still on the edited board: the group was UNGROUPED (its content kept), not deleted."""
    return any(c["id"] in alive or _survivors(c, alive) for c in node.get("children") or [])


def _top_change(nid: str, o: dict, n: dict | None, bw: float, bh: float, alive: set[str] = frozenset()) -> dict | None:
    """What the designer did to ONE top-level object (`n` is its edited twin, None when it is gone), or None when nothing worth learning."""
    before = _frac_box(o, bw, bh)

    def entry(after, action):
        return {"id": nid, "signature": _signature(o), "before": before, "after": after, "action": action}

    if n is None:
        return None if _survivors(o, alive) else entry(None, "deleted")
    if o.get("visible", True) and not n.get("visible", True):
        return entry(None, "hidden")
    if o.get("children") and _group_refresh(o, n, bw, bh):
        return None                                          # only its box followed a child that was edited (see diff_nested)
    after = _frac_box(n, bw, bh)
    moved = max(abs(before["cx"] - after["cx"]), abs(before["cy"] - after["cy"])) >= MIN_SHIFT_FRAC
    resized = max(abs(before["w"] - after["w"]), abs(before["h"] - after["h"])) >= MIN_SHIFT_FRAC
    return entry(after, _action(moved, resized)) if moved or resized else None


def diff_scenes(base: dict, edited: dict) -> dict:
    """The designer's corrections between the engine's scene and the edited one.

    Returns {"page_changed": bool, "changes": [...], "text_edits": int}. A change is
    {"id", "signature", "before", "after", "action": "moved" | "resized" | "moved+resized" | "hidden" | "deleted"}.
    A page-size edit makes every box meaningless against the old page, so then no changes are reported.
    """
    bw, bh = _page(base)
    ew, eh = _page(edited)
    if not (math.isclose(bw, ew, rel_tol=1e-6) and math.isclose(bh, eh, rel_tol=1e-6)):
        return {"page_changed": True, "changes": [], "nested": [], "text_edits": 0}
    old, new = _top_nodes(base), _all_nodes(edited)
    alive = set(new)
    changes, text_edits = [], 0
    for nid, o in old.items():
        n = new.get(nid)
        if n is not None and n.get("text") != o.get("text"):
            text_edits += 1
        ch = _top_change(nid, o, n, bw, bh, alive)
        if ch:
            changes.append(ch)
    nested, nested_text_edits = diff_nested(base, edited)
    return {"page_changed": False, "changes": changes, "nested": nested, "text_edits": text_edits + nested_text_edits}


STYLE_KEYS = ("font", "bold", "italic", "underline", "align", "line_spacing", "char_spacing")
SIZE_TOL = 0.02            # a font size counts as an explicit change when it differs this much from what the box resize alone gives


def _nodes_with_parents(scene: dict) -> dict[str, tuple[dict, str | None]]:
    """Every node at any depth -> (node, id of its parent group / PowerClip, None for a top-level object)."""
    out: dict[str, tuple[dict, str | None]] = {}

    def walk(children, parent):
        for n in children:
            out[n["id"]] = (n, parent)
            walk(n.get("children") or [], n["id"])

    for layer in scene["layers"]:
        walk(layer.get("children", []), None)
    return out


def _depth(nodes: dict, nid: str) -> int:
    d = 0
    while nodes[nid][1] is not None:
        nid = nodes[nid][1]
        d += 1
    return d


def _path(nodes: dict, nid: str) -> list[str]:
    out = []
    while nodes[nid][1] is not None:
        nid = nodes[nid][1]
        out.append(nid)
    return out[::-1]


def _box(n: dict) -> dict:
    return {"x": n["x"], "y": n["y"], "w": n["w"], "h": n["h"]}


def _text_block(n: dict) -> bool:
    """A text object, or a group / PowerClip holding nothing but text objects (the shop-name block): its width is the TEXT's width, which
    differs from shop to shop, so it is placed by centre and height - never by the designer's width."""
    kids = n.get("children") or []
    return bool(n.get("text")) if not kids else all(_text_block(c) for c in kids)


def _carried(child: dict, parent_before: dict, parent_after: dict) -> dict:
    """Where a child ends up when only its parent was moved / resized (a group's children scale with it)."""
    sx = parent_after["w"] / parent_before["w"] if parent_before["w"] else 1.0
    sy = parent_after["h"] / parent_before["h"] if parent_before["h"] else 1.0
    return {"x": parent_after["x"] + (child["x"] - parent_before["x"]) * sx,
            "y": parent_after["y"] + (child["y"] - parent_before["y"]) * sy, "w": child["w"] * sx, "h": child["h"] * sy}


def _same(a: dict, b: dict, pw: float, ph: float) -> bool:
    """Two boxes agree: centre and size within the nudging threshold (a share of the page)."""
    return (max(abs(a["x"] + a["w"] / 2 - b["x"] - b["w"] / 2) / pw, abs(a["y"] + a["h"] / 2 - b["y"] - b["h"] / 2) / ph) < MIN_SHIFT_FRAC
            and max(abs(a["w"] - b["w"]) / pw, abs(a["h"] - b["h"]) / ph) < MIN_SHIFT_FRAC)


def _group_refresh(old: dict, new: dict, pw: float, ph: float) -> bool:
    """A group's box follows its children, so editing ONE child changes the group's box without the other children moving at all. That is a
    refresh, not a group move / resize (which carries every child along). True when more of the group's children stayed put than were
    carried along with its box change."""
    kids = {c["id"]: c for c in new.get("children") or []}
    stayed = carried = 0
    for c in old.get("children") or []:
        nc = kids.get(c["id"])
        if nc is None:
            continue
        along, here = _carried(c, _box(old), _box(new)), _box(c)
        if _same(along, here, pw, ph):
            continue                                         # the group's change does not tell the two apart for this child
        stayed += _same(_box(nc), here, pw, ph)
        carried += _same(_box(nc), along, pw, ph)
    return stayed > carried


def _style_changes(old: dict, new: dict) -> dict:
    """What the designer set on a text object beyond its box: font, bold / italic / underline, alignment, line / character spacing, and
    a font size that is not just the box resize. The text CONTENT is never learned (names come from the sheet)."""
    a, b = old.get("text") or {}, new.get("text") or {}
    out = {k: b[k] for k in STYLE_KEYS if k in b and b.get(k) != a.get(k)}
    if a.get("size_pt") and b.get("size_pt") and old["h"] and new["h"]:
        follows = a["size_pt"] * (new["h"] / old["h"])
        if abs(b["size_pt"] / follows - 1) > SIZE_TOL:
            out["size_pt"] = b["size_pt"]
    return out


def _nested_motion(o: dict, n: dict, parent_before: dict, parent_after: dict, pw: float, ph: float) -> tuple[bool, bool]:
    """(moved, resized) for a nested object beyond what its parent's own move / resize explains."""
    got = _box(n)
    along, here = _carried(o, _box(parent_before), _box(parent_after)), _box(o)
    refreshed = bool(o.get("children")) and _group_refresh(o, n, pw, ph)
    if _same(got, along, pw, ph) or _same(got, here, pw, ph) or refreshed:
        return False, False                                  # explained by the parent (carried along, or the parent's box merely refreshed)
    shifted = max(abs(got["x"] + got["w"] / 2 - along["x"] - along["w"] / 2) / pw,
                  abs(got["y"] + got["h"] / 2 - along["y"] - along["h"] / 2) / ph) >= MIN_SHIFT_FRAC
    resized = max(abs(got["w"] - along["w"]) / pw, abs(got["h"] - along["h"]) / ph) >= MIN_SHIFT_FRAC
    return shifted, resized


def _content_of(n: dict):
    return (n.get("text") or {}).get("content")


_LINE_BREAK = re.compile(r"\r\n|\r|\n")


def _line_count(text) -> int:
    text = str(text or "")
    return len(_LINE_BREAK.split(text)) if text else 1


def _single_text_leaf(n: dict) -> dict | None:
    """The one text object inside `n` (n itself when it is text), None when there is no text or several."""
    found: list[dict] = []

    def walk(x: dict) -> None:
        if x.get("text"):
            found.append(x)
        for c in x.get("children") or []:
            walk(c)

    walk(n)
    return found[0] if len(found) == 1 else None


def name_chars(text) -> int:
    """How long a name is, ignoring line breaks and runs of spaces."""
    return len(" ".join(str(text or "").split()))


def _line_change(old: dict, new: dict) -> int | None:
    """The number of lines the designer set a name block on, when that differs from the engine's (None when unchanged or not one text)."""
    a, b = _single_text_leaf(old), _single_text_leaf(new)
    if a is None or b is None:
        return None
    before, after = _line_count(_content_of(a)), _line_count(_content_of(b))
    return after if after != before else None


def _nested_entry(nid: str, parent: str, old: dict, new: dict, pw: float, ph: float) -> dict | None:
    """The recorded change for one nested object (box beyond its parent's own move / resize, and / or text style), or None."""
    o, n = old[nid][0], new[nid][0]
    shifted, resized = _nested_motion(o, n, old[parent][0], new[parent][0], pw, ph)
    style = _style_changes(o, n) if o.get("text") and n.get("text") else {}
    lines = _line_change(o, n) if _text_block(o) else None
    if not (shifted or resized or style or lines):
        return None
    action = _action(shifted, resized) if shifted or resized else "styled"
    entry = {"id": nid, "parent": parent, "path": _path(old, nid), "signature": _signature(o), "action": action,
             "before": _frac_box(o, pw, ph), "after": _frac_box(n, pw, ph)}
    if _text_block(o):
        entry["text"] = True
    if style:
        entry["style"] = style
    if lines:
        entry["lines"] = lines          # how many lines she set the name on: the next board's name gets the same number
        leaf = _single_text_leaf(n)
        entry["name_chars"] = name_chars(_content_of(leaf)) if leaf else 0     # ... but only a name about as long as hers
    return entry


def _gone_entry(nid: str, parent: str, old: dict, o: dict, action: str, pw: float, ph: float) -> dict:
    """A nested object the designer hid or deleted (no 'after' box)."""
    return {"id": nid, "parent": parent, "path": _path(old, nid), "signature": _signature(o), "action": action,
            "before": _frac_box(o, pw, ph), "after": None}


def diff_nested(base: dict, edited: dict) -> tuple[list, int]:
    """The designer's changes to objects INSIDE groups / PowerClips (the shop-name block and its text usually sit in the master's big group):
    for every nested object still in the same parent, its box compared with where its parent's own move / resize alone would leave it, plus
    text style changes. Boxes are page fractions like the top-level ones. Returns (changes, number of text contents edited)."""
    pw, ph = _page(base)
    old, new = _nodes_with_parents(base), _nodes_with_parents(edited)
    out, text_edits = [], 0
    alive = set(new)
    for nid, (o, parent) in old.items():
        entry, edited_text = _nested_change(nid, o, parent, old, new, alive, pw, ph)
        text_edits += edited_text
        if entry:
            out.append(entry)
    return out, text_edits


def _nested_change(nid: str, o: dict, parent: str | None, old: dict, new: dict, alive: set[str], pw: float, ph: float) -> tuple[dict | None, int]:
    """(the recorded change for one nested object or None, 1 when its text content was edited)."""
    if parent is None:
        return None, 0                                       # top-level: handled in diff_scenes
    if nid not in new:
        if parent in new and not _survivors(o, alive):
            return _gone_entry(nid, parent, old, o, "deleted", pw, ph), 0     # the object itself was deleted (not just ungrouped)
        return None, 0
    if new[nid][1] != parent:
        return None, 0                                       # moved to another parent: not learned
    n = new[nid][0]
    if o.get("visible", True) and not n.get("visible", True):
        return _gone_entry(nid, parent, old, o, "hidden", pw, ph), 0
    return _nested_entry(nid, parent, old, new, pw, ph), int(_content_of(n) != _content_of(o))


SAME_SIZE_TOL = 0.005      # a correction applies to a board whose page is within 0.5 % of the one it was made on
MATCH_TOL = 0.02           # a recorded "before" box matches a placed object within 2 % of the page (centre and size)
IN_PLACE_TOL = 0.002       # an object already within 0.2 % of the page of the learned box needs no move
CONSENSUS_TOL = 0.02       # several designers agree on an object when their boxes differ by less than this share of the page
TEXT_ROLES = ("text", "shopname")
APPLIED_ACTIONS = ("moved", "resized", "moved+resized")     # re-applied while the board is placed (engine time)
SCENE_ACTIONS = ("hidden", "deleted")                       # re-applied afterwards as editor ops (visibility / delete)
LEARNED_ACTIONS = APPLIED_ACTIONS + SCENE_ACTIONS
RECENT_WEIGHT = 0.8        # an object's learned box = 80 % the NEWEST editor correction ...
AVERAGE_WEIGHT = 0.2       # ... + 20 % the MEDIAN (3+ records; the plain average of two) of every editor correction that names it
NEARBY_ASPECT_TOL = 0.03   # a correction made on a page of nearly the same shape (aspect within 3 %) ...
NEARBY_AREA_TOL = 0.25     # ... and size (area within 25 %) still describes a board that has no record of its own size
NEARBY_WEIGHT = 0.5        # a nearby record counts half in the averaged part; a record of the exact size always leads
LINE_MIN_FRAC = 0.75       # a name shorter than this share of the name she broke into lines stays on one line
FIT_FRAC = 0.92            # the export keeps a learned name inside this share of her box width (measured by CorelDRAW, not estimated)
FAMILY_MAX_ASPECT_LN = 0.7   # a record from another board size still teaches this one when the pages are within this log-aspect distance ...
FAMILY_MAX_AREA_LN = 1.6     # ... and this log-area distance (the same master, so the same design family)
FAMILY_TAU = 0.6             # how fast a size's say fades with distance: weight = exp(-distance / tau)
FAMILY_STRENGTH = 0.85       # an object takes this share of the shift designers made on another size (the rest stays where the engine put it)
FAMILY_MATCH_TOL = 0.10      # a recorded object is the placed one when its box is this close (share of the page) ...
FAMILY_ASPECT_TOL = 0.10     # ... and its shape (log aspect) this similar - the sizes differ, so the loose rule of the two
IDENTITY_CAPTURE_TOL = 0.005  # when a correction is recorded, its object is tied to the master element whose placed box is within this share of the page
IDENTITY_SHIFT_TOL = 0.02     # an object found by identity that sits within this of where the record saw it takes the recorded box as it is;
                              # farther away (the engine placed it differently since) it takes the recorded SHIFT instead
TRUST_LEAD = 0.5           # only a record trusted at least this much may LEAD a blend (the newest such record); distrusted ones only steady it
QUARANTINE_TRUST = 0.3     # a record trusted less than this ...
NUDGE_SHIFT = 0.02         # a designer who moved a learned object by less than this (share of the page) nudged it: the lesson was PARTLY right (half kept, half disputed)
REVERT_WEIGHT = 2.0        # a designer who put a learned object back where the ENGINE had it says the lesson was wrong: it counts double
AUTO_MIN_CONFIDENCE = 0.50  # when learning applies by itself on Convert (no button) only changes this sure are applied
QUARANTINE_DISPUTES = 2    # ... after at least this many designers corrected what it taught, is left out until confirmations win it back


# ---------------------------------------------------------------- trust: did the designers keep what a record taught?
#
# A record is a lesson. When a later board is made with it (Corel Intelligence) the designer either keeps the result (the lesson is
# CONFIRMED) or corrects it again (DISPUTED). `learn_from_save` measures that on every editor save; `trust` turns the tallies into a
# weight: (confirmed + 1) / (confirmed + disputed + 2), so an unproven record sits at 0.5, a confirmed one climbs towards 1 and a record that
# keeps being corrected falls towards 0 and is finally paused (`quarantined`). Tallies are kept per BOARD (the board whose save produced
# them), so autosaving the same board many times never counts twice.

def _totals(fb: dict) -> tuple[float, float]:
    boards = fb.get("boards") or {}
    if boards:
        return sum(float(v[0]) for v in boards.values()), sum(float(v[1]) for v in boards.values())
    return max(0.0, float(fb.get("confirmed", 0) or 0)), max(0.0, float(fb.get("disputed", 0) or 0))


def feedback_of(rec: dict) -> dict:
    """{"confirmed", "disputed"} of a stored record (a row from db.list_corrections or the bare record)."""
    c, d = _totals((rec.get("record") or rec).get("feedback") or {})
    return {"confirmed": c, "disputed": d}


def trust(rec: dict) -> float:
    fb = feedback_of(rec)
    return (fb["confirmed"] + 1) / (fb["confirmed"] + fb["disputed"] + 2)


def quarantined(rec: dict) -> bool:
    return feedback_of(rec)["disputed"] >= QUARANTINE_DISPUTES and trust(rec) < QUARANTINE_TRUST


def usable_records(rows: list[dict], brand: str | None, master_file: str | None, w_mm: float, h_mm: float,
                   board_type: str | None, family: bool = False) -> list[dict]:
    """The stored corrections that describe THIS board: same brand, same master file, same page size (within `SAME_SIZE_TOL`) and, when
    both name one, the same TYPE OF BOARD. Every stored record is used.
    Newest first, so a later correction wins a clash."""
    want = (board_type or "").strip().lower()
    rows = [r for r in rows if not quarantined(r)]           # a lesson the designers keep correcting is paused (see `trust`)
    out = [r for r in rows if _record_fits(r, brand, master_file, w_mm, h_mm, want)]
    out = sorted(out, key=lambda r: r.get("updated_at") or 0, reverse=True)
    near = [{**r, "nearby": True} for r in rows if _record_fits(r, brand, master_file, w_mm, h_mm, want, nearby=True)]
    near.sort(key=lambda r: r.get("updated_at") or 0, reverse=True)
    fam: list[dict] = []
    if family:                                              # other sizes of the same master: they only speak for objects nothing else covers
        for r in rows:
            d = family_distance(r, brand, master_file, w_mm, h_mm, want)
            if d is not None:
                fam.append({**r, "family": True, "_dist": d})
        fam.sort(key=lambda r: r["_dist"])
    return out + near + fam                                 # exact-size records lead; nearby ones only steady them; family ones stand in


def _same_size(a, b) -> bool:
    return math.isclose(float(a), float(b), rel_tol=SAME_SIZE_TOL)


def _lower(v) -> str:
    return (v or "").strip().lower()


def _nearby_size(r: dict, w_mm: float, h_mm: float) -> bool:
    """The record's page is not this size but nearly the same shape (`NEARBY_ASPECT_TOL`) and area (`NEARBY_AREA_TOL`)."""
    try:
        rw, rh = float(r["page_w_mm"]), float(r["page_h_mm"])
        if min(rw, rh, w_mm, h_mm) <= 0:
            return False
        return abs(math.log((rw / rh) / (w_mm / h_mm))) <= NEARBY_ASPECT_TOL and abs((rw * rh) / (w_mm * h_mm) - 1.0) <= NEARBY_AREA_TOL
    except (KeyError, TypeError, ValueError):
        return False


def _record_fits(r: dict, brand: str | None, master_file: str | None, w_mm: float, h_mm: float, want_type: str, nearby: bool = False) -> bool:
    """One stored record describes this board: same brand, same master file, same page size (`nearby`: a nearly same-shaped, nearly same-sized
    page that is NOT the same size) and (when both name one) board type."""
    if (r.get("brand") or "") != (brand or ""):
        return False
    if _lower(r.get("master_file")) != _lower(master_file):
        return False
    same = _same_size(r["page_w_mm"], w_mm) and _same_size(r["page_h_mm"], h_mm)
    if nearby:
        if same or not _nearby_size(r, w_mm, h_mm):
            return False
    elif not same:
        return False
    have = _lower(r.get("board_type"))
    return not (want_type and have and want_type != have)


def family_distance(r: dict, brand: str | None, master_file: str | None, w_mm: float, h_mm: float, want_type: str) -> float | None:
    """How far another board size of the same brand / master / board type is from this one (log-aspect distance + half the log-area distance),
    or None when it is the same or a nearby size (those have their own rule), a different master, or too far to say anything."""
    if (r.get("brand") or "") != (brand or "") or _lower(r.get("master_file")) != _lower(master_file):
        return None
    have = _lower(r.get("board_type"))
    if want_type and have and want_type != have:
        return None
    try:
        rw, rh = float(r["page_w_mm"]), float(r["page_h_mm"])
        if min(rw, rh, w_mm, h_mm) <= 0:
            return None
        if (_same_size(rw, w_mm) and _same_size(rh, h_mm)) or _nearby_size(r, w_mm, h_mm):
            return None
        da, dv = abs(math.log((rw / rh) / (w_mm / h_mm))), abs(math.log((rw * rh) / (w_mm * h_mm)))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    if da > FAMILY_MAX_ASPECT_LN or dv > FAMILY_MAX_AREA_LN:
        return None
    return da + 0.5 * dv


def _median(v: list[float]) -> float:
    v = sorted(v)
    return v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2


BOX_KEYS = ("cx", "cy", "w", "h")


def _nearest_placed(placed: list, taken: set[str], b: dict, new_w: float, new_h: float):
    """The not yet used, non-text placed object whose box is nearest the recorded `b` (within MATCH_TOL), or None."""
    best = None
    for p in placed:
        if p.id in taken or p.role in TEXT_ROLES or p.w <= 0 or p.h <= 0:
            continue
        d = max(abs((p.x + p.w / 2) / new_w - b["cx"]), abs((p.y + p.h / 2) / new_h - b["cy"]),
                abs(p.w / new_w - b["w"]), abs(p.h / new_h - b["h"]))
        if d <= MATCH_TOL and (best is None or d < best[0]):
            best = (d, p)
    return best


def _is_editor(rec: dict) -> bool:
    """A record saved from the editor (a designer corrected a board in this app); False for one learned from a designer's own file."""
    return not (rec.get("record") or rec).get("source")


def _collect_votes(placed: list, new_w: float, new_h: float, records: list[dict]) -> tuple[dict, int]:
    """({placed id: [(recorded "after" box, shop id, from the editor)]}, number of recorded changes that matched nothing or are not re-applied)."""
    votes: dict[str, list[tuple[dict, str, bool]]] = {}
    skipped = 0
    for rec in records:
        skipped += _record_votes(rec, placed, new_w, new_h, votes)
    return votes, skipped


def _record_votes(rec: dict, placed: list, new_w: float, new_h: float, votes: dict) -> int:
    """Add one record's votes to `votes`; returns how many of its changes matched nothing or are not re-applied."""
    sid = rec.get("shop_id") or (rec.get("record") or {}).get("shop_id")
    if rec.get("family"):
        return _family_votes(rec, sid, placed, new_w, new_h, votes)
    taken: set[str] = set()                                   # within one record an object answers one change only
    skipped = 0
    for ch in (rec.get("record") or rec).get("changes", []):
        if ch["action"] in SCENE_ACTIONS:
            continue                                          # replayed afterwards as editor ops (nested_ops)
        best = None
        by_id = False
        if ch["action"] in APPLIED_ACTIONS and (ch.get("signature") or {}).get("kind") != "text":
            ident = _find_identity(placed, taken, ch.get("master_id"))
            if ident is not None:                                  # the same master element, wherever the engine put it
                best, by_id = (0.0, ident), True
            else:
                best = _nearest_placed(placed, taken, ch["before"], new_w, new_h)
        if best is None:
            skipped += 1
            continue
        taken.add(best[1].id)
        after = {**ch["after"], "_t": trust(rec)}
        if by_id:
            after["_id"] = True
            p = best[1]
            moved = max(abs((p.x + p.w / 2) / new_w - ch["before"]["cx"]), abs((p.y + p.h / 2) / new_h - ch["before"]["cy"]),
                        abs(p.w / new_w - ch["before"]["w"]), abs(p.h / new_h - ch["before"]["h"]))
            if moved > IDENTITY_SHIFT_TOL:                         # placed elsewhere than when the designer fixed it: repeat her SHIFT, not her box
                b, a = ch["before"], ch["after"]
                sw = (a["w"] / b["w"]) if b["w"] else 1.0
                sh = (a["h"] / b["h"]) if b["h"] else 1.0
                after.update({"cx": (p.x + p.w / 2) / new_w + (a["cx"] - b["cx"]), "cy": (p.y + p.h / 2) / new_h + (a["cy"] - b["cy"]),
                              "w": p.w / new_w * sw, "h": p.h / new_h * sh})
        if rec.get("nearby"):
            after["_w"] = NEARBY_WEIGHT
        votes.setdefault(best[1].id, []).append((after, sid, _is_editor(rec)))
    return skipped


def _base_id(pid) -> str:
    """A placed object's master element id: tile copies are named "<id>_tile<n>"."""
    return str(pid).split("_tile")[0]


def _find_identity(placed: list, taken: set[str], master_id) -> object | None:
    """The placed object that IS the recorded master element (the same layout id), or None - also None when the element was copied
    (tiles) or is text, because then one id names several objects and only the box can tell which one the designer meant."""
    if master_id is None:
        return None
    hits = [p for p in placed if _base_id(p.id) == str(master_id)]
    if len(hits) != 1:
        return None
    p = hits[0]
    return None if p.id in taken or p.role in TEXT_ROLES or p.w <= 0 or p.h <= 0 else p


def attach_identity(changes: list[dict], objects: list[dict] | None, pw: float, ph: float) -> int:
    """Tie each top-level change to the master element it is about, so a later board can find the same element even when the engine places it
    differently (another size, a better layout rule). `objects` = the report's placed objects of the board the designer corrected (id, role, x, y,
    w, h in mm); an element qualifies when exactly one object has its box within IDENTITY_CAPTURE_TOL of the change's `before` and its id is not
    shared by tile copies. Sets `master_id` on the change; returns how many were tied."""
    if not objects or pw <= 0 or ph <= 0:
        return 0
    counts: dict[str, int] = {}
    for o in objects:
        counts[_base_id(o.get("id"))] = counts.get(_base_id(o.get("id")), 0) + 1
    tied = 0
    for c in changes:
        b = c.get("before")
        if c.get("master_id") is not None or not b or (c.get("signature") or {}).get("kind") == "text":
            continue
        close = []
        for o in objects:
            if o.get("role") in TEXT_ROLES or not o.get("w") or not o.get("h"):
                continue
            d = max(abs((o["x"] + o["w"] / 2) / pw - b["cx"]), abs((o["y"] + o["h"] / 2) / ph - b["cy"]), abs(o["w"] / pw - b["w"]), abs(o["h"] / ph - b["h"]))
            if d <= IDENTITY_CAPTURE_TOL:
                close.append(o)
        if len(close) == 1 and counts.get(_base_id(close[0]["id"])) == 1:
            c["master_id"] = _base_id(close[0]["id"])
            tied += 1
    return tied


def _family_match(placed: list, taken: set[str], ch: dict, new_w: float, new_h: float):
    """The placed object a change recorded on ANOTHER board size is about: nearest box within FAMILY_MATCH_TOL whose shape (aspect) agrees."""
    b, aspect = ch["before"], (ch.get("signature") or {}).get("aspect")
    best = None
    for p in placed:
        if p.id in taken or p.role in TEXT_ROLES or p.w <= 0 or p.h <= 0:
            continue
        if aspect and abs(math.log((p.w / p.h) / aspect)) > FAMILY_ASPECT_TOL:
            continue
        d = max(abs((p.x + p.w / 2) / new_w - b["cx"]), abs((p.y + p.h / 2) / new_h - b["cy"]))
        if d <= FAMILY_MATCH_TOL and (best is None or d < best[0]):
            best = (d, p)
    return best[1] if best else None


def _family_votes(rec: dict, sid, placed: list, new_w: float, new_h: float, votes: dict) -> int:
    """Votes from a record made on another size of the same master: its shift (after - before, as page fractions) is added to where the engine
    placed the matching object here, damped by FAMILY_STRENGTH and weighted by how near that size is. Changes that match nothing are NOT counted
    as skipped (a different size is expected to lack some of them). Returns 0."""
    wgt = math.exp(-float(rec.get("_dist") or 0.0) / FAMILY_TAU)
    taken: set[str] = set()
    for ch in (rec.get("record") or rec).get("changes", []):
        b, a = ch.get("before"), ch.get("after")
        if ch["action"] not in APPLIED_ACTIONS or (ch.get("signature") or {}).get("kind") == "text" or not b or not a:
            continue
        p = _find_identity(placed, taken, ch.get("master_id"))
        if p is not None and (ch.get("signature") or {}).get("aspect") and abs(math.log((p.w / p.h) / ch["signature"]["aspect"])) > 3 * FAMILY_ASPECT_TOL:
            p = None                                               # the same id but a very different shape: not the element she fixed
        by_id = p is not None
        if p is None:
            p = _family_match(placed, taken, ch, new_w, new_h)
        if p is None:
            continue
        taken.add(p.id)
        k = FAMILY_STRENGTH
        sw = (a["w"] / b["w"]) if b["w"] else 1.0
        sh = (a["h"] / b["h"]) if b["h"] else 1.0
        box = {"cx": (p.x + p.w / 2) / new_w + k * (a["cx"] - b["cx"]), "cy": (p.y + p.h / 2) / new_h + k * (a["cy"] - b["cy"]),
               "w": p.w / new_w * (1 + k * (sw - 1)), "h": p.h / new_h * (1 + k * (sh - 1)), "_t": trust(rec), "_w": wgt, "_fam": True}
        if by_id:
            box["_id"] = True
        votes.setdefault(p.id, []).append((box, sid, _is_editor(rec)))
    return 0


def blend_boxes(boxes: list[dict]) -> dict:
    """The learned box from the editor corrections that name an object, NEWEST FIRST: 80 % the newest + 20 % the average of all of them.
    Records the designers keep correcting (`_t` below TRUST_LEAD) neither lead nor steady the blend while a trusted one is available."""
    pool = [b for b in boxes if b.get("_t", 1.0) >= TRUST_LEAD] or boxes
    if len(pool) == 1:
        return {k: pool[0][k] for k in BOX_KEYS}                 # one correction: exactly as the designer made it
    if len(pool) >= 3:
        mid = {k: _median([b[k] for b in pool]) for k in BOX_KEYS}       # the median: one mistaken edit cannot drag the steady part
    else:
        w = [b.get("_w", 1.0) * b.get("_t", 1.0) for b in pool]  # two records: the weighted average - a nearby-size record counts half
        mid = {k: sum(b[k] * x for b, x in zip(pool, w)) / sum(w) for k in BOX_KEYS}
    return {k: round(RECENT_WEIGHT * pool[0][k] + AVERAGE_WEIGHT * mid[k], 6) for k in BOX_KEYS}


def _decide_box(votes: list[tuple[dict, str, bool]]) -> tuple[dict | None, list[str]]:
    """(the box an object takes, the shop ids behind it). Corrections saved from the editor beat the rest and are blended 8:2 - 80 % the NEWEST
    (`records` come newest first) and 20 % the average of all of them (`blend_boxes`) - so a fresh fix leads and older ones steady it instead of
    clashing. Without one (corrections learned from designer files) the boxes must agree - see `_agreed_box`."""
    family = [(box, sid) for box, sid, _ in votes if box.get("_fam")]
    votes = [v for v in votes if not v[0].get("_fam")]
    if not votes and family:                                  # no record of this size / a nearby one names the object: other sizes decide
        wts = [b["_w"] * b["_t"] for b, _ in family]
        total = sum(wts) or 1.0
        return {k: round(sum(b[k] * x for (b, _), x in zip(family, wts)) / total, 6) for k in BOX_KEYS}, [sid for _, sid in family]
    editors = [(box, sid) for box, sid, editor in votes if editor]
    if editors:
        return blend_boxes([b for b, _ in editors]), [sid for _, sid in editors]
    agreed = _agreed_box([b for b, _, _ in votes])
    return agreed, [sid for _, sid, _ in votes]


def _agreed_box(boxes: list[dict]) -> dict | None:
    """One answer from the designers' boxes for an object: the box itself, or the median when they agree within CONSENSUS_TOL; None when not."""
    if len(boxes) > 1 and any(max(a[k] for a in boxes) - min(a[k] for a in boxes) > CONSENSUS_TOL for k in BOX_KEYS):
        return None
    return {k: _median([x[k] for x in boxes]) for k in BOX_KEYS}


# ---------------------------------------------------------------- confidence + quality gate
#
# Every learned change gets a CONFIDENCE (0-1) before it is applied: what kind of record taught it (this size's editor correction > a designer
# file > a nearby size > another size, which fades with distance), that record's trust, whether the element was found by identity, how many
# records agree (noisy-OR) and a penalty per record that disagrees. Changes below APPLY_MIN_CONFIDENCE are HELD BACK (reported, not applied).
# After the changes are applied a quality gate looks at the board and UNDOES any learned move that would push an object off the page or onto
# another object it did not touch before (lowest confidence first), so a bad lesson cannot make a board worse than the engine's own layout.

APPLY_MIN_CONFIDENCE = 0.20   # tuned with tools/intelligence_loo.py --sweep: 0.2 keeps the cross-size gain, 0.3 held back changes that were good (see CLAUDE.md)
BASE_CONFIDENCE = {"editor": 0.90, "seed": 0.75, "nearby": 0.60}
FAMILY_CONFIDENCE = 0.70      # x the size's weight exp(-distance / FAMILY_TAU)
IDENTITY_BONUS = 0.05
DISAGREE_PENALTY = 0.75       # x per record whose box differs from the decided one by more than CONSENSUS_TOL
GATE_PAGE_TOL = 0.005         # an object may stick out of the page by this share (the engine itself does so slightly) ...
GATE_NEW_OVERLAP = 0.25       # ... and a learned move may add at most this share of the smaller object's area as overlap with another object
GATE_SKIP_ROLES = ("bg", "frame")


def _vote_confidence(box: dict, editor: bool) -> float:
    if box.get("_fam"):
        base = FAMILY_CONFIDENCE * float(box.get("_w", 0.5))
    elif box.get("_w"):
        base = BASE_CONFIDENCE["nearby"]
    else:
        base = BASE_CONFIDENCE["editor" if editor else "seed"]
    c = base * (0.5 + 0.5 * float(box.get("_t", 0.5))) + (IDENTITY_BONUS if box.get("_id") else 0.0)
    return max(0.0, min(0.99, c))


def confidence_of(votes: list, decided: dict) -> float:
    """How far to trust the box decided for one object from its `votes` ([(box, shop id, from the editor)]). The decided box may be a blend, so
    "agreeing" is measured against the vote nearest to it (the anchor): votes within CONSENSUS_TOL of the anchor agree, the rest disagree."""
    if not votes:
        return 0.0
    anchor = min((v[0] for v in votes), key=lambda bx: max(abs(bx[k] - decided[k]) for k in BOX_KEYS))
    agree, off = [], 0
    for box, _, editor in votes:
        if max(abs(box[k] - anchor[k]) for k in BOX_KEYS) <= CONSENSUS_TOL:
            agree.append(_vote_confidence(box, editor))
        else:
            off += 1
    if not agree:
        return 0.0
    miss = 1.0
    for c in agree:
        miss *= 1.0 - c
    return round(min(0.99, (1.0 - miss) * DISAGREE_PENALTY ** off), 3)


def _inter(a, b) -> float:
    w = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
    h = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
    return max(0.0, w) * max(0.0, h)


class _Box:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


def _breaks_board(p, before: _Box, placed: list, new_w: float, new_h: float, prior: dict | None = None) -> str | None:
    """Why the learned move of `p` must be undone ("leaves the page" / "overlaps another object"), or None when the board is no worse for it.
    `prior` = {id: box before any learned move}: an overlap is compared with the one the SAME two objects had before, so pieces that moved
    together (a logo made of many shapes) never count as overlapping each other."""
    prior = prior or {}
    tx, ty = GATE_PAGE_TOL * new_w, GATE_PAGE_TOL * new_h

    def outside(b) -> bool:
        return b.x < -tx or b.y < -ty or b.x + b.w > new_w + tx or b.y + b.h > new_h + ty

    if outside(p) and not outside(before):
        return "leaves the page"
    for o in placed:
        if o is p or o.role in GATE_SKIP_ROLES or o.w <= 0 or o.h <= 0:
            continue
        smaller = min(p.w * p.h, o.w * o.h)
        if smaller > 0 and _inter(p, o) - _inter(before, prior.get(o.id, o)) > GATE_NEW_OVERLAP * smaller:
            return "overlaps another object"
    return None


def apply_to_placed(placed: list, new_w: float, new_h: float, records: list[dict], min_confidence: float = APPLY_MIN_CONFIDENCE,
                    details: list | None = None) -> dict:
    """Move/resize the engine's placed objects to where designers put them on a board of this size.

    Each recorded move/resize is matched to a placed object - by master element identity when the record knows it, else the one whose box is
    nearest the recorded "before" box (the engine produced that box for the same master and size). One designer's correction is applied as it
    is. When several records corrected the same object their "after" boxes are blended (editor corrections) or must agree (designer-file
    records; "conflicting" when they do not). Records of other sizes of the same master move an object only when nothing else names it.
    Each change carries a confidence; those below `min_confidence` are held back and, once applied, a change that would push an object off
    the page or onto another object is undone (the quality gate). Text objects are never touched; hidden / deleted objects are not re-applied.
    Returns {"applied", "skipped", "conflicting", "records": [shop ids that contributed], optional "held_back", "gated", "family", "identity",
    "in_place", "avg_confidence"}. `details` (a list) receives one dict per decided object: id, confidence, outcome - for tools and tests.
    """
    votes, skipped = _collect_votes(placed, new_w, new_h, records)
    by_id = {p.id: p for p in placed}
    conflicting = in_place = from_family = by_identity = held_back = 0
    used: list[str] = []
    todo = []                                                 # (confidence, placed object, box, votes, source shop ids)
    for pid, vs in votes.items():
        a, sids = _decide_box(vs)
        if a is None:
            conflicting += 1
            continue
        p = by_id[pid]
        conf = confidence_of(vs, a)
        if _already_there(p, a, new_w, new_h):
            in_place += 1
            used.extend(sid for sid in sids if sid not in used)
            if details is not None:
                details.append({"id": pid, "confidence": conf, "outcome": "in_place"})
            continue
        if conf < min_confidence:
            held_back += 1
            if details is not None:
                details.append({"id": pid, "confidence": conf, "outcome": "held_back"})
            continue
        todo.append((conf, p, a, vs, sids))
    before = {p.id: _Box(p.x, p.y, p.w, p.h) for _, p, _, _, _ in todo}
    for conf, p, a, vs, sids in todo:
        p.w, p.h = a["w"] * new_w, a["h"] * new_h
        p.x, p.y = a["cx"] * new_w - p.w / 2, a["cy"] * new_h - p.h / 2
        p.warnings.append("moved like designers corrected other sizes of this master (Corel Intelligence)" if all(v[0].get("_fam") for v in vs)
                          else "moved to where a designer corrected this board size (Corel Intelligence)")
    gated = 0
    applied_conf: list[float] = []
    for conf, p, a, vs, sids in sorted(todo, key=lambda t: t[0]):          # the least certain change is the first to go
        why = _breaks_board(p, before[p.id], placed, new_w, new_h, before)
        if why:
            b = before[p.id]
            p.x, p.y, p.w, p.h = b.x, b.y, b.w, b.h
            p.warnings[-1] = f"a learned move was undone: it would make this object {why} (Corel Intelligence)"
            gated += 1
            if details is not None:
                details.append({"id": p.id, "confidence": conf, "outcome": "gated", "why": why})
            continue
        applied_conf.append(conf)
        used.extend(sid for sid in sids if sid not in used)
        from_family += all(v[0].get("_fam") for v in vs)
        by_identity += any(v[0].get("_id") for v in vs)
        if details is not None:
            details.append({"id": p.id, "confidence": conf, "outcome": "applied"})
    out = {"applied": len(applied_conf), "skipped": skipped, "conflicting": conflicting, "records": used}
    if in_place:
        out["in_place"] = in_place          # learned boxes the engine already produced: nothing to move
    if held_back:
        out["held_back"] = held_back        # changes below the confidence floor: not applied
    if gated:
        out["gated"] = gated                # changes undone by the quality gate (off the page / onto another object)
    if from_family:
        out["family"] = int(from_family)    # objects moved because of corrections made on OTHER sizes of this master
    if by_identity:
        out["identity"] = int(by_identity)  # objects found as the same master element (not by where they happen to sit)
    if applied_conf:
        out["avg_confidence"] = round(sum(applied_conf) / len(applied_conf), 2)
    return out


def _already_there(p, box: dict, new_w: float, new_h: float) -> bool:
    """True when the placed object already sits (within IN_PLACE_TOL of the page) where the designers put it."""
    return (abs((p.x + p.w / 2) / new_w - box["cx"]) <= IN_PLACE_TOL and abs((p.y + p.h / 2) / new_h - box["cy"]) <= IN_PLACE_TOL
            and abs(p.w / new_w - box["w"]) <= IN_PLACE_TOL and abs(p.h / new_h - box["h"]) <= IN_PLACE_TOL)


def build_record(shop: dict, job: dict | None, base: dict, edited: dict, layout: dict | None, diff: dict | None = None) -> dict | None:
    """One correction record for a shop, or None when the designer changed nothing worth learning.

    `layout` is the report's `layout` block (which designer board the engine copied and how confident it was), when the report has one.
    """
    d = diff if diff is not None else diff_scenes(base, edited)
    if d["page_changed"] or not (d["changes"] or d["nested"]):
        return None
    pw, ph = _page(base)
    return {
        "shop_id": shop["id"], "brand": (job or {}).get("brand"), "master_file": (job or {}).get("master_filename"),
        "page_w_mm": round(pw, 3), "page_h_mm": round(ph, 3), "board_type": shop.get("board_type"),
        "template": (layout or {}).get("template") if isinstance(layout, dict) else None,
        "confidence": (layout or {}).get("confidence") if isinstance(layout, dict) else None,
        "changes": d["changes"], "nested": d["nested"], "text_edits": d["text_edits"],
    }


def summarize(row: dict, shop_name: str | None = None) -> dict:
    """A stored correction as the review screen shows it: where it came from, the page, and every change as page fractions plus the
    shift in millimetres (x right, y UP like the scene) so a person can judge it without opening the board."""
    rec = row["record"]
    pw, ph = float(row["page_w_mm"]), float(row["page_h_mm"])
    changes = []
    for c in rec.get("changes", []) + [{**n, "nested": True} for n in rec.get("nested", [])]:
        b, a = c.get("before"), c.get("after")
        shift = None
        if b and a:
            shift = {"dx_mm": round((a["cx"] - b["cx"]) * pw, 1), "dy_mm": round((a["cy"] - b["cy"]) * ph, 1),
                     "dw_mm": round((a["w"] - b["w"]) * pw, 1), "dh_mm": round((a["h"] - b["h"]) * ph, 1)}
        changes.append({"id": c["id"], "action": c["action"], "kind": "text" if c.get("text") else (c.get("signature") or {}).get("kind"),
                        "before": b, "after": a, "shift": shift, "nested": bool(c.get("nested")), "style": c.get("style"),
                        "lines": c.get("lines")})
    return {
        "id": row["shop_id"], "title": shop_name or rec.get("file") or row["shop_id"], "brand": row.get("brand"),
        "master_file": row.get("master_file"), "board_type": row.get("board_type"),
        "source": "designer-dataset" if rec.get("source") else "editor", "page_w_mm": pw, "page_h_mm": ph,
        "updated_at": row.get("updated_at"), "text_edits": rec.get("text_edits", 0),
        "trust": round(trust(row), 2), **feedback_of(row), "quarantined": quarantined(row),
        "applies": [c["id"] for c in changes if (c["action"] in LEARNED_ACTIONS and c["kind"] != "text") or c["nested"]
                    or c["action"] in SCENE_ACTIONS],
        "changes": changes,
    }


# ------------------------------------------------------------------ applying nested changes (as editor ops)

def _frac_to_mm(box: dict, pw: float, ph: float) -> dict:
    w, h = box["w"] * pw, box["h"] * ph
    return {"x": box["cx"] * pw - w / 2, "y": box["cy"] * ph - h / 2, "w": w, "h": h}


def _nested_target(node: dict, box: dict, pw: float, ph: float, is_text: bool) -> dict:
    """Where an object should end up. A text object keeps its own width-to-height shape (its content differs from the designer's): it is
    scaled uniformly to FIT INSIDE the designer's box and centred on the designer's centre - never larger than her box in either direction.
    (It used to take her HEIGHT only; her box can hold two lines where this board's name has one - a Hangyo name she had set on two lines
    came out 1.7 times too wide on the next board and ran off its white panel.) Anything else takes the designer's box."""
    t = _frac_to_mm(box, pw, ph)
    if not is_text or not node["h"] or not node["w"]:
        return t
    s = min(t["w"] / node["w"], t["h"] / node["h"])
    w, h = node["w"] * s, node["h"] * s
    return {"x": t["x"] + t["w"] / 2 - w / 2, "y": t["y"] + t["h"] / 2 - h / 2, "w": w, "h": h}


COMBINING_UNIT = 0.35      # a vowel sign / virama adds this much width next to its letter (Tamil letters are 1)
SPACE_UNIT = 0.4
INK_SAFETY = 1.0           # the width estimate is rough (no text shaping here, +-50 % on Tamil): it is only a guard against extremes
LINED_FILL_MAX = 1.2       # a re-broken name is kept to at most this share of the designer's box width; below it she gets HER line height


def _units(text: str) -> float:
    """A rough visual width of `text`: letters 1, spaces and combining marks (Tamil vowel signs, pulli) less. There is no shaping engine here,
    so counting code points would treat "ஸ்ரீ" (two letters + two marks) like four letters."""
    import unicodedata

    total = 0.0
    for ch in str(text):
        if ch.isspace():
            total += SPACE_UNIT
        elif unicodedata.category(ch) in ("Mn", "Mc", "Me"):
            total += COMBINING_UNIT
        else:
            total += 1.0
    return total


def balanced_lines(content: str, n: int) -> str | None:
    """`content` broken into `n` lines at its spaces, the longest line as short as it can be ("
" between lines). None when it has fewer
    than two words (nothing to break at) or `n` is below 2. With fewer words than `n`, one line per word."""
    words = str(content or "").split()
    n = min(int(n or 0), len(words))
    if n < 2:
        return None
    size = [_units(w) for w in words]
    total = len(words)

    def span(i: int, j: int) -> float:                       # visual width of words[i:j] joined by single spaces
        return sum(size[i:j]) + (j - i - 1) * SPACE_UNIT

    best: dict[tuple[int, int], tuple[float, list[int]]] = {}

    def solve(i: int, k: int) -> tuple[float, list[int]]:      # the best way to put words[i:] on k lines: (longest line, cut points)
        if k == 1:
            return span(i, total), []
        if (i, k) in best:
            return best[(i, k)]
        pick = None
        for j in range(i + 1, total - k + 2):
            rest, cuts = solve(j, k - 1)
            cand = (max(span(i, j), rest), [j] + cuts)
            if pick is None or cand[0] < pick[0]:
                pick = cand
        best[(i, k)] = pick
        return pick

    _, cuts = solve(0, n)
    edges = [0] + cuts + [total]
    return "\n".join(" ".join(words[a:b]) for a, b in zip(edges, edges[1:]))


def _line_plan(ch: dict, node: dict) -> dict | None:
    """The new content for a name block the designer set on a different number of lines than this board has, or None."""
    want = ch.get("lines")
    leaf = _single_text_leaf(node)
    if not want or leaf is None:
        return None
    old = str((leaf.get("text") or {}).get("content") or "")
    hers = ch.get("name_chars")
    if hers and name_chars(old) < hers * LINE_MIN_FRAC:
        return None                                           # a much shorter name than the one she broke stays on one line
    new = balanced_lines(" ".join(old.split()), want)
    if new is None or _line_count(new) == _line_count(old):
        return None
    return {"id": leaf["id"], "content": new, "old": old, "from_lines": _line_count(old)}


def _longest_line(text: str) -> float:
    return max((_units(x) for x in _LINE_BREAK.split(text)), default=0.0) or 1.0


def _lined_target(node: dict, box: dict, pw: float, ph: float, plan: dict) -> dict:
    """Where a name block goes when its text gets a different number of lines. The editor and the export size an edited text PER ORIGINAL
    LINE: one line is `box height / original lines` tall and the text grows around the box centre, so the designer's box height gives the
    line height she chose. The width follows the glyphs: it is estimated from this board's one-line width and the longest new line, and the
    line height is reduced when that would be wider than her box (a longer name must still stay inside the white panel)."""
    t = _frac_to_mm(box, pw, ph)
    w0, h0, l0 = node["w"], node["h"], plan["from_lines"]
    if not w0 or not h0:
        return t
    line = t["h"] / l0                                       # her height of one line
    ink = w0 * (line / (h0 / l0)) * (_longest_line(plan["content"]) / _longest_line(plan["old"])) * INK_SAFETY
    room = t["w"] * LINED_FILL_MAX
    if ink > room:
        line *= room / ink
        ink = room
    h = line * l0
    return {"x": t["x"] + t["w"] / 2 - ink / 2, "y": t["y"] + t["h"] / 2 - h / 2, "w": ink, "h": h}


def _with_weight(c: dict, row: dict) -> dict:
    """A change from a nearby-size record counts half in the averaged part (`blend_boxes` reads the `_w` mark on its box); every change also
    carries the record it came from (`_src`) and that record's trust (`_t`, on the change and on its box)."""
    c = {**c, "_src": row.get("shop_id"), "_t": trust(row)}
    if c.get("after"):
        extra = {"_t": c["_t"]}
        if row.get("nearby"):
            extra["_w"] = NEARBY_WEIGHT
        c = {**c, "after": {**c["after"], **extra}}
    return c


def _nested_requests(records: list[dict]) -> dict[str, list[dict]]:
    asked: dict[str, list[dict]] = {}
    for r in records:
        rec = r.get("record") or {}
        for c in rec.get("nested", []):
            asked.setdefault(c["id"], []).append(_with_weight({**c, "_editor": _is_editor(r)}, r))
        for c in rec.get("changes", []):
            if c["action"] in SCENE_ACTIONS:                 # a top-level object the designer hid / deleted
                asked.setdefault(c["id"], []).append({**c, "_editor": _is_editor(r), "_src": r.get("shop_id"), "_t": trust(r)})
    return asked


def _by_depth(asked: dict, nodes: dict) -> list[str]:
    """The asked-for ids that exist on the board, parents before children."""
    return sorted((i for i in asked if i in nodes), key=lambda i: _depth(nodes, i))


def _agreed_change(group: list[dict]) -> dict | None:
    """One change for an object from every record that names it (same boxes within CONSENSUS_TOL and the same style), None when they clash."""
    editors = [c for c in group if c.get("_editor")]
    if editors:
        newest = next((c for c in editors if c.get("_t", 1.0) >= TRUST_LEAD), editors[0])   # newest first; the newest trusted one leads
        if newest.get("lines"):
            hers = [c["name_chars"] for c in editors if c.get("lines") == newest["lines"] and c.get("name_chars")]
            if hers:
                newest = {**newest, "name_chars": min(hers)}  # the SHORTEST name she has broken that way: shorter ones stay on one line
        boxed = [c["after"] for c in editors if c.get("after")]
        if newest.get("after") and boxed:
            return {**newest, "after": blend_boxes(boxed)}    # 80 % newest + 20 % average of the editor corrections
        return newest
    first = group[0]
    if len(group) == 1:
        return first
    for c in group[1:]:
        if max(abs(c["after"][k] - first["after"][k]) for k in BOX_KEYS) > CONSENSUS_TOL or c.get("style") != first.get("style") \
                or c.get("lines") != first.get("lines"):
            return None
    return {**first, "after": {k: _median([c["after"][k] for c in group]) for k in BOX_KEYS}}


def _fits_node(ch: dict, node: dict) -> bool:
    """The recorded object is the same kind of thing as the board's node (kind, text or not, number of nested shapes)."""
    sig = ch.get("signature") or {}
    if sig.get("kind") != (node.get("kind") or node.get("type")):
        return False
    if bool(ch.get("text")) != _text_block(node):
        return False
    return not (node.get("kind") == "group" and sig.get("n_desc") != _count_nested(node))


def _fits_object(ch: dict, node: dict) -> bool:
    """A recorded hide / delete names the same kind of object (kind, and the number of nested shapes for a group)."""
    sig = ch.get("signature") or {}
    if sig.get("kind") != (node.get("kind") or node.get("type")):
        return False
    return not (node.get("kind") == "group" and sig.get("n_desc") != _count_nested(node))


def _scene_action_ops(ch: dict, nid: str) -> list[dict]:
    return [{"op": "visibility", "id": nid, "visible": False}] if ch["action"] == "hidden" else [{"op": "delete", "ids": [nid]}]


def _nested_edits(ch: dict, node: dict, nid: str, pw: float, ph: float) -> list[dict]:
    """The editor ops that bring one node to the recorded box and style (none when it is already there)."""
    made = []
    plan = _line_plan(ch, node)
    if plan:
        made.append({"op": "text", "id": plan["id"], "content": plan["content"]})     # first: the resize below then sizes the edited text
    if ch["action"] != "styled":
        target = _lined_target(node, ch["after"], pw, ph, plan) if plan else _nested_target(node, ch["after"], pw, ph, _text_block(node))
        cur = _box(node)
        if max(abs(target["x"] - cur["x"]), abs(target["y"] - cur["y"])) / max(pw, ph) >= MIN_SHIFT_FRAC \
                or max(abs(target["w"] - cur["w"]), abs(target["h"] - cur["h"])) / max(pw, ph) >= MIN_SHIFT_FRAC:
            op = {"op": "resize", "ids": [nid], "from": cur, "to": target}
            if _text_block(node):
                op["fit_w"] = round(_frac_to_mm(ch["after"], pw, ph)["w"] * FIT_FRAC, 3)   # the export keeps the real text inside her box
            made.append(op)
    if ch.get("style"):
        have = node.get("text") or {}
        style = {k: v for k, v in ch["style"].items() if have.get(k) != v}
        if style:
            made.append({"op": "text", "id": nid, **style})
    return made


def _replay(work: dict, ops: list[dict]) -> bool:
    """Apply the ops to the working copy; False when one of them cannot be applied."""
    from . import scene_ops

    try:
        for op in ops:
            scene_ops.apply_op(work, op)
    except scene_ops.OpError:
        return False
    return True


def _edits_for(ch: dict, node: dict, nid: str, pw: float, ph: float) -> list[dict] | None:
    """The editor ops for one agreed change, or None when the board's node is not the object that was recorded."""
    if ch["action"] in SCENE_ACTIONS:
        return _scene_action_ops(ch, nid) if _fits_object(ch, node) else None
    return _nested_edits(ch, node, nid, pw, ph) if _fits_node(ch, node) else None


def nested_ops(scene: dict, records: list[dict], sources_out: list | None = None) -> tuple[list[dict], dict]:
    """Editor ops that reproduce the stored records' nested changes on a freshly converted board's `scene`.

    Same consensus rule as the top-level ones: one record applies as is; several that name the same object must agree within
    CONSENSUS_TOL (then the median is used) or the object is left alone. An object is matched by id AND checked (kind, text or not,
    number of nested shapes); a board whose structure differs skips it. Parents are handled before children and each op is applied to a
    working copy first, so a child's op starts from where its parent's op left it. Returns (ops, {applied, skipped, conflicting}); the shop ids of the
    records the ops came from are appended to `sources_out` when given."""
    import copy

    pw, ph = _page(scene)
    work = copy.deepcopy(scene)
    nodes = _nodes_with_parents(work)
    asked = _nested_requests(records)
    ops: list[dict] = []
    summary = {"applied": 0, "skipped": sum(1 for i in asked if i not in nodes), "conflicting": 0}
    sources: list[str] = []
    for nid in _by_depth(asked, nodes):
        ch = _agreed_change(asked[nid])
        if ch is None:
            summary["conflicting"] += 1
            continue
        if nid not in nodes:
            continue                                          # inside something already deleted
        made = _edits_for(ch, nodes[nid][0], nid, pw, ph)
        if made is None or not _replay(work, made):
            summary["skipped"] += 1
            continue
        nodes = _nodes_with_parents(work)
        if made:
            ops.extend(made)
            summary["applied"] += 1
            sources.extend(s for s in (c.get("_src") for c in asked[nid]) if s and s not in sources)
    if sources_out is not None:
        sources_out.extend(sources)                           # the records these ops were learned from (judged on the next editor save)
    return ops, summary


def has_scene_changes(rows: list[dict]) -> bool:
    """True when any of the records has something that is replayed as editor ops (nested changes, or a hidden / deleted object)."""
    return any((r.get("record") or {}).get("nested") or any(c["action"] in SCENE_ACTIONS for c in (r.get("record") or {}).get("changes", []))
               for r in rows)


# ------------------------------------------------------------------ learning from a save of a board that was made WITH learned data

def learned_baseline(base: dict, learned_ops: list[dict]) -> dict:
    """The board as Corel Intelligence left it: `base` with the learned editor ops applied. A save is judged against THIS, not against the
    engine's raw board - otherwise the learned changes would be recorded again as if a designer had just made them (an echo that made
    every lesson reinforce itself and always count as the 'newest' correction)."""
    import copy

    from . import scene_ops

    if not learned_ops:
        return base
    work = copy.deepcopy(base)
    try:
        for op in learned_ops:
            scene_ops.apply_op(work, op)
    except scene_ops.OpError:
        return base
    return work


def _op_ids(op: dict) -> list[str]:
    ids = list(op.get("ids") or [])
    if op.get("id"):
        ids.append(op["id"])
    return ids


def nested_feedback(baseline: dict, edited: dict, learned_ops: list[dict]) -> tuple[int, int]:
    """(confirmed, disputed) objects among those the learned ops touched: kept as learned, or moved / restyled / hidden / shown again."""
    if not learned_ops:
        return 0, 0
    pw, ph = _page(baseline)
    ew, eh = _page(edited)
    if not (math.isclose(pw, ew, rel_tol=1e-6) and math.isclose(ph, eh, rel_tol=1e-6)):
        return 0, 0
    old, new = _all_nodes(baseline), _all_nodes(edited)
    confirmed = disputed = 0
    for nid in dict.fromkeys(i for op in learned_ops for i in _op_ids(op)):
        o, n = old.get(nid), new.get(nid)
        if o is None:
            continue                                         # the lesson deleted it: nothing left to compare
        if n is None or o.get("visible", True) != n.get("visible", True) or not _same(_box(o), _box(n), pw, ph) or _style_changes(o, n):
            disputed += 1
        else:
            confirmed += 1
    return confirmed, disputed


def _close(a: dict, b: dict) -> bool:
    return max(abs(a[k] - b[k]) for k in BOX_KEYS) <= MATCH_TOL


def _verdict_of(new: dict, source: dict) -> tuple[float, float]:
    """(kept, disputed) of one learned move that the designer moved AGAIN: put back near where the engine had it = the lesson was wrong (counts
    REVERT_WEIGHT); only nudged (under NUDGE_SHIFT) = partly right (half and half); moved elsewhere / hidden / deleted = one dispute."""
    after, before = new.get("after"), new.get("before")
    if after and source.get("before") and _close(after, source["before"]):
        return 0.0, REVERT_WEIGHT
    if after and before and max(abs(after[k] - before[k]) for k in BOX_KEYS) <= NUDGE_SHIFT:
        return 0.5, 0.5
    return 0.0, 1.0


def rebase_and_feedback(changes: list[dict], source_rows: list[dict], baseline: dict) -> tuple[list[dict], dict[str, list[float]]]:
    """Top-level corrections made on a board that already carried learned moves.

    `changes` are the new save's top-level changes, whose "before" is where the object sat on the LEARNED board. A record must describe the
    move from the ENGINE's own placement (that is what the next conversion will match against), so a change that starts exactly where a
    source record put the object gets that record's own "before" back. The same comparison is the verdict on the source: the designer moved
    the object again (disputed), or it is still where the record put it (confirmed). Returns (changes, {source shop id: [confirmed, disputed]})."""
    out = [dict(c) for c in changes]
    pw, ph = _page(baseline)
    boxes = [_frac_box(n, pw, ph) for n in _top_nodes(baseline).values()]
    verdicts: dict[str, list[int]] = {}
    rebased: set[int] = set()
    for row in source_rows:
        sid = row.get("shop_id")
        for s in (row.get("record") or {}).get("changes", []):
            if s["action"] not in APPLIED_ACTIONS or not s.get("after") or (s.get("signature") or {}).get("kind") == "text":
                continue
            hit = next((i for i, c in enumerate(out) if i not in rebased and c.get("before") and _close(c["before"], s["after"])), None)
            tally = verdicts.setdefault(sid, [0, 0])
            if hit is not None:
                kept, wrong = _verdict_of(out[hit], s)
                tally[0] += kept
                tally[1] += wrong
                rebased.add(hit)
                out[hit]["before"] = s["before"]
                if s.get("master_id") is not None:
                    out[hit]["master_id"] = s["master_id"]
            elif any(_close(b, s["after"]) for b in boxes):
                tally[0] += 1                                # nothing moved it again and it is where the record put it
    return out, {k: v for k, v in verdicts.items() if v != [0, 0]}


def learn_from_save(shop: dict, job: dict | None, base: dict, edited: dict, layout: dict | None,
                    source_rows: list[dict], objects: list[dict] | None = None) -> tuple[dict | None, dict[str, list[int]]]:
    """Everything one editor save teaches. Returns (the record to store or None, {record shop id: [confirmed, disputed] on THIS board}).

    On a board made with Corel Intelligence the save is judged against the board as it was left (`learned_baseline`), so only what the
    designer did ON TOP is recorded, top-level corrections are re-based onto the engine's own placement, and every record that fed the
    board is told whether its lesson was kept. `source_rows` = stored records (db.list_corrections())."""
    intel = (layout or {}).get("intelligence") if isinstance(layout, dict) else None
    if not isinstance(intel, dict) or not (intel.get("learned_ops") or intel.get("records")):
        rec = build_record(shop, job, base, edited, layout)
        if rec:
            attach_identity(rec["changes"], objects, *_page(base))
        return rec, {}
    ops = intel.get("learned_ops") or []
    baseline = learned_baseline(base, ops)
    diff = diff_scenes(baseline, edited)
    if diff["page_changed"]:
        return None, {}
    own = shop["id"]
    used = {s for s in (intel.get("records") or []) if s != own}
    rows = [r for r in source_rows if r.get("shop_id") in used]
    diff["changes"], top = rebase_and_feedback(diff["changes"], rows, baseline)
    verdicts: dict[str, list[int]] = {sid: list(v) for sid, v in top.items()}
    nested_sources = [s for s in (intel.get("nested_sources") or []) if s != own]
    if nested_sources:
        c, d = nested_feedback(baseline, edited, ops)
        for sid in nested_sources:
            v = verdicts.setdefault(sid, [0, 0])
            v[0] += c
            v[1] += d
    rec = build_record(shop, job, baseline, edited, layout, diff=diff)
    if rec:
        attach_identity(rec["changes"], objects, *_page(base))      # changes rebased onto a source record already carry its master_id
    return rec, {k: v for k, v in verdicts.items() if v != [0, 0]}


# ---------------------------------------------------------------- measuring: how much did the designer still have to fix?
#
# Every editor save is scored, even one that changed nothing: the COST is how far the designer still moved / resized objects, in % of the page
# (hiding or deleting an object counts HIDE_COST, a text style or line-count edit STYLE_COST). On a board made with Corel Intelligence the cost
# is measured against the board as the learning left it, so it is the work learning did NOT save. Comparing the average cost of boards made
# with and without Corel Intelligence is the accuracy number (`summarize_stats`); `tools/intelligence_loo.py` measures the same offline.

HIDE_COST = 5.0
STYLE_COST = 1.0


def _change_cost(c: dict) -> float:
    if c.get("action") in SCENE_ACTIONS:
        return HIDE_COST
    b, a = c.get("before"), c.get("after")
    if b and a:
        return 100.0 * max(abs(a[k] - b[k]) for k in BOX_KEYS)
    if c.get("style") or c.get("lines"):
        return STYLE_COST
    return 0.0


def edit_cost(diff: dict) -> dict:
    """{"cost": % of the page the designer moved things in total, "objects": how many objects they changed} for one `diff_scenes` result."""
    items = list(diff.get("changes") or []) + list(diff.get("nested") or [])
    return {"cost": round(sum(_change_cost(c) for c in items), 3), "objects": len(items)}


def save_cost(shop: dict, base: dict, edited: dict, layout: dict | None) -> dict | None:
    """The score of one editor save: {"cost", "objects", "used_intelligence", "learned"}; None when the page size was edited (boxes are then
    meaningless). `layout` is the report's `layout` block - its `intelligence` entry says whether (and how much) learning fed the board."""
    intel = (layout or {}).get("intelligence") if isinstance(layout, dict) else None
    used = isinstance(intel, dict) and bool(intel.get("learned_ops") or intel.get("records"))
    start = learned_baseline(base, intel.get("learned_ops") or []) if used else base
    d = diff_scenes(start, edited)
    if d["page_changed"]:
        return None
    learned = ((intel.get("applied") or 0) + ((intel.get("nested") or {}).get("applied") or 0)) if used else 0
    return {**edit_cost(d), "used_intelligence": used, "learned": int(learned)}


def _mean(v: list[float]) -> float | None:
    return round(sum(v) / len(v), 2) if v else None


def _aggregate(rows: list[dict]) -> dict:
    with_ = [r["edit_cost"] for r in rows if r.get("used_intelligence")]
    without = [r["edit_cost"] for r in rows if not r.get("used_intelligence")]
    raw_a = sum(with_) / len(with_) if with_ else None
    raw_b = sum(without) / len(without) if without else None
    return {"with": {"boards": len(with_), "avg_cost": _mean(with_), "untouched": sum(1 for c in with_ if c == 0)},
            "without": {"boards": len(without), "avg_cost": _mean(without), "untouched": sum(1 for c in without if c == 0)},
            "improvement_pct": round((1 - raw_a / raw_b) * 100, 1) if raw_a is not None and raw_b else None}


def summarize_stats(stats: list[dict], records: list[dict]) -> dict:
    """The accuracy panel: average cost of saved boards made WITH vs WITHOUT Corel Intelligence (overall and per brand) and how the stored
    lessons fared (kept = the designer left it, corrected = moved again). `stats` = db.list_correction_stats(), `records` = db.list_corrections()."""
    brands: dict[str, list[dict]] = {}
    for r in stats:
        brands.setdefault(r.get("brand") or "", []).append(r)
    kept = corrected = 0
    for rec in records:
        fb = feedback_of(rec)
        kept += fb["confirmed"]
        corrected += fb["disputed"]
    kept, corrected = round(kept, 1), round(corrected, 1)
    return {**_aggregate(stats), "brands": {b: _aggregate(rows) for b, rows in sorted(brands.items())},
            "lessons": {"records": len(records), "kept": kept, "corrected": corrected, "paused": sum(1 for r in records if quarantined(r))}}
