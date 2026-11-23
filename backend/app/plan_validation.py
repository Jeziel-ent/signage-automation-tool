"""Phase 5b: validation of an adaptation plan - structured issues, no silent fixes.

Every rule returns `Issue`s (severity error | warning | info) instead of changing the plan: a plan with an `error` is not applied
(the legacy layout runs instead). Not every overlap is wrong: pairs that already overlapped in the master (text on its background, a
logo inside a card - the model's `contains` / `overlaps` / `text_on` relations) are intentional and never flagged.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from . import layout, master_model as mm

ERROR, WARNING, INFO = "error", "warning", "info"

OUT_OF_PAGE_WARN = 0.002          # share of the page an object may stick out before a warning
OUT_OF_PAGE_ERROR = 0.05          # ... and before an error (an object far off the board)
DISTORTION_TOL = 0.01             # aspect change of a non-stretching object beyond 1% = distortion
NEW_OVERLAP_WARN = 0.10           # share of the smaller box that now overlaps an object it did not touch in the master
NEW_OVERLAP_ERROR = 0.40
ALIGN_DRIFT_FRAC = 0.01           # aligned objects drifting apart by more than 1% of the page dimension
COVER_TOL = 0.005
MAX_PAIRS = 400
REFLOW_STRICT_STRETCH = 1.05      # text and bitmaps in a reflow: effectively no stretch
REFLOW_GROUP_STRETCH = 1.25       # vector groups / shapes in a reflow


@dataclass
class Issue:
    severity: str
    rule: str
    ids: list[str]
    message: str
    suggestion: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _bad(v) -> bool:
    return not isinstance(v, (int, float)) or math.isnan(v) or math.isinf(v)


def _inter(a: dict, b: dict) -> float:
    ox = max(0.0, min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"]))
    oy = max(0.0, min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"]))
    return ox * oy


def validate_plan(model: mm.MasterModel, plan) -> list[Issue]:
    """`plan` = adaptation_plan.AdaptationPlan. Returns every issue found (empty = clean)."""
    issues: list[Issue] = []
    tw, th = plan.target["width_mm"], plan.target["height_mm"]
    src = {o.id: o for o in model.top_level()}
    boxes = {p.id: p.box for p in plan.objects}

    # 1. geometry sanity
    for p in plan.objects:
        b = p.box
        if any(_bad(b.get(k)) for k in ("x", "y", "width", "height")):
            issues.append(Issue(ERROR, "invalid_coordinates", [p.id], "the planned box has a non-finite value", "use the legacy layout"))
        elif b["width"] < 0 or b["height"] < 0:
            issues.append(Issue(ERROR, "negative_size", [p.id], "the planned size is negative", "use the legacy layout"))
    if any(i.severity == ERROR for i in issues):
        return issues

    # 2. objects off the board
    for p in plan.objects:
        o = src.get(p.id)
        if o is None or (o.role == "bg" and p.mode in (mm.STRETCH, mm.REFLOW)):     # a cover-fitted background overflows by design
            continue
        b = p.box
        out = max(-b["x"], -b["y"], b["x"] + b["width"] - tw, b["y"] + b["height"] - th, 0.0)
        frac = out / max(min(tw, th), 1e-9)
        if frac > OUT_OF_PAGE_ERROR:
            issues.append(Issue(ERROR, "out_of_page", [p.id], f"{o.name or p.id} lies {out:.0f} mm outside the board", "reposition inside the page"))
        elif frac > OUT_OF_PAGE_WARN:
            issues.append(Issue(WARNING, "out_of_page", [p.id], f"{o.name or p.id} sticks out {out:.1f} mm", "nudge inside the safe area"))

    # 3. distortion of objects that must keep their proportions
    for p in plan.objects:
        o = src.get(p.id)
        if o is None or p.mode in (mm.STRETCH, mm.REFLOW) and o.role == "bg":
            continue
        if p.mode == mm.REFLOW:
            sb, b = o.box_mm, p.box
            if sb["width"] > 0 and sb["height"] > 0 and b["width"] > 0 and b["height"] > 0:
                change = (b["width"] / b["height"]) / (sb["width"] / sb["height"])
                stretch = max(change, 1 / change)
                # found by the offline evaluation (agarpathi 36x48 -> 2.5x wide): the editor's zone logic may stretch a text line / logo up to
                # its own 2.0 cap, which put a 37% non-uniform stretch on a text object the legacy path had left undistorted. Lettering and
                # photos must not be stretched at all; vector groups only a little; anything more is a failed plan (legacy layout runs).
                limit = REFLOW_STRICT_STRETCH if (o.type in ("text", "bitmap")) else REFLOW_GROUP_STRETCH
                if stretch > limit:
                    issues.append(Issue(ERROR, "distortion", [p.id], f"{o.name or o.type} {p.id} would be stretched {stretch:.2f}x by the reflow "
                                                                        f"(limit {limit:.2f}x for a {o.type})", "scale it uniformly or use the legacy layout"))
                elif stretch > 1 + DISTORTION_TOL:
                    issues.append(Issue(WARNING, "distortion", [p.id], f"{o.name or p.id} is stretched {stretch:.2f}x by the reflow", "check the lettering / logo"))
            continue
        if p.mode in (mm.UNIFORM_SCALE, mm.TEXT_FIT, mm.ANCHOR, mm.FIXED, mm.TILE):
            sb, b = o.box_mm, p.box
            if sb["width"] > 0 and sb["height"] > 0 and b["width"] > 0 and b["height"] > 0:
                change = (b["width"] / b["height"]) / (sb["width"] / sb["height"])
                if abs(change - 1) > DISTORTION_TOL and not p.constraints.get("unit_member"):
                    issues.append(Issue(ERROR, "distortion", [p.id], f"{o.name or p.id} changes aspect by {change:.2f}x but its mode is {p.mode}",
                                        "scale it uniformly"))

    # 4. text legibility
    for p in plan.objects:
        o = src.get(p.id)
        if o is not None and o.type == "text" and (o.text or {}).get("size_pt") and o.box_mm["height"] > 0:
            k = p.box["height"] / o.box_mm["height"]
            if o.text["size_pt"] * k < layout.MIN_TEXT_PT and p.mode != mm.TEXT_FIT:
                issues.append(Issue(WARNING, "text_legibility", [p.id], f"text would be about {o.text['size_pt'] * k:.0f} pt (< {layout.MIN_TEXT_PT:.0f} pt)",
                                    "give the text a larger region"))

    # 5. new, unintentional overlaps
    intended: set[frozenset] = set()
    for r in model.relations:
        if r["kind"] == "contains":
            intended.add(frozenset((r["outer"], r["inner"])))
        elif r["kind"] == "overlaps":
            intended.add(frozenset((r["a"], r["b"])))
        elif r["kind"] == "text_on":
            intended.add(frozenset((r["text"], r["surface"])))
    unit_of = {m: u["id"] for u in model.units for m in u["members"]}
    fg = [p for p in plan.objects if src.get(p.id) is not None and src[p.id].role not in ("bg", "frame") and src[p.id].visible]
    if len(fg) <= MAX_PAIRS:
        for i, a in enumerate(fg):
            for b in fg[i + 1:]:
                if frozenset((a.id, b.id)) in intended or (a.id in unit_of and unit_of.get(a.id) == unit_of.get(b.id)):
                    continue
                ia = _inter(a.box, b.box)
                if ia <= 0:
                    continue
                share = ia / max(min(a.box["width"] * a.box["height"], b.box["width"] * b.box["height"]), 1e-9)
                if share >= NEW_OVERLAP_WARN:
                    sev = ERROR if share >= NEW_OVERLAP_ERROR else WARNING
                    issues.append(Issue(sev, "new_overlap", [a.id, b.id], f"{a.id} and {b.id} overlap by {share:.0%} of the smaller one; they did not overlap in the master",
                                        "move one away or use the legacy layout"))
    else:
        issues.append(Issue(INFO, "overlap_check_skipped", [], f"{len(fg)} foreground objects: pairwise overlap check skipped"))

    # 6. backgrounds still cover the board
    for p in plan.objects:
        o = src.get(p.id)
        if o is not None and o.role == "bg" and p.mode == mm.STRETCH:
            b = p.box
            if b["x"] > tw * COVER_TOL or b["y"] > th * COVER_TOL or b["x"] + b["width"] < tw * (1 - COVER_TOL) or b["y"] + b["height"] < th * (1 - COVER_TOL):
                issues.append(Issue(ERROR, "background_coverage", [p.id], "a background no longer covers the whole board", "stretch it to the page"))

    # 7. alignment and equal spacing kept (continuity of repeated / aligned elements)
    def edge(box, key):
        return {"left": box["x"], "right": box["x"] + box["width"], "center_x": box["x"] + box["width"] / 2,
                "bottom": box["y"], "top": box["y"] + box["height"], "center_y": box["y"] + box["height"] / 2}[key]

    for r in model.relations:
        if r["kind"] == "aligned" and all(i in boxes for i in r["ids"]):
            vals = [edge(boxes[i], r["edge"]) for i in r["ids"]]
            dim = tw if r["edge"] in ("left", "right", "center_x") else th
            if max(vals) - min(vals) > ALIGN_DRIFT_FRAC * dim and plan.strategy != "reflow":
                issues.append(Issue(INFO, "alignment_lost", list(r["ids"]), f"objects aligned on their {r['edge']} no longer line up"))
        elif r["kind"] == "equal_gap" and all(i in boxes for i in r["ids"]) and plan.strategy != "reflow":
            ax = r["axis"]
            lo = (lambda b: b["x"]) if ax == "x" else (lambda b: b["y"])
            sz = (lambda b: b["width"]) if ax == "x" else (lambda b: b["height"])
            ordered = sorted((boxes[i] for i in r["ids"]), key=lo)
            gaps = [lo(n) - (lo(p) + sz(p)) for p, n in zip(ordered, ordered[1:])]
            dim = tw if ax == "x" else th
            if gaps and max(gaps) - min(gaps) > ALIGN_DRIFT_FRAC * dim:
                issues.append(Issue(WARNING, "panel_continuity", list(r["ids"]), f"a run of equally spaced objects along {ax} lost its even spacing",
                                    "keep them as one rigid group"))

    # 8. orientation consistency
    if plan.source["orientation"] != plan.target["orientation"] and plan.strategy not in ("reflow", "declined"):
        issues.append(Issue(WARNING, "orientation_consistency", [], "the board changes orientation but the plan did not reflow",
                            "use the other-orientation master or the reflow strategy"))
    return issues


def has_errors(issues: list[Issue]) -> bool:
    return any(i.severity == ERROR for i in issues)
