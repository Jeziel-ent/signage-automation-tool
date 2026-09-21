"""Editor scene model + replayable edit operations (pure Python, no CorelDRAW).

The editor never mutates a scene directly: every edit is an entry in an
operation list, and the current scene is always `apply_ops(base_scene, ops)`.
The same list is what Phase D replays through COM, so the semantics here are
the contract. `frontend/src/editor/ops.js` mirrors this file; both are run
against the shared golden cases in tests/fixtures/ops_golden.json so they
cannot drift apart silently.

Scene shape (all lengths in mm, origin bottom-left like CorelDRAW):
    {"page": {"width", "height"},
     "layers": [{"id", "name", "visible", "locked", "children": [node, ...]}],
     ...extra keys (unit, page_image, ...) are carried along untouched}
    node = {"id", "kind": "shape"|"group"|"powerclip", "type", "name",
            "x", "y", "w", "h", "rotation", "visible", "locked",
            "image": {"file", "format"} | None,      # leaf shapes only
            "text": {"content", "font", "size_pt"},  # text shapes only
            "children": [node, ...]}                 # group / powerclip
`children` lists are bottom -> top (draw order). A PowerClip's contents are
listed for the layers tree, selectable, and can be edited in place: their TEXT
(`text` op) and their position/size (`move`/`resize`) - see
`_check_editable`'s `allow_powerclip`. Order/reorder/group/ungroup/delete
still cannot target a PowerClip child (those change what is inside the clip,
not how it looks). Child coordinates are absolute page coordinates like every
other node, so a nested move is the same translation as a top-level one - the
clip frame itself never changes. Moving/resizing a child marks its PowerClip
`stale` (its rendered image no longer matches) until CorelDRAW re-renders it.
No real board in this project's dataset contains a PowerClip, so none of this
has been verified against real CorelDRAW.

Operations (`op` key; every op is JSON and self-contained):
    move        {ids, dx, dy}
    resize      {ids, from:{x,y,w,h}, to:{x,y,w,h}}   descendants scale with it
    order       {id, mode: front|back|forward|backward}
    reorder     {id, parent, index}                    parent = layer or group id
    visibility  {id, visible}                          node or layer id
    group       {ids, group_id, name?}                 ids must share one parent
    ungroup     {id}
    text        {id, content?, font?, size_pt?}
    delete      {ids}
    paste       {parent, nodes: [subtree, ...]}        also used for duplicate
    page        {width, height}                        page size only, objects stay
    layer_order {id, index}                            move a layer (index in the bottom -> top list)
"""
from __future__ import annotations

import copy
from typing import Any

MIN_SIZE = 0.001
ROUND = 4


class OpError(ValueError):
    """An operation that cannot be applied to the scene it was given."""


def _r(v: float) -> float:
    return round(float(v), ROUND)


# ----------------------------------------------------------------- indexing

def _index(scene: dict) -> dict[str, dict]:
    """id -> {"node", "list", "parent", "layer"} for every layer and node."""
    idx: dict[str, dict] = {}

    def walk(children, parent, layer):
        for n in children:
            idx[n["id"]] = {"node": n, "list": children, "parent": parent, "layer": layer}
            if n.get("children"):
                walk(n["children"], n, layer)

    for layer in scene["layers"]:
        idx[layer["id"]] = {"node": layer, "list": scene["layers"], "parent": None, "layer": layer, "is_layer": True}
        walk(layer["children"], None, layer)
    return idx


def find_node(scene: dict, node_id: str) -> dict | None:
    e = _index(scene).get(node_id)
    return e["node"] if e else None


def iter_nodes(scene: dict):
    """Depth-first, bottom -> top, every node (not layers)."""
    def walk(children):
        for n in children:
            yield n
            if n.get("children"):
                yield from walk(n["children"])

    for layer in scene["layers"]:
        yield from walk(layer["children"])


def _need(idx: dict, node_id: str) -> dict:
    e = idx.get(node_id)
    if e is None:
        raise OpError(f"unknown id {node_id!r}")
    return e


def _top_ids(idx: dict, ids: list[str]) -> list[str]:
    """Validate ids and drop any whose ancestor is also listed (they move/scale with it)."""
    if not ids:
        raise OpError("no ids given")
    seen = set(ids)
    out = []
    for i in dict.fromkeys(ids):
        e = _need(idx, i)
        if e.get("is_layer"):
            raise OpError(f"{i!r} is a layer, not an object")
        p = e["parent"]
        nested = False
        while p is not None:
            if p["id"] in seen:
                nested = True
                break
            p = idx[p["id"]]["parent"]
        if not nested:
            out.append(i)
    return out


def _check_editable(idx: dict, node_id: str, allow_powerclip: bool = False) -> None:
    """`allow_powerclip=True` is the narrow exception - only `text`, `move` and
    `resize` pass it, so a PowerClip child can be edited in place without
    opening up order/reorder/group/ungroup/delete on it (see this module's
    docstring)."""
    e = idx[node_id]
    if e["layer"].get("locked"):
        raise OpError(f"{node_id!r} is on a locked layer")
    n = e["node"]
    while n is not None:
        if n.get("locked"):
            raise OpError(f"{n['id']!r} is locked")
        p = idx[n["id"]]["parent"]
        if p is not None and p.get("kind") == "powerclip" and not allow_powerclip:
            raise OpError(f"{node_id!r} is inside a PowerClip (contents are read-only in v1)")
        n = p


def _mark_clip_stale(idx: dict, node_id: str) -> None:
    """A child moved/resized inside a PowerClip: the container's rendered image is now out of date."""
    p = idx[node_id]["parent"]
    while p is not None:
        if p.get("kind") == "powerclip":
            p["stale"] = True
        p = idx[p["id"]]["parent"]


def _refresh_chain(idx_scene: dict, start_parent: dict | None) -> None:
    """Recompute group bboxes upward from `start_parent`; drop groups that became empty."""
    p = start_parent
    while p is not None:
        idx = _index(idx_scene)
        entry = idx.get(p["id"])
        if entry is None:  # already removed as an empty group by an earlier refresh
            return
        parent = entry["parent"]
        if p.get("kind") == "group":
            kids = p.get("children") or []
            if not kids:
                entry["list"].remove(p)
            else:
                x0 = min(k["x"] for k in kids)
                y0 = min(k["y"] for k in kids)
                x1 = max(k["x"] + k["w"] for k in kids)
                y1 = max(k["y"] + k["h"] for k in kids)
                p["x"], p["y"], p["w"], p["h"] = _r(x0), _r(y0), _r(x1 - x0), _r(y1 - y0)
        p = parent


# --------------------------------------------------------------------- ops

def _translate(node: dict, dx: float, dy: float) -> None:
    node["x"], node["y"] = _r(node["x"] + dx), _r(node["y"] + dy)
    for c in node.get("children") or []:
        _translate(c, dx, dy)


def _scale(node: dict, frm: dict, to: dict) -> None:
    sx = to["w"] / frm["w"] if frm["w"] > MIN_SIZE else 1.0
    sy = to["h"] / frm["h"] if frm["h"] > MIN_SIZE else 1.0

    def go(n):
        n["x"] = _r(to["x"] + (n["x"] - frm["x"]) * sx)
        n["y"] = _r(to["y"] + (n["y"] - frm["y"]) * sy)
        n["w"] = _r(n["w"] * sx)
        n["h"] = _r(n["h"] * sy)
        if n.get("text") and n["text"].get("size_pt"):
            n["text"]["size_pt"] = _r(n["text"]["size_pt"] * sy)
        for c in n.get("children") or []:
            go(c)

    go(node)


def _bbox_arg(b: Any, what: str) -> dict:
    try:
        out = {k: float(b[k]) for k in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError):
        raise OpError(f"{what} must be an object with numeric x, y, w, h") from None
    return out


def _op_move(s, op):
    idx = _index(s)
    ids = _top_ids(idx, op.get("ids") or [])
    dx, dy = float(op["dx"]), float(op["dy"])
    for i in ids:
        _check_editable(idx, i, allow_powerclip=True)
    parents = [idx[i]["parent"] for i in ids]
    for i in ids:
        _translate(idx[i]["node"], dx, dy)
        _mark_clip_stale(idx, i)
    for p in parents:
        _refresh_chain(s, p)


def _op_resize(s, op):
    idx = _index(s)
    ids = _top_ids(idx, op.get("ids") or [])
    frm, to = _bbox_arg(op.get("from"), "from"), _bbox_arg(op.get("to"), "to")
    if to["w"] < MIN_SIZE or to["h"] < MIN_SIZE:
        raise OpError("target width/height must be positive")
    for i in ids:
        _check_editable(idx, i, allow_powerclip=True)
    parents = [idx[i]["parent"] for i in ids]
    for i in ids:
        _scale(idx[i]["node"], frm, to)
        _mark_clip_stale(idx, i)
    for p in parents:
        _refresh_chain(s, p)


def _op_order(s, op):
    idx = _index(s)
    e = _need(idx, op["id"])
    if e.get("is_layer"):
        raise OpError("cannot reorder a layer with 'order'")
    _check_editable(idx, op["id"])
    lst, n = e["list"], e["node"]
    i = lst.index(n)
    mode = op.get("mode")
    if mode == "front":
        lst.append(lst.pop(i))
    elif mode == "back":
        lst.insert(0, lst.pop(i))
    elif mode == "forward":
        if i < len(lst) - 1:
            lst[i], lst[i + 1] = lst[i + 1], lst[i]
    elif mode == "backward":
        if i > 0:
            lst[i], lst[i - 1] = lst[i - 1], lst[i]
    else:
        raise OpError(f"unknown order mode {mode!r}")


def _op_reorder(s, op):
    idx = _index(s)
    e = _need(idx, op["id"])
    if e.get("is_layer"):
        raise OpError("layers are reordered with 'layer_order'")
    _check_editable(idx, op["id"])
    dest = _need(idx, op["parent"])
    dn = dest["node"]
    if not dest.get("is_layer") and dn.get("kind") != "group":
        raise OpError(f"{op['parent']!r} is not a layer or group")
    if dest["layer"].get("locked"):
        raise OpError("destination layer is locked")
    p = dn
    while p is not None and not dest.get("is_layer"):
        if p["id"] == op["id"]:
            raise OpError("cannot move an object into itself")
        p = idx[p["id"]]["parent"]
    node, old_parent = e["node"], e["parent"]
    e["list"].remove(node)
    target = dn["children"]
    index = max(0, min(int(op["index"]), len(target)))
    target.insert(index, node)
    _refresh_chain(s, old_parent)
    _refresh_chain(s, None if dest.get("is_layer") else dn)


def _op_visibility(s, op):
    idx = _index(s)
    _need(idx, op["id"])["node"]["visible"] = bool(op["visible"])


def _op_group(s, op):
    idx = _index(s)
    ids = list(dict.fromkeys(op.get("ids") or []))
    if len(ids) < 2:
        raise OpError("grouping needs at least two objects")
    gid = op["group_id"]
    if gid in idx:
        raise OpError(f"id {gid!r} already exists")
    entries = [_need(idx, i) for i in ids]
    if any(e.get("is_layer") for e in entries):
        raise OpError("cannot group layers")
    first_list = entries[0]["list"]
    if any(e["list"] is not first_list for e in entries):
        raise OpError("objects must share the same parent to be grouped")
    for i in ids:
        _check_editable(idx, i)
    members = sorted((e["node"] for e in entries), key=first_list.index)
    at = max(first_list.index(m) for m in members)
    # position after removal = topmost member's index minus the members below it
    pos = at - (len(members) - 1)
    for m in members:
        first_list.remove(m)
    x0 = min(m["x"] for m in members)
    y0 = min(m["y"] for m in members)
    x1 = max(m["x"] + m["w"] for m in members)
    y1 = max(m["y"] + m["h"] for m in members)
    group = {
        "id": gid, "kind": "group", "type": "group", "name": op.get("name") or "Group",
        "x": _r(x0), "y": _r(y0), "w": _r(x1 - x0), "h": _r(y1 - y0),
        "rotation": 0.0, "visible": True, "locked": False, "image": None,
        "children": members,
    }
    first_list.insert(pos, group)
    parent = entries[0]["parent"]
    _refresh_chain(s, parent)


def _op_ungroup(s, op):
    idx = _index(s)
    e = _need(idx, op["id"])
    n = e["node"]
    if e.get("is_layer") or n.get("kind") != "group":
        raise OpError(f"{op['id']!r} is not a group")
    _check_editable(idx, op["id"])
    lst = e["list"]
    i = lst.index(n)
    lst[i:i + 1] = n["children"]


def _op_text(s, op):
    idx = _index(s)
    e = _need(idx, op["id"])
    n = e["node"]
    if not n.get("text"):
        raise OpError(f"{op['id']!r} is not a text object")
    _check_editable(idx, op["id"], allow_powerclip=True)
    t = n["text"]
    if "content" in op and op["content"] is not None:
        t["content"] = str(op["content"])
        n["stale"] = True
    if "font" in op and op["font"] is not None:
        t["font"] = str(op["font"])
        n["stale"] = True
    if "size_pt" in op and op["size_pt"] is not None:
        size = float(op["size_pt"])
        if size <= 0:
            raise OpError("font size must be positive")
        t["size_pt"] = _r(size)
        n["stale"] = True


def _op_delete(s, op):
    idx = _index(s)
    ids = _top_ids(idx, op.get("ids") or [])
    for i in ids:
        _check_editable(idx, i)
    parents = [idx[i]["parent"] for i in ids]
    for i in ids:
        e = idx[i]
        e["list"].remove(e["node"])
    for p in parents:
        _refresh_chain(s, p)


def _collect_ids(node: dict, out: list[str]) -> None:
    out.append(node["id"])
    for c in node.get("children") or []:
        _collect_ids(c, out)


def _op_paste(s, op):
    idx = _index(s)
    dest = _need(idx, op["parent"])
    dn = dest["node"]
    if not dest.get("is_layer") and dn.get("kind") != "group":
        raise OpError(f"{op['parent']!r} is not a layer or group")
    if dest["layer"].get("locked"):
        raise OpError("destination layer is locked")
    nodes = op.get("nodes") or []
    if not nodes:
        raise OpError("paste has no nodes")
    new_ids: list[str] = []
    for n in nodes:
        _collect_ids(n, new_ids)
    clash = [i for i in new_ids if i in idx]
    if clash or len(set(new_ids)) != len(new_ids):
        raise OpError(f"pasted ids are not unique: {clash or 'duplicates inside paste'}")
    for n in nodes:
        dn["children"].append(copy.deepcopy(n))
    _refresh_chain(s, None if dest.get("is_layer") else dn)


def _op_page(s, op):
    w, h = float(op["width"]), float(op["height"])
    if w < MIN_SIZE or h < MIN_SIZE:
        raise OpError("page size must be positive")
    s["page"]["width"], s["page"]["height"] = _r(w), _r(h)


def _op_layer_order(s, op):
    idx = _index(s)
    e = _need(idx, op["id"])
    if not e.get("is_layer"):
        raise OpError(f"{op['id']!r} is not a layer")
    layers = s["layers"]
    layers.remove(e["node"])
    layers.insert(max(0, min(int(op["index"]), len(layers))), e["node"])


_APPLY = {
    "move": _op_move, "resize": _op_resize, "order": _op_order, "reorder": _op_reorder,
    "visibility": _op_visibility, "group": _op_group, "ungroup": _op_ungroup,
    "text": _op_text, "delete": _op_delete, "paste": _op_paste, "page": _op_page,
    "layer_order": _op_layer_order,
}


def apply_op(scene: dict, op: dict) -> None:
    """Mutates `scene` in place. Raises OpError (scene may be partially changed)."""
    fn = _APPLY.get(op.get("op"))
    if fn is None:
        raise OpError(f"unknown op {op.get('op')!r}")
    try:
        fn(scene, op)
    except KeyError as e:
        raise OpError(f"missing field {e}") from None
    except (TypeError, ValueError) as e:
        if isinstance(e, OpError):
            raise
        raise OpError(str(e)) from None


def apply_ops(scene: dict, ops: list[dict]) -> dict:
    """Returns a new scene; the input is never modified."""
    out = copy.deepcopy(scene)
    for n, op in enumerate(ops):
        try:
            apply_op(out, op)
        except OpError as e:
            raise OpError(f"op #{n} ({op.get('op')}): {e}") from None
    return out
