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


def usable_records(rows: list[dict], brand: str | None, master_file: str | None, w_mm: float, h_mm: float,
                   board_type: str | None) -> list[dict]:
    """The stored corrections that describe THIS board: same brand, same master file, same page size (within `SAME_SIZE_TOL`) and, when
    both name one, the same TYPE OF BOARD. Every stored record is used.
    Newest first, so a later correction wins a clash."""
    want = (board_type or "").strip().lower()
    out = [r for r in rows if _record_fits(r, brand, master_file, w_mm, h_mm, want)]
    out = sorted(out, key=lambda r: r.get("updated_at") or 0, reverse=True)
    near = [{**r, "nearby": True} for r in rows if _record_fits(r, brand, master_file, w_mm, h_mm, want, nearby=True)]
    near.sort(key=lambda r: r.get("updated_at") or 0, reverse=True)
    return out + near                                       # exact-size records lead; nearby ones only steady them (or stand in when there is none)


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
    taken: set[str] = set()                                   # within one record an object answers one change only
    skipped = 0
    for ch in (rec.get("record") or rec).get("changes", []):
        if ch["action"] in SCENE_ACTIONS:
            continue                                          # replayed afterwards as editor ops (nested_ops)
        best = None
        if ch["action"] in APPLIED_ACTIONS and (ch.get("signature") or {}).get("kind") != "text":
            best = _nearest_placed(placed, taken, ch["before"], new_w, new_h)
        if best is None:
            skipped += 1
            continue
        taken.add(best[1].id)
        after = {**ch["after"], "_w": NEARBY_WEIGHT} if rec.get("nearby") else ch["after"]
        votes.setdefault(best[1].id, []).append((after, sid, _is_editor(rec)))
    return skipped


def blend_boxes(boxes: list[dict]) -> dict:
    """The learned box from the editor corrections that name an object, NEWEST FIRST: 80 % the newest + 20 % the average of all of them."""
    if len(boxes) == 1:
        return {k: boxes[0][k] for k in BOX_KEYS}                # one correction: exactly as the designer made it
    if len(boxes) >= 3:
        mid = {k: _median([b[k] for b in boxes]) for k in BOX_KEYS}      # the median: one mistaken edit cannot drag the steady part
    else:
        w = [b.get("_w", 1.0) for b in boxes]                    # two records: the (weighted) average - a nearby-size record counts half
        mid = {k: sum(b[k] * x for b, x in zip(boxes, w)) / sum(w) for k in BOX_KEYS}
    return {k: round(RECENT_WEIGHT * boxes[0][k] + AVERAGE_WEIGHT * mid[k], 6) for k in BOX_KEYS}


def _decide_box(votes: list[tuple[dict, str, bool]]) -> tuple[dict | None, list[str]]:
    """(the box an object takes, the shop ids behind it). Corrections saved from the editor beat the rest and are blended 8:2 - 80 % the NEWEST
    (`records` come newest first) and 20 % the average of all of them (`blend_boxes`) - so a fresh fix leads and older ones steady it instead of
    clashing. Without one (corrections learned from designer files) the boxes must agree - see `_agreed_box`."""
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


def apply_to_placed(placed: list, new_w: float, new_h: float, records: list[dict]) -> dict:
    """Move/resize the engine's placed objects to where designers put them on a board of this size.

    Each recorded move/resize is matched to the placed object whose box is nearest the recorded "before" box (the engine produced that
    box for the same master and size, so it is the same object). One designer's correction is applied as it is. When several records
    corrected the same object their "after" boxes must agree (within `CONSENSUS_TOL` of the page on every value): then the median is
    used; when they disagree the designers do not share one answer, so the object is left as the engine placed it ("conflicting").
    Text objects are never touched, and hidden / deleted objects are not re-applied (counted as skipped).
    Returns {"applied", "skipped", "conflicting", "records": [shop ids that contributed]}.
    """
    votes, skipped = _collect_votes(placed, new_w, new_h, records)
    by_id = {p.id: p for p in placed}
    applied = conflicting = in_place = 0
    used: list[str] = []
    for pid, vs in votes.items():
        a, sids = _decide_box(vs)
        if a is None:
            conflicting += 1
            continue
        p = by_id[pid]
        if _already_there(p, a, new_w, new_h):
            in_place += 1
            used.extend(sid for sid in sids if sid not in used)
            continue
        p.w, p.h = a["w"] * new_w, a["h"] * new_h
        p.x, p.y = a["cx"] * new_w - p.w / 2, a["cy"] * new_h - p.h / 2
        p.warnings.append("moved to where a designer corrected this board size (Corel Intelligence)")
        applied += 1
        used.extend(sid for sid in sids if sid not in used)
    out = {"applied": applied, "skipped": skipped, "conflicting": conflicting, "records": used}
    if in_place:
        out["in_place"] = in_place          # learned boxes the engine already produced: nothing to move
    return out


def _already_there(p, box: dict, new_w: float, new_h: float) -> bool:
    """True when the placed object already sits (within IN_PLACE_TOL of the page) where the designers put it."""
    return (abs((p.x + p.w / 2) / new_w - box["cx"]) <= IN_PLACE_TOL and abs((p.y + p.h / 2) / new_h - box["cy"]) <= IN_PLACE_TOL
            and abs(p.w / new_w - box["w"]) <= IN_PLACE_TOL and abs(p.h / new_h - box["h"]) <= IN_PLACE_TOL)


def build_record(shop: dict, job: dict | None, base: dict, edited: dict, layout: dict | None) -> dict | None:
    """One correction record for a shop, or None when the designer changed nothing worth learning.

    `layout` is the report's `layout` block (which designer board the engine copied and how confident it was), when the report has one.
    """
    d = diff_scenes(base, edited)
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
    """A change from a nearby-size record counts half in the averaged part (`blend_boxes` reads the `_w` mark on its box)."""
    if row.get("nearby") and c.get("after"):
        c = {**c, "after": {**c["after"], "_w": NEARBY_WEIGHT}}
    return c


def _nested_requests(records: list[dict]) -> dict[str, list[dict]]:
    asked: dict[str, list[dict]] = {}
    for r in records:
        rec = r.get("record") or {}
        for c in rec.get("nested", []):
            asked.setdefault(c["id"], []).append(_with_weight({**c, "_editor": _is_editor(r)}, r))
        for c in rec.get("changes", []):
            if c["action"] in SCENE_ACTIONS:                 # a top-level object the designer hid / deleted
                asked.setdefault(c["id"], []).append({**c, "_editor": _is_editor(r)})
    return asked


def _by_depth(asked: dict, nodes: dict) -> list[str]:
    """The asked-for ids that exist on the board, parents before children."""
    return sorted((i for i in asked if i in nodes), key=lambda i: _depth(nodes, i))


def _agreed_change(group: list[dict]) -> dict | None:
    """One change for an object from every record that names it (same boxes within CONSENSUS_TOL and the same style), None when they clash."""
    editors = [c for c in group if c.get("_editor")]
    if editors:
        newest = editors[0]                                   # records come newest first; its style / lines / action lead
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


def nested_ops(scene: dict, records: list[dict]) -> tuple[list[dict], dict]:
    """Editor ops that reproduce the stored records' nested changes on a freshly converted board's `scene`.

    Same consensus rule as the top-level ones: one record applies as is; several that name the same object must agree within
    CONSENSUS_TOL (then the median is used) or the object is left alone. An object is matched by id AND checked (kind, text or not,
    number of nested shapes); a board whose structure differs skips it. Parents are handled before children and each op is applied to a
    working copy first, so a child's op starts from where its parent's op left it. Returns (ops, {applied, skipped, conflicting})."""
    import copy

    pw, ph = _page(scene)
    work = copy.deepcopy(scene)
    nodes = _nodes_with_parents(work)
    asked = _nested_requests(records)
    ops: list[dict] = []
    summary = {"applied": 0, "skipped": sum(1 for i in asked if i not in nodes), "conflicting": 0}
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
    return ops, summary


def has_scene_changes(rows: list[dict]) -> bool:
    """True when any of the records has something that is replayed as editor ops (nested changes, or a hidden / deleted object)."""
    return any((r.get("record") or {}).get("nested") or any(c["action"] in SCENE_ACTIONS for c in (r.get("record") or {}).get("changes", []))
               for r in rows)
