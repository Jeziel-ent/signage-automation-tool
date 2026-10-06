"""Correction memory, step 1 (capture): what the designer changed in the editor, as page-fraction boxes.

The engine places art by copying the nearest designer board (`example_layout`). When a designer then fixes the result in the editor,
the fix is knowledge the engine should reuse for the next board of the same size. This module only CAPTURES it: it compares the
scene the engine produced (`base`) with the same scene after the saved edit list (`edited`) and records, for every top-level object
that was moved, resized, hidden or deleted, where it was and where the designer put it - as fractions of the page (origin bottom-left,
like the scene), so the record is independent of the page size and can later be matched against `library.json` boards.

Text edits are not learned (Tamil spelling and names come from the sheet) - they are only counted. Nothing here changes how a board is
generated; a correction is `pending` until a later step approves it.
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


def diff_scenes(base: dict, edited: dict) -> dict:
    """The designer's corrections between the engine's scene and the edited one.

    Returns {"page_changed": bool, "changes": [...], "text_edits": int}. A change is
    {"id", "signature", "before", "after", "action": "moved" | "resized" | "moved+resized" | "hidden" | "deleted"}.
    A page-size edit makes every box meaningless against the old page, so then no changes are reported.
    """
    bw, bh = _page(base)
    ew, eh = _page(edited)
    if not (math.isclose(bw, ew, rel_tol=1e-6) and math.isclose(bh, eh, rel_tol=1e-6)):
        return {"page_changed": True, "changes": [], "text_edits": 0}
    old, new = _top_nodes(base), _all_nodes(edited)
    changes, text_edits = [], 0
    for nid, o in old.items():
        n = new.get(nid)
        before = _frac_box(o, bw, bh)
        if n is None:
            changes.append({"id": nid, "signature": _signature(o), "before": before, "after": None, "action": "deleted"})
            continue
        if n.get("text") != o.get("text"):
            text_edits += 1
        if o.get("visible", True) and not n.get("visible", True):
            changes.append({"id": nid, "signature": _signature(o), "before": before, "after": None, "action": "hidden"})
            continue
        after = _frac_box(n, bw, bh)
        moved = max(abs(before["cx"] - after["cx"]), abs(before["cy"] - after["cy"])) >= MIN_SHIFT_FRAC
        resized = max(abs(before["w"] - after["w"]), abs(before["h"] - after["h"])) >= MIN_SHIFT_FRAC
        if moved or resized:
            action = "moved+resized" if moved and resized else "moved" if moved else "resized"
            changes.append({"id": nid, "signature": _signature(o), "before": before, "after": after, "action": action})
    return {"page_changed": False, "changes": changes, "text_edits": text_edits}


SAME_SIZE_TOL = 0.005      # a correction applies to a board whose page is within 0.5 % of the one it was made on
MATCH_TOL = 0.012          # a recorded "before" box matches a placed object within 1.2 % of the page (centre and size)
CONSENSUS_TOL = 0.02       # several designers agree on an object when their boxes differ by less than this share of the page
TEXT_ROLES = ("text", "shopname")
APPLIED_ACTIONS = ("moved", "resized", "moved+resized")


def usable_records(rows: list[dict], brand: str | None, master_file: str | None, w_mm: float, h_mm: float,
                   board_type: str | None) -> list[dict]:
    """The stored corrections that describe THIS board: same brand, same master file, same page size (within `SAME_SIZE_TOL`) and, when
    both name one, the same TYPE OF BOARD. Rejected records are never used. Newest first, so a later correction wins a clash."""
    def same(a, b):
        return math.isclose(float(a), float(b), rel_tol=SAME_SIZE_TOL)

    want = (board_type or "").strip().lower()
    out = []
    for r in rows:
        if r.get("status") == "rejected" or (r.get("brand") or "") != (brand or ""):
            continue
        if (r.get("master_file") or "").strip().lower() != (master_file or "").strip().lower():
            continue
        if not (same(r["page_w_mm"], w_mm) and same(r["page_h_mm"], h_mm)):
            continue
        have = (r.get("board_type") or "").strip().lower()
        if want and have and want != have:
            continue
        out.append(r)
    return sorted(out, key=lambda r: r.get("updated_at") or 0, reverse=True)


def _median(v: list[float]) -> float:
    v = sorted(v)
    return v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2


def apply_to_placed(placed: list, new_w: float, new_h: float, records: list[dict]) -> dict:
    """Move/resize the engine's placed objects to where designers put them on a board of this size.

    Each recorded move/resize is matched to the placed object whose box is nearest the recorded "before" box (the engine produced that
    box for the same master and size, so it is the same object). One designer's correction is applied as it is. When several records
    corrected the same object their "after" boxes must agree (within `CONSENSUS_TOL` of the page on every value): then the median is
    used; when they disagree the designers do not share one answer, so the object is left as the engine placed it ("conflicting").
    Text objects are never touched, and hidden / deleted objects are not re-applied (counted as skipped).
    Returns {"applied", "skipped", "conflicting", "records": [shop ids that contributed]}.
    """
    votes: dict[str, list[tuple[dict, str]]] = {}
    skipped = 0
    for rec in records:
        sid = rec.get("shop_id") or (rec.get("record") or {}).get("shop_id")
        taken: set[str] = set()                                   # within one record an object answers one change only
        for ch in (rec.get("record") or rec).get("changes", []):
            if ch["action"] not in APPLIED_ACTIONS or (ch.get("signature") or {}).get("kind") == "text":
                skipped += 1
                continue
            b, best = ch["before"], None
            for p in placed:
                if p.id in taken or p.role in TEXT_ROLES or p.w <= 0 or p.h <= 0:
                    continue
                d = max(abs((p.x + p.w / 2) / new_w - b["cx"]), abs((p.y + p.h / 2) / new_h - b["cy"]),
                        abs(p.w / new_w - b["w"]), abs(p.h / new_h - b["h"]))
                if d <= MATCH_TOL and (best is None or d < best[0]):
                    best = (d, p)
            if best is None:
                skipped += 1
                continue
            taken.add(best[1].id)
            votes.setdefault(best[1].id, []).append((ch["after"], sid))
    by_id = {p.id: p for p in placed}
    applied = conflicting = 0
    used: list[str] = []
    for pid, vs in votes.items():
        keys = ("cx", "cy", "w", "h")
        if len(vs) > 1 and any(max(a[k] for a, _ in vs) - min(a[k] for a, _ in vs) > CONSENSUS_TOL for k in keys):
            conflicting += 1
            continue
        a = {k: _median([x[k] for x, _ in vs]) for k in keys}
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
    if d["page_changed"] or not d["changes"]:
        return None
    pw, ph = _page(base)
    return {
        "shop_id": shop["id"], "brand": (job or {}).get("brand"), "master_file": (job or {}).get("master_filename"),
        "page_w_mm": round(pw, 3), "page_h_mm": round(ph, 3), "board_type": shop.get("board_type"),
        "template": (layout or {}).get("template") if isinstance(layout, dict) else None,
        "confidence": (layout or {}).get("confidence") if isinstance(layout, dict) else None,
        "changes": d["changes"], "text_edits": d["text_edits"],
    }
