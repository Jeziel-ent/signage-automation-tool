"""Phase 2 of master spatial intelligence: the reusable spatial model of a master, built from a `master_analysis` extraction.

The model never replaces CorelDRAW geometry: every object keeps its exact box in millimetres (origin bottom-left) and ADDS normalized
coordinates, adaptive row / column bands, a semantic region, spatial relationships and a suggested adaptation behaviour. It is pure
Python (no COM), JSON serializable, and is analysed once per master version - target-specific placement is `adaptation_plan`'s job.

Reused, not duplicated: `layout.detect_role` (roles from tags / area / kind), `layout.cluster_fragments` (visual units of loose shapes),
`layout.FULL_SPAN_FRAC`, and the zone bands of `orientation_adapter` (`_child_zone`, so a master's regions mean the same as the editor's).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from . import layout, orientation_adapter as oa

SCHEMA_VERSION = 1

# adaptation modes (a plan may also use REFLOW for a whole conversion that changes orientation)
STRETCH, UNIFORM_SCALE, ANCHOR, REFLOW, TILE, FIXED, TEXT_FIT = "STRETCH", "UNIFORM_SCALE", "ANCHOR", "REFLOW", "TILE", "FIXED", "TEXT_FIT"
MODES = (STRETCH, UNIFORM_SCALE, ANCHOR, REFLOW, TILE, FIXED, TEXT_FIT)

BAND_GAP_FRAC = 0.006         # a clear gap this wide (share of the page dimension) ends a row / column band
ALIGN_TOL_FRAC = 0.004        # edges / centres within this share of the page dimension count as aligned
EQUAL_GAP_TOL_FRAC = 0.004    # gaps of a run equal within this share of the page dimension
REPEAT_SIZE_TOL = 0.03        # same type and size within 3% = a repeated element
CONTAIN_FRAC = 0.95           # share of B's area inside A for "A contains B"
OVERLAP_MIN_FRAC = 0.05       # share of the smaller box's area to report an overlap
EDGE_TOUCH_FRAC = 0.01        # an object this close to a page edge is anchored to it
MAX_PAIRWISE = 400            # top-level objects above this skip the O(n^2) relation pass (reported in `warnings`)


@dataclass
class ModelObject:
    id: str
    scene_id: str | None
    parent: str | None
    children: list[str]
    z_order: int
    name: str
    type: str
    kind: str
    box_mm: dict
    normalized: dict
    rotation: float = 0.0
    visible: bool = True
    locked: bool = False
    role: str | None = None
    appearance: dict = field(default_factory=dict)
    text: dict | None = None
    bitmap: dict | None = None
    powerclip: dict | None = None
    n_descendants: int = 0
    region: str | None = None
    h_region: str | None = None
    bands: dict = field(default_factory=dict)
    adaptation_mode: str | None = None
    mode_confidence: float = 0.0
    mode_reason: str = ""
    constraints: dict = field(default_factory=dict)
    background: dict | None = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "ModelObject":
        known = cls.__dataclass_fields__
        o = cls(**{k: d[k] for k in known if k in d and k not in ("appearance", "bands", "constraints")})
        o.children = list(d.get("children") or [])
        o.appearance = dict(d.get("appearance") or {})
        o.bands = dict(d.get("bands") or {})
        o.constraints = dict(d.get("constraints") or {})
        return o


@dataclass
class MasterModel:
    schema_version: int
    master_hash: str | None
    corel_version: str | None
    extractor_version: int
    page: dict
    orientation: str
    objects: list[ModelObject]
    bands: dict = field(default_factory=dict)
    relations: list[dict] = field(default_factory=list)
    units: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    shape_sets: dict = field(default_factory=dict)
    extraction_errors: dict = field(default_factory=dict)
    background: dict | None = None

    @property
    def page_w(self) -> float:
        return float(self.page["width_mm"])

    @property
    def page_h(self) -> float:
        return float(self.page["height_mm"])

    def top_level(self) -> list[ModelObject]:
        return [o for o in self.objects if o.parent is None]

    def get(self, oid: str) -> ModelObject | None:
        return next((o for o in self.objects if o.id == oid), None)

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version, "master_hash": self.master_hash, "corel_version": self.corel_version,
                "extractor_version": self.extractor_version, "page": self.page, "orientation": self.orientation,
                "objects": [o.to_dict() for o in self.objects], "bands": self.bands, "relations": self.relations,
                "units": self.units, "warnings": self.warnings, "shape_sets": self.shape_sets,
                "extraction_errors": self.extraction_errors, "background": self.background}

    @classmethod
    def from_dict(cls, d: dict) -> "MasterModel":
        if int(d.get("schema_version", -1)) != SCHEMA_VERSION:
            raise ValueError(f"unsupported spatial model schema {d.get('schema_version')!r} (this code reads {SCHEMA_VERSION})")
        m = cls(schema_version=SCHEMA_VERSION, master_hash=d.get("master_hash"), corel_version=d.get("corel_version"),
                extractor_version=int(d.get("extractor_version", 0)), page=dict(d["page"]), orientation=d.get("orientation", "landscape"),
                objects=[ModelObject.from_dict(o) for o in d.get("objects") or []], bands=dict(d.get("bands") or {}),
                relations=list(d.get("relations") or []), units=list(d.get("units") or []), warnings=list(d.get("warnings") or []),
                shape_sets=dict(d.get("shape_sets") or {}), extraction_errors=dict(d.get("extraction_errors") or {}),
                background=d.get("background"))
        m.validate()
        return m

    def validate(self) -> None:
        """Raises ValueError when the geometry cannot be trusted (callers fall back to the legacy layout path)."""
        if self.page_w <= 0 or self.page_h <= 0:
            raise ValueError("page size must be positive")
        seen: set[str] = set()
        for o in self.objects:
            if o.id in seen:
                raise ValueError(f"duplicate object id {o.id}")
            seen.add(o.id)
            b = o.box_mm
            for k in ("x", "y", "width", "height"):
                v = b.get(k)
                if not isinstance(v, (int, float)) or v != v or abs(v) > 1e9:
                    raise ValueError(f"object {o.id}: bad {k}")
            if b["width"] < 0 or b["height"] < 0:
                raise ValueError(f"object {o.id}: negative size")

    def to_scene(self) -> dict:
        """An editor-scene dict (what `orientation_adapter` / `scene_ops` consume) built from the model, so the editor's semantic
        zone logic can run on a master without a converted board. Ids are the model's ids ("3", "3.1"), one layer."""
        by_id = {o.id: o for o in self.objects}

        def node(o: ModelObject) -> dict:
            n = {"id": o.id, "kind": o.kind if o.kind in ("group", "powerclip") else "shape", "type": o.type, "name": o.name,
                 "x": o.box_mm["x"], "y": o.box_mm["y"], "w": o.box_mm["width"], "h": o.box_mm["height"],
                 "rotation": o.rotation, "visible": o.visible, "locked": o.locked, "image": None}
            if o.text:
                n["text"] = dict(o.text)
            if o.kind == "powerclip":
                n["frame_rect"] = bool((o.powerclip or {}).get("frame_rect"))
            if o.children:
                n["children"] = [node(by_id[c]) for c in o.children if c in by_id]
            return n

        tops = sorted(self.top_level(), key=lambda o: o.z_order)
        return {"version": 6, "unit": "mm", "page": {"width": self.page_w, "height": self.page_h},
                "layers": [{"id": "L1", "name": "Layer 1", "visible": True, "locked": False, "children": [node(o) for o in tops]}]}


# ---------------------------------------------------------------------------------------------------------------- helpers


def _orientation(w: float, h: float) -> str:
    return "portrait" if w < h else "landscape" if w > h else "square"


def _inter(a: dict, b: dict) -> float:
    ox = max(0.0, min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"]))
    oy = max(0.0, min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"]))
    return ox * oy


def _area(b: dict) -> float:
    return b["width"] * b["height"]


def _merge_intervals(iv: list[tuple[float, float]], gap: float) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(iv):
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _count_descendants(by_id: dict, o: ModelObject) -> int:
    return sum(1 + _count_descendants(by_id, by_id[c]) for c in o.children if c in by_id)


def _layout_kind(o: ModelObject) -> str:
    return "text" if o.type == "text" else "bitmap" if o.type == "bitmap" else "group" if o.type == "group" else "shape"


def _cmyk_of(o: ModelObject) -> tuple | None:
    f = o.appearance.get("fill") or {}
    c = (f.get("color") or {}).get("cmyk") if f.get("type") == "uniform" else None
    return tuple(c) if c else None


def build_model(extraction: dict, master_hash: str | None = None, background: dict | None = None) -> MasterModel:
    """Spatial model from a `master_analysis.extract_master` result. Raises ValueError on an unusable extraction."""
    page = extraction.get("page") or {}
    pw, ph = float(page.get("width_mm") or 0), float(page.get("height_mm") or 0)
    if pw <= 0 or ph <= 0:
        raise ValueError("the extraction has no valid page size")
    objects: list[ModelObject] = []
    for e in extraction.get("objects") or []:
        box = {"x": float(e["x"]), "y": float(e["y"]), "width": float(e["w"]), "height": float(e["h"])}
        app = {k: e[k] for k in ("fill", "outline", "transparency") if e.get(k) is not None}
        objects.append(ModelObject(
            id=str(e["id"]), scene_id=e.get("scene_id"), parent=e.get("parent"), children=list(e.get("children") or []),
            z_order=int(e.get("z", 0)), name=e.get("name") or "", type=e.get("type") or "unknown", kind=e.get("kind") or "shape",
            box_mm=box, normalized={"x": round(box["x"] / pw, 6), "y": round(box["y"] / ph, 6),
                                    "width": round(box["width"] / pw, 6), "height": round(box["height"] / ph, 6)},
            rotation=float(e.get("rotation") or 0.0), visible=bool(e.get("visible", True)), locked=bool(e.get("locked", False)),
            appearance=app, text=e.get("text"), bitmap=e.get("bitmap"), powerclip=e.get("powerclip")))
    model = MasterModel(SCHEMA_VERSION, master_hash, extraction.get("corel_version"), int(extraction.get("extractor_version") or 0),
                        {"width_mm": pw, "height_mm": ph, "unit": "mm", "origin": page.get("origin", "bottom-left")},
                        _orientation(pw, ph), objects, shape_sets=dict(extraction.get("shape_sets") or {}),
                        extraction_errors=dict(extraction.get("errors") or {}))
    if extraction.get("truncated"):
        model.warnings.append("the extraction stopped at the object limit: the model is incomplete")
    if model.shape_sets and not model.shape_sets.get("equal", True):
        model.warnings.append("page.Shapes and the layer walk list different shapes (see shape_sets)")
    model.validate()
    by_id = {o.id: o for o in objects}
    for o in objects:
        o.n_descendants = _count_descendants(by_id, o)
    _assign_roles(model)
    _bands(model)
    _regions(model)
    _relations(model)
    _modes(model)
    if background:
        attach_background(model, background)
    return model


def attach_background(model: MasterModel, background: dict) -> None:
    """`background` = background_analysis.analyze_background result: per-object entries land on the objects, the rest on the model."""
    per = background.get("objects") or {}
    for o in model.objects:
        if o.id in per:
            o.background = per[o.id]
    model.background = {k: v for k, v in background.items() if k != "objects"}


def _assign_roles(model: MasterModel) -> None:
    pw, ph = model.page_w, model.page_h
    for o in model.top_level():
        lo = layout.Obj(o.id, o.name, _layout_kind(o), o.box_mm["x"], o.box_mm["y"], o.box_mm["width"], o.box_mm["height"],
                        (o.text or {}).get("content"), _cmyk_of(o), o.n_descendants if o.type == "group" else 0)
        o.role = layout.detect_role(lo, pw, ph)


def _bands(model: MasterModel) -> None:
    """Adaptive row / column bands: merge the page-axis projections of the foreground objects and cut where a clear gap opens. Objects
    spanning the page (backgrounds, rules) are left out so they do not fuse everything into one band."""
    pw, ph = model.page_w, model.page_h
    fg = [o for o in model.top_level() if o.visible and o.role not in ("bg", "frame") and not _spans_page(o, pw, ph)]
    cols = _merge_intervals([(o.box_mm["x"], o.box_mm["x"] + o.box_mm["width"]) for o in fg], BAND_GAP_FRAC * pw)
    rows = _merge_intervals([(o.box_mm["y"], o.box_mm["y"] + o.box_mm["height"]) for o in fg], BAND_GAP_FRAC * ph)
    model.bands = {"columns": [{"index": i, "x0": round(a / pw, 5), "x1": round(b / pw, 5)} for i, (a, b) in enumerate(cols)],
                   "rows": [{"index": i, "y0": round(a / ph, 5), "y1": round(b / ph, 5)} for i, (a, b) in enumerate(rows)]}
    for o in model.top_level():
        x0, x1 = o.box_mm["x"], o.box_mm["x"] + o.box_mm["width"]
        y0, y1 = o.box_mm["y"], o.box_mm["y"] + o.box_mm["height"]
        o.bands = {"columns": [i for i, (a, b) in enumerate(cols) if x0 < b and x1 > a],
                   "rows": [i for i, (a, b) in enumerate(rows) if y0 < b and y1 > a]}


def _spans_page(o: ModelObject, pw: float, ph: float) -> bool:
    return o.box_mm["width"] >= layout.FULL_SPAN_FRAC * pw or o.box_mm["height"] >= layout.FULL_SPAN_FRAC * ph


def _regions(model: MasterModel) -> None:
    pw, ph = model.page_w, model.page_h
    for o in model.top_level():
        if o.role == "bg":
            o.region, o.h_region = "background", "full"
            continue
        node = {"x": o.box_mm["x"], "y": o.box_mm["y"], "w": o.box_mm["width"], "h": o.box_mm["height"], "name": o.name,
                "text": o.text, "children": [], "kind": o.kind, "type": o.type}
        try:
            o.region = oa._child_zone(node, pw, ph)           # header / product / main_text / footer (same bands as the editor)
        except Exception:                                       # noqa: BLE001 - e.g. a node shape the helper does not expect
            cy = (o.box_mm["y"] + o.box_mm["height"] / 2) / ph
            o.region = "header" if cy >= 0.72 else "main_text" if cy >= 0.55 else "product" if cy >= 0.20 else "footer"
        cx = (o.box_mm["x"] + o.box_mm["width"] / 2) / pw
        o.h_region = "left" if cx < 1 / 3 else "right" if cx > 2 / 3 else "center"


def _relations(model: MasterModel) -> None:
    pw, ph = model.page_w, model.page_h
    tops = [o for o in model.top_level() if o.visible]
    rels: list[dict] = []
    # visual units of loose shapes (the engine's own rule), as "same visual group" relations
    loose = [o for o in tops if o.role == "logo" and o.kind == "shape" and o.type != "text" and not _spans_page(o, pw, ph)]
    lobj = [layout.Obj(o.id, o.name, "shape", o.box_mm["x"], o.box_mm["y"], o.box_mm["width"], o.box_mm["height"]) for o in loose]
    for n, members in enumerate(layout.cluster_fragments(lobj, pw, ph)):
        if len(members) >= 2:
            xs0 = min(m.x for m in members); ys0 = min(m.y for m in members)
            xs1 = max(m.x + m.w for m in members); ys1 = max(m.y + m.h for m in members)
            model.units.append({"id": f"u{n}", "members": [m.id for m in members],
                                "box_mm": {"x": xs0, "y": ys0, "width": xs1 - xs0, "height": ys1 - ys0}})
    if len(tops) > MAX_PAIRWISE:
        model.warnings.append(f"{len(tops)} top-level objects: pairwise relations skipped (limit {MAX_PAIRWISE})")
        model.relations = rels
        return
    tol_x, tol_y = ALIGN_TOL_FRAC * pw, ALIGN_TOL_FRAC * ph
    for a, b in combinations(tops, 2):
        ia = _inter(a.box_mm, b.box_mm)
        if ia <= 0:
            continue
        small, big = (a, b) if _area(a.box_mm) <= _area(b.box_mm) else (b, a)
        share = ia / max(_area(small.box_mm), 1e-9)
        if share >= CONTAIN_FRAC:
            rels.append({"kind": "contains", "outer": big.id, "inner": small.id})
            if small.type == "text" and big.type != "text" and (big.appearance.get("fill") or {}).get("type") not in (None, "none"):
                top_is_text = small.z_order > big.z_order
                if top_is_text:
                    rels.append({"kind": "text_on", "text": small.id, "surface": big.id})
        elif share >= OVERLAP_MIN_FRAC:
            rels.append({"kind": "overlaps", "a": a.id, "b": b.id, "share_of_smaller": round(share, 3)})
    # alignment groups (edges / centres) among foreground objects
    fg = [o for o in tops if o.role not in ("bg", "frame") and not _spans_page(o, pw, ph)]
    for key, fn, tol in (("left", lambda o: o.box_mm["x"], tol_x), ("right", lambda o: o.box_mm["x"] + o.box_mm["width"], tol_x),
                         ("center_x", lambda o: o.box_mm["x"] + o.box_mm["width"] / 2, tol_x),
                         ("bottom", lambda o: o.box_mm["y"], tol_y), ("top", lambda o: o.box_mm["y"] + o.box_mm["height"], tol_y),
                         ("center_y", lambda o: o.box_mm["y"] + o.box_mm["height"] / 2, tol_y)):
        for grp in _tolerance_groups(fg, fn, tol):
            rels.append({"kind": "aligned", "edge": key, "ids": [o.id for o in grp]})
    # equal spacing along each axis for runs of 3+
    for axis in ("x", "y"):
        rels.extend(_equal_gap_runs(fg, axis, (EQUAL_GAP_TOL_FRAC * (pw if axis == "x" else ph))))
    # repeated elements
    for grp in _repeat_groups(fg):
        rels.append({"kind": "repeat", "ids": [o.id for o in grp]})
    model.relations = rels


def _tolerance_groups(objs: list[ModelObject], fn, tol: float) -> list[list[ModelObject]]:
    vals = sorted(((fn(o), o) for o in objs), key=lambda t: t[0])
    groups: list[list[ModelObject]] = []
    cur: list[tuple] = []
    for v, o in vals:
        if cur and v - cur[0][0] > tol:
            if len(cur) >= 2:
                groups.append([x[1] for x in cur])
            cur = []
        cur.append((v, o))
    if len(cur) >= 2:
        groups.append([x[1] for x in cur])
    return groups


def _equal_gap_runs(objs: list[ModelObject], axis: str, tol: float) -> list[dict]:
    if len(objs) < 3:
        return []
    lo = (lambda o: o.box_mm["x"]) if axis == "x" else (lambda o: o.box_mm["y"])
    size = (lambda o: o.box_mm["width"]) if axis == "x" else (lambda o: o.box_mm["height"])
    s = sorted(objs, key=lo)
    out, run, gaps = [], [s[0]], []
    for prev, nxt in zip(s, s[1:]):
        gap = lo(nxt) - (lo(prev) + size(prev))
        if gap < 0:
            run, gaps = [nxt], []
            continue
        if gaps and abs(gap - gaps[-1]) > tol:
            if len(run) >= 3:
                out.append({"kind": "equal_gap", "axis": axis, "ids": [o.id for o in run], "gap_mm": round(sum(gaps) / len(gaps), 2)})
            run, gaps = [prev], []
        run.append(nxt)
        gaps.append(gap)
    if len(run) >= 3 and len(gaps) >= 2:
        out.append({"kind": "equal_gap", "axis": axis, "ids": [o.id for o in run], "gap_mm": round(sum(gaps) / len(gaps), 2)})
    return out


def _repeat_groups(objs: list[ModelObject]) -> list[list[ModelObject]]:
    groups: list[list[ModelObject]] = []
    for o in objs:
        if o.type == "text" or o.box_mm["width"] <= 0 or o.box_mm["height"] <= 0:
            continue
        for g in groups:
            r = g[0]
            if (r.type == o.type and r.n_descendants == o.n_descendants
                    and abs(r.box_mm["width"] / o.box_mm["width"] - 1) <= REPEAT_SIZE_TOL
                    and abs(r.box_mm["height"] / o.box_mm["height"] - 1) <= REPEAT_SIZE_TOL):
                g.append(o)
                break
        else:
            groups.append([o])
    return [g for g in groups if len(g) >= 2 and (g[0].n_descendants > 0 or g[0].type in ("bitmap", "group"))]


def _modes(model: MasterModel) -> None:
    """A suggested behaviour per top-level object, with its confidence and the reason. Tags (`bg`, `frame`, `fixed`, `shopname`) are
    decided; geometry is a judgement (lower confidence)."""
    pw, ph = model.page_w, model.page_h
    repeated = {i for r in model.relations if r["kind"] == "repeat" for i in r["ids"]}
    for o in model.top_level():
        b = o.box_mm
        tagged = o.name.strip().lower().startswith(("bg", "frame", "fixed", "shopname", "text", "logo"))
        near = {"left": b["x"] <= EDGE_TOUCH_FRAC * pw, "right": pw - (b["x"] + b["width"]) <= EDGE_TOUCH_FRAC * pw,
                "bottom": b["y"] <= EDGE_TOUCH_FRAC * ph, "top": ph - (b["y"] + b["height"]) <= EDGE_TOUCH_FRAC * ph}
        if o.role == "bg":
            o.adaptation_mode, o.mode_confidence = STRETCH, 0.95 if tagged else 0.85
            o.mode_reason = "covers the page" if not tagged else "tagged bg"
            o.constraints = {"fill": "page"}
        elif o.role == "frame":
            o.adaptation_mode, o.mode_confidence, o.mode_reason = ANCHOR, 0.95, "tagged frame: keeps its margins to the page edges"
            o.constraints = {"anchor": "margins"}
        elif o.role == "fixed":
            o.adaptation_mode, o.mode_confidence, o.mode_reason = FIXED, 0.95, "tagged fixed: keeps its size"
        elif o.role == "shopname" or o.type == "text":
            o.adaptation_mode, o.mode_confidence = TEXT_FIT, 0.95 if o.role == "shopname" else 0.8
            o.mode_reason = "shop-name text (fitted to its slot)" if o.role == "shopname" else "text keeps its aspect and is fitted"
        elif o.type != "group" and o.kind == "shape" and _spans_page(o, pw, ph) and (b["width"] >= layout.FULL_SPAN_FRAC * pw) != (b["height"] >= layout.FULL_SPAN_FRAC * ph):
            axis = "x" if b["width"] >= layout.FULL_SPAN_FRAC * pw else "y"
            o.adaptation_mode, o.mode_confidence = STRETCH, 0.7
            o.mode_reason = f"spans the page along {axis}: a rule or band that follows the page"
            o.constraints = {"stretch_axis": axis}
        elif o.id in repeated:
            o.adaptation_mode, o.mode_confidence, o.mode_reason = TILE, 0.55, "one of several repeated equal elements"
        elif any(near.values()) and b["width"] * b["height"] < 0.1 * pw * ph:
            sides = [k for k, v in near.items() if v]
            o.adaptation_mode, o.mode_confidence = ANCHOR, 0.65
            o.mode_reason = f"small element at the {'/'.join(sides)} edge: keeps its distance to it"
            o.constraints = {"anchor": sides}
        else:
            o.adaptation_mode, o.mode_confidence, o.mode_reason = UNIFORM_SCALE, 0.7, "artwork keeps its proportions"
