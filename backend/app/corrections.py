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


def _top_change(nid: str, o: dict, n: dict | None, bw: float, bh: float) -> dict | None:
    """What the designer did to ONE top-level object (`n` is its edited twin, None when it is gone), or None when nothing worth learning."""
    before = _frac_box(o, bw, bh)

    def entry(after, action):
        return {"id": nid, "signature": _signature(o), "before": before, "after": after, "action": action}

    if n is None:
        return entry(None, "deleted")
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
    changes, text_edits = [], 0
    for nid, o in old.items():
        n = new.get(nid)
        if n is not None and n.get("text") != o.get("text"):
            text_edits += 1
        ch = _top_change(nid, o, n, bw, bh)
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


def _nested_entry(nid: str, parent: str, old: dict, new: dict, pw: float, ph: float) -> dict | None:
    """The recorded change for one nested object (box beyond its parent's own move / resize, and / or text style), or None."""
    o, n = old[nid][0], new[nid][0]
    shifted, resized = _nested_motion(o, n, old[parent][0], new[parent][0], pw, ph)
    style = _style_changes(o, n) if o.get("text") and n.get("text") else {}
    if not (shifted or resized or style):
        return None
    action = _action(shifted, resized) if shifted or resized else "styled"
    entry = {"id": nid, "parent": parent, "path": _path(old, nid), "signature": _signature(o), "action": action,
             "before": _frac_box(o, pw, ph), "after": _frac_box(n, pw, ph)}
    if _text_block(o):
        entry["text"] = True
    if style:
        entry["style"] = style
    return entry


def diff_nested(base: dict, edited: dict) -> tuple[list, int]:
    """The designer's changes to objects INSIDE groups / PowerClips (the shop-name block and its text usually sit in the master's big group):
    for every nested object still in the same parent, its box compared with where its parent's own move / resize alone would leave it, plus
    text style changes. Boxes are page fractions like the top-level ones. Returns (changes, number of text contents edited)."""
    pw, ph = _page(base)
    old, new = _nodes_with_parents(base), _nodes_with_parents(edited)
    out, text_edits = [], 0
    for nid, (o, parent) in old.items():
        if parent is None or nid not in new or new[nid][1] != parent:
            continue                                         # top-level (handled elsewhere) or moved to another parent: not learned
        n = new[nid][0]
        text_edits += _content_of(n) != _content_of(o)
        entry = _nested_entry(nid, parent, old, new, pw, ph)
        if entry:
            out.append(entry)
    return out, text_edits


SAME_SIZE_TOL = 0.005      # a correction applies to a board whose page is within 0.5 % of the one it was made on
MATCH_TOL = 0.012          # a recorded "before" box matches a placed object within 1.2 % of the page (centre and size)
CONSENSUS_TOL = 0.02       # several designers agree on an object when their boxes differ by less than this share of the page
TEXT_ROLES = ("text", "shopname")
APPLIED_ACTIONS = ("moved", "resized", "moved+resized")


def usable_records(rows: list[dict], brand: str | None, master_file: str | None, w_mm: float, h_mm: float,
                   board_type: str | None) -> list[dict]:
    """The stored corrections that describe THIS board: same brand, same master file, same page size (within `SAME_SIZE_TOL`) and, when
    both name one, the same TYPE OF BOARD. Every stored record is used.
    Newest first, so a later correction wins a clash."""
    want = (board_type or "").strip().lower()
    out = [r for r in rows if _record_fits(r, brand, master_file, w_mm, h_mm, want)]
    return sorted(out, key=lambda r: r.get("updated_at") or 0, reverse=True)


def _same_size(a, b) -> bool:
    return math.isclose(float(a), float(b), rel_tol=SAME_SIZE_TOL)


def _lower(v) -> str:
    return (v or "").strip().lower()


def _record_fits(r: dict, brand: str | None, master_file: str | None, w_mm: float, h_mm: float, want_type: str) -> bool:
    """One stored record describes this board: same brand, same master file, same page size and (when both name one) board type."""
    if (r.get("brand") or "") != (brand or ""):
        return False
    if _lower(r.get("master_file")) != _lower(master_file):
        return False
    if not (_same_size(r["page_w_mm"], w_mm) and _same_size(r["page_h_mm"], h_mm)):
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


def _collect_votes(placed: list, new_w: float, new_h: float, records: list[dict]) -> tuple[dict, int]:
    """({placed id: [(recorded "after" box, shop id)]}, number of recorded changes that matched nothing or are not re-applied)."""
    votes: dict[str, list[tuple[dict, str]]] = {}
    skipped = 0
    for rec in records:
        sid = rec.get("shop_id") or (rec.get("record") or {}).get("shop_id")
        taken: set[str] = set()                                   # within one record an object answers one change only
        for ch in (rec.get("record") or rec).get("changes", []):
            if ch["action"] not in APPLIED_ACTIONS or (ch.get("signature") or {}).get("kind") == "text":
                skipped += 1
                continue
            best = _nearest_placed(placed, taken, ch["before"], new_w, new_h)
            if best is None:
                skipped += 1
                continue
            taken.add(best[1].id)
            votes.setdefault(best[1].id, []).append((ch["after"], sid))
    return votes, skipped


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
    applied = conflicting = 0
    used: list[str] = []
    for pid, vs in votes.items():
        a = _agreed_box([x for x, _ in vs])
        if a is None:
            conflicting += 1
            continue
        p = by_id[pid]
        p.w, p.h = a["w"] * new_w, a["h"] * new_h
        p.x, p.y = a["cx"] * new_w - p.w / 2, a["cy"] * new_h - p.h / 2
        p.warnings.append("moved to where a designer corrected this board size (Corel Intelligence)")
        applied += 1
        used.extend(sid for _, sid in vs if sid not in used)
    return {"applied": applied, "skipped": skipped, "conflicting": conflicting, "records": used}


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
                        "before": b, "after": a, "shift": shift, "nested": bool(c.get("nested")), "style": c.get("style")})
    return {
        "id": row["shop_id"], "title": shop_name or rec.get("file") or row["shop_id"], "brand": row.get("brand"),
        "master_file": row.get("master_file"), "board_type": row.get("board_type"),
        "source": "designer-dataset" if rec.get("source") else "editor", "page_w_mm": pw, "page_h_mm": ph,
        "updated_at": row.get("updated_at"), "text_edits": rec.get("text_edits", 0),
        "applies": [c["id"] for c in changes if (c["action"] in APPLIED_ACTIONS and c["kind"] != "text") or c["nested"]],
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


def _nested_requests(records: list[dict]) -> dict[str, list[dict]]:
    asked: dict[str, list[dict]] = {}
    for r in records:
        for c in (r.get("record") or {}).get("nested", []):
            asked.setdefault(c["id"], []).append(c)
    return asked


def _by_depth(asked: dict, nodes: dict) -> list[str]:
    """The asked-for ids that exist on the board, parents before children."""
    return sorted((i for i in asked if i in nodes), key=lambda i: _depth(nodes, i))


def _agreed_change(group: list[dict]) -> dict | None:
    """One change for an object from every record that names it (same boxes within CONSENSUS_TOL and the same style), None when they clash."""
    first = group[0]
    if len(group) == 1:
        return first
    for c in group[1:]:
        if max(abs(c["after"][k] - first["after"][k]) for k in BOX_KEYS) > CONSENSUS_TOL or c.get("style") != first.get("style"):
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


def _nested_edits(ch: dict, node: dict, nid: str, pw: float, ph: float) -> list[dict]:
    """The editor ops that bring one node to the recorded box and style (none when it is already there)."""
    made = []
    if ch["action"] != "styled":
        target = _nested_target(node, ch["after"], pw, ph, _text_block(node))
        cur = _box(node)
        if max(abs(target["x"] - cur["x"]), abs(target["y"] - cur["y"])) / max(pw, ph) >= MIN_SHIFT_FRAC \
                or max(abs(target["w"] - cur["w"]), abs(target["h"] - cur["h"])) / max(pw, ph) >= MIN_SHIFT_FRAC:
            made.append({"op": "resize", "ids": [nid], "from": cur, "to": target})
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
        node = nodes[nid][0]
        if not _fits_node(ch, node):
            summary["skipped"] += 1
            continue
        made = _nested_edits(ch, node, nid, pw, ph)
        if not _replay(work, made):
            summary["skipped"] += 1
            continue
        nodes = _nodes_with_parents(work)
        if made:
            ops.extend(made)
            summary["applied"] += 1
    return ops, summary
