"""Phase 5a: the target-specific adaptation plan, computed from a stored master model + a target size. Never touches CorelDRAW.

    plan_adaptation(model, target_w, target_h) -> AdaptationPlan      (pure, deterministic)
    apply_to_placed(placed, model, objs, ...)   -> summary dict       (hands a valid plan to the legacy `Placed` list)

Strategies (`plan.strategy`):
- "uniform"   same aspect ratio: every coordinate and size scales by one factor.
- "fit"       same orientation, other aspect ratio: each object follows its adaptation mode (STRETCH only for backgrounds and page-spanning
              rules, UNIFORM_SCALE / TEXT_FIT about its own centre, ANCHOR keeps its distance to the edge it sits on, FIXED keeps its size,
              loose logo fragments move as one rigid unit - `layout._place_logo_units`, the engine's own rule, is reused for that).
- "reflow"    the board changes orientation: the editor's zone logic (`orientation_adapter.convert_orientation`) runs on a scene built from the model
              and the final boxes of the model's objects are read back. Nothing is duplicated.
- "declined"  the plan does not apply (tiled board: the legacy tiling places it; unsupported input). `plan.reason` says why.

The legacy engine stays the owner of text replacement and fitting: `apply_to_placed` only moves/sizes objects that the legacy path left to its
plain rules, never a shop-name line, a text-fit spec, a PowerClip plan or a tile copy.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from . import layout, master_model as mm, orientation_adapter as oa, plan_validation as pv, scene_ops

SAME_ASPECT_TOL = 0.005
MATCH_TOL_MM = 0.5            # an engine object counts as the model's object when its box agrees within this (+ MATCH_TOL_FRAC of its size)
MATCH_TOL_FRAC = 0.002


@dataclass
class PlannedObject:
    id: str
    mode: str
    confidence: float
    reason: str
    box: dict                      # target millimetres, origin bottom-left: x, y, width, height
    source_box: dict
    constraints: dict = field(default_factory=dict)


@dataclass
class AdaptationPlan:
    strategy: str
    supported: bool
    target: dict
    source: dict
    objects: list[PlannedObject]
    reason: str = ""
    tiling: dict | None = None
    warnings: list[str] = field(default_factory=list)
    validation: list[dict] = field(default_factory=list)
    master_hash: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def box_of(self, oid: str) -> dict | None:
        return next((p.box for p in self.objects if p.id == oid), None)


def _orientation(w: float, h: float) -> str:
    return "portrait" if w < h else "landscape" if w > h else "square"


def _box(x, y, w, h) -> dict:
    return {"x": round(float(x), 4), "y": round(float(y), 4), "width": round(float(w), 4), "height": round(float(h), 4)}


def _layout_obj(o: mm.ModelObject) -> layout.Obj:
    return layout.Obj(o.id, o.name, mm._layout_kind(o), o.box_mm["x"], o.box_mm["y"], o.box_mm["width"], o.box_mm["height"],
                      (o.text or {}).get("content"), mm._cmyk_of(o), o.n_descendants if o.type == "group" else 0)


def plan_adaptation(model: mm.MasterModel, target_w: float, target_h: float, *, validate: bool = True) -> AdaptationPlan:
    if target_w <= 0 or target_h <= 0:
        raise ValueError("target size must be positive")
    pw, ph = model.page_w, model.page_h
    target = {"width_mm": float(target_w), "height_mm": float(target_h), "orientation": _orientation(target_w, target_h)}
    source = {"width_mm": pw, "height_mm": ph, "orientation": model.orientation}
    tops = [o for o in sorted(model.top_level(), key=lambda o: o.z_order) if o.visible]

    def declined(reason: str, tiling: dict | None = None) -> AdaptationPlan:
        return AdaptationPlan("declined", False, target, source, [], reason=reason, tiling=tiling, master_hash=model.master_hash)

    axis, n_tiles = layout._tile_plan(pw, ph, target_w, target_h)
    if not tops:
        return declined("the master has no visible top-level objects")

    cross = ({source["orientation"], target["orientation"]} == {"landscape", "portrait"})
    if abs((target_w / target_h) / (pw / ph) - 1) < SAME_ASPECT_TOL:
        plan = _plan_uniform(model, tops, target, source)
    elif cross:
        plan = _plan_reflow(model, tops, target, source)          # an orientation change is a re-composition, not a tiling
    elif axis and n_tiles > 1:
        plan = _plan_tile(model, tops, target, source, axis, n_tiles)
    else:
        plan = _plan_fit(model, tops, target, source)
    plan.master_hash = model.master_hash
    if validate and plan.supported:
        plan.validation = [i.to_dict() for i in pv.validate_plan(model, plan)]
    return plan


# ------------------------------------------------------------------------------------------------------- strategies


def _plan_uniform(model, tops, target, source) -> AdaptationPlan:
    s = target["width_mm"] / model.page_w
    objs = []
    for o in tops:
        b = o.box_mm
        objs.append(PlannedObject(o.id, o.adaptation_mode or mm.UNIFORM_SCALE, o.mode_confidence, "same aspect ratio: one scale factor for everything",
                                  _box(b["x"] * s, b["y"] * s, b["width"] * s, b["height"] * s), dict(b), dict(o.constraints)))
    return AdaptationPlan("uniform", True, target, source, objs, reason=f"scale x{s:.4g}")


def _plan_fit(model, tops, target, source) -> AdaptationPlan:
    pw, ph = model.page_w, model.page_h
    tw, th = target["width_mm"], target["height_mm"]
    s = min(tw / pw, th / ph)
    fx, fy = tw / pw, th / ph
    by_id = {o.id: o for o in tops}
    logos = [_layout_obj(o) for o in tops if o.role not in ("bg", "frame", "fixed", "shopname") and o.type != "text"]
    unit_boxes = layout._place_logo_units(logos, pw, ph, tw, th, s, 0.0)
    warnings: list[str] = []
    out: list[PlannedObject] = []
    for o in tops:
        b = o.box_mm
        mode, conf, reason, cons = o.adaptation_mode or mm.UNIFORM_SCALE, o.mode_confidence, o.mode_reason, dict(o.constraints)
        if o.id in unit_boxes and o.role not in ("bg", "frame", "fixed"):
            x, y, w, h, _w = unit_boxes[o.id]
            axis_stretch = o.box_mm["width"] >= layout.FULL_SPAN_FRAC * pw or o.box_mm["height"] >= layout.FULL_SPAN_FRAC * ph
            if axis_stretch and o.kind == "shape":
                mode, reason = mm.STRETCH, "page-spanning rule or band follows the page"
            else:
                mode, reason, cons = mm.UNIFORM_SCALE, "moves with its visual unit (rigid)", {**cons, "unit_member": True}
        elif o.role == "bg":
            x, y, w, h = 0.0, 0.0, tw, th
            mode, reason = mm.STRETCH, "background fills the board"
        elif o.role == "frame":
            ml, mb = b["x"], b["y"]
            mr, mt = pw - (b["x"] + b["width"]), ph - (b["y"] + b["height"])
            x, y, w, h = ml, mb, tw - ml - mr, th - mb - mt
            if w <= 0 or h <= 0:
                x, y, w, h = b["x"] * s, b["y"] * s, b["width"] * s, b["height"] * s
                warnings.append(f"frame {o.id}: margins larger than the new page - scaled instead")
        elif o.role == "fixed" or mode == mm.FIXED:
            w, h = b["width"], b["height"]
            x, y = layout._anchor_axis(b["x"], b["width"], pw, tw), layout._anchor_axis(b["y"], b["height"], ph, th)
        elif mode == mm.ANCHOR and "anchor" in cons and isinstance(cons["anchor"], list):
            w, h = b["width"] * s, b["height"] * s
            sides = cons["anchor"]
            if "left" in sides:
                x = b["x"] * s
            elif "right" in sides:
                x = tw - (pw - (b["x"] + b["width"])) * s - w
            else:
                x = (b["x"] + b["width"] / 2) * fx - w / 2
            if "bottom" in sides:
                y = b["y"] * s
            elif "top" in sides:
                y = th - (ph - (b["y"] + b["height"])) * s - h
            else:
                y = (b["y"] + b["height"] / 2) * fy - h / 2
        else:
            w, h = b["width"] * s, b["height"] * s
            x, y = (b["x"] + b["width"] / 2) * fx - w / 2, (b["y"] + b["height"] / 2) * fy - h / 2
        if o.role not in ("bg", "frame") and mode != mm.STRETCH:       # keep inside the board (nudge, never resize)
            nx, ny = min(max(x, 0.0), max(tw - w, 0.0)), min(max(y, 0.0), max(th - h, 0.0))
            if abs(nx - x) > 1e-6 or abs(ny - y) > 1e-6:
                warnings.append(f"{o.id}: moved {math.hypot(nx - x, ny - y):.1f} mm to stay on the board")
            x, y = nx, ny
        out.append(PlannedObject(o.id, mode, conf, reason, _box(x, y, w, h), dict(b), cons))
    return AdaptationPlan("fit", True, target, source, out, reason=f"scale x{s:.4g} (limited by the tighter axis)", warnings=warnings)


def _plan_tile(model, tops, target, source, axis: str, n_tiles: int) -> AdaptationPlan:
    """A very wide / tall board repeats its panel. The legacy rigid-panel tiling (`layout._place_tiled_panel`) is reused for the logo-role
    objects (copies get `<id>_tile<n>` ids, like the engine's); everything else follows the `fit` rules. This plan is an ANALYSIS: the legacy
    engine still performs tiling (`apply_to_placed` declines tiled plans), because tile copies are CorelDRAW `Duplicate()` calls it owns."""
    base = _plan_fit(model, tops, target, source)
    tw, th = target["width_mm"], target["height_mm"]
    panel = [o for o in tops if o.role == "logo" and o.type != "text"]
    if not panel:
        base.strategy, base.tiling = "tile", {"axis": axis, "tiles": n_tiles, "panel_objects": 0}
        base.warnings.append("tiled target but the master has no logo-role objects to repeat")
        return base
    roles = {o.id: o.role for o in tops}
    placed, scale = layout._place_tiled_panel([_layout_obj(o) for o in panel], roles, tw, th, axis, n_tiles)
    by = {p.id: p for p in base.objects}
    copies: list[PlannedObject] = []
    for pl in placed:
        oid, _, tile = pl.id.partition("_tile")
        src = by.get(oid)
        if src is None:
            continue
        box = _box(pl.x, pl.y, pl.w, pl.h)
        if tile in ("", "0"):
            src.box, src.mode, src.reason = box, mm.TILE, f"panel {1}/{n_tiles} (rigid panel scaled x{scale:.3g})"
        else:
            copies.append(PlannedObject(pl.id, mm.TILE, 0.6, f"panel copy {int(tile) + 1}/{n_tiles}", box, dict(src.source_box), {"copy_of": oid}))
    base.objects.extend(copies)
    base.strategy, base.tiling = "tile", {"axis": axis, "tiles": n_tiles, "panel_objects": len(panel), "panel_scale": round(scale, 5)}
    base.reason = f"{n_tiles} panels along {axis}"
    return base


def _plan_reflow(model, tops, target, source) -> AdaptationPlan:
    """Orientation change: the editor's zone logic on a scene built from the model. Only top-level, visible objects are placed."""
    tw, th = target["width_mm"], target["height_mm"]
    try:
        scene = model.to_scene()
        zones, _warn = oa.classify_zones(scene)
        nested = sorted(i for ids in zones.values() for i in ids if "." in str(i))
        if nested:
            return AdaptationPlan("declined", False, target, source, [],
                                  reason=f"foreground objects sit inside the background PowerClip ({', '.join(nested[:4])}...): the engine cannot place them separately")
        ops = oa.convert_orientation(scene, tw, th)
        final = scene_ops.apply_ops(scene, ops)
    except Exception as e:  # noqa: BLE001 - OpError / unexpected scene shape: the legacy path decides
        return AdaptationPlan("declined", False, target, source, [], reason=f"reflow unavailable: {type(e).__name__}: {e}")
    boxes: dict[str, dict] = {}
    for n in scene_ops.iter_nodes(final):
        boxes[str(n["id"])] = _box(n["x"], n["y"], n["w"], n["h"])
    out: list[PlannedObject] = []
    for o in tops:
        fb = boxes.get(o.id)
        if fb is None:
            return AdaptationPlan("declined", False, target, source, [], reason=f"reflow lost object {o.id}")
        out.append(PlannedObject(o.id, mm.REFLOW, 0.6, "orientation change: placed by the editor's semantic zones", fb, dict(o.box_mm), {}))
    return AdaptationPlan("reflow", True, target, source, out, reason=f"{source['orientation']} -> {target['orientation']}",
                          warnings=["reflow is a draft: review the layout"])


# ------------------------------------------------------------------------------------------------ legacy integration


def model_matches_objects(model: mm.MasterModel, objs: list) -> tuple[bool, str]:
    """Do the engine's top-level `layout.Obj`s (`page.Shapes` order, id = index) match the model's top-level objects? The two id spaces
    can differ (layers vs page.Shapes), so a plan is only used when count, ids and boxes agree."""
    top = {o.id: o for o in model.top_level()}
    if len(objs) != len(top):
        return False, f"object count differs ({len(objs)} in the file, {len(top)} in the model)"
    for o in objs:
        m = top.get(o.id)
        if m is None:
            return False, f"object {o.id} is not in the model"
        b = m.box_mm
        tol = MATCH_TOL_MM + MATCH_TOL_FRAC * max(o.w, o.h)
        if max(abs(o.x - b["x"]), abs(o.y - b["y"]), abs(o.w - b["width"]), abs(o.h - b["height"])) > tol:
            return False, f"object {o.id} differs from the model (the master changed?)"
    return True, ""


def eligible(p) -> bool:
    """A `Placed` the plan may move: a plain object the legacy path positioned by its generic rules."""
    return (p.role not in ("shopname", "bg") and not p.text_fit and not p.clip_plan and "_tile" not in p.id and not p.bring_to_front
            and p.recolor_cmyk is None)


def apply_to_placed(placed: list, model: mm.MasterModel, objs: list, new_w: float, new_h: float, *, page_w: float, page_h: float) -> dict:
    """Replace the geometry of eligible `Placed` objects with the plan's. Returns a summary {used, reason, strategy, applied, validation}.
    The legacy result is kept untouched when anything is off (no match, declined, validation errors, tiled, an exception)."""
    summary: dict = {"used": False}
    try:
        ok, why = model_matches_objects(model, objs)
        if not ok:
            return {**summary, "reason": why}
        if abs(model.page_w - page_w) > 0.5 or abs(model.page_h - page_h) > 0.5:
            return {**summary, "reason": "the model's page size differs from the opened master"}
        plan = plan_adaptation(model, new_w, new_h)
        summary.update(strategy=plan.strategy, validation=plan.validation, plan_reason=plan.reason)
        if not plan.supported:
            return {**summary, "reason": plan.reason}
        if plan.strategy == "tile":
            return {**summary, "reason": "tiled target: the legacy engine places the panel copies"}
        if any(v["severity"] == pv.ERROR for v in plan.validation):
            return {**summary, "reason": "the plan failed validation: " + "; ".join(v["rule"] for v in plan.validation if v["severity"] == pv.ERROR)}
        moves = [(p, plan.box_of(p.id)) for p in placed if eligible(p) and plan.box_of(p.id) is not None]
        for p, box in moves:                      # everything was computed above: nothing here can fail half way
            p.x, p.y, p.w, p.h = box["x"], box["y"], box["width"], box["height"]
        applied = len(moves)
        summary.update(used=True, applied=applied, reason="")
    except Exception as e:  # noqa: BLE001 - an integration error never corrupts the legacy result
        summary.update(used=False, reason=f"{type(e).__name__}: {e}")
    return summary
