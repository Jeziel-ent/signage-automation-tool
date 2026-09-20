"""Derive a per-brand tiling rule from the master + its real designer resizes.

Real masters have no CorelDRAW groups marking "this is one logo" (see
CLAUDE.md "Designer dataset analysis" - dalmia's two logos are ~130 raw
top-level curves). To know *which* sub-graphic the designer repeats when
tiling (dalmia: only the small "roof/foundation" triangle, not the whole
panel - confirmed by eye against signage_dataset/_previews), this:

1. Clusters the master's logo-role shapes into connected groups by
   bounding-box proximity (union-find: shapes whose boxes, expanded by a
   small margin, overlap are the same logo - curves making up one
   hand-drawn graphic touch or sit very close together; two separate
   logos don't).
2. For each known wide/tiled real file, independently clusters *its* own
   logo-role shapes the same way, then matches each real cluster to the
   master cluster whose bounding-box *size* (scaled by the non-tiled
   axis's fit ratio) it's closest to. Counting how many real clusters
   match each master cluster gives the repeat count for that file/aspect.
   This is size-based, not shape-count-based, because real files also
   weld/simplify curves (fewer shapes for the *same* graphic) independently
   of repetition - shape-count deltas alone were tried first and were too
   noisy to trust (see git history / session notes).
3. Writes backend/app/brand_rules/<brand>.json with each cluster's bbox
   (in the master's own mm coordinates - used to re-match a master's
   shapes to a cluster at layout time, since there's no stable id) and
   either "repeat": "never" or an aspect-ratio -> count table built from
   step 2's actual observations.

Offline - reads existing dumps from backend/dataset_analysis/dumps/,
doesn't touch CorelDRAW or signage_dataset/.

Usage:
    python derive_brand_rules.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.layout import Obj, detect_role, find_shopname_ids  # noqa: E402

DUMPS = ROOT / "dataset_analysis" / "dumps"
OUT = ROOT / "app" / "brand_rules"

MASTER_SHOPNAME_HINTS = ["SRI KAVI STEELS", "ஸ்ரீ கவி ஸ்டீல்ஸ்"]

# Wide/tall real files known (from the validation batch) to tile: file -> (target_w_mm, target_h_mm)
WIDE_FILES = {
    "dalmia_02_180x48.json": (4572.0, 1219.2),
    "dalmia_06_180x60.json": (4572.0, 1524.0),
    "dalmia_11_216x48.json": (5486.4, 1219.2),
    "dalmia_11_240x60.json": (6096.0, 1524.0),
}
MASTER_FILE = "dalmia_master_120x48.json"
MASTER_PAGE = (3048.0, 1219.2)
SIZE_MATCH_TOLERANCE = 0.35  # relative difference allowed on both w and h to call it a match


def _load(name: str) -> dict:
    return json.loads((DUMPS / name).read_text(encoding="utf-8"))


def _flattened_leaf_objs(dump: dict) -> list[Obj]:
    """All non-group shapes regardless of nesting - real files group their
    logos inconsistently (see CLAUDE.md), so top-level counts/positions
    alone aren't comparable across files; leaf shapes are (a group is just
    an organizational wrapper, it doesn't change what's actually drawn).
    """
    return [
        Obj(str(i), s["name"], s["type"], s["x"], s["y"], s["w"], s["h"], s.get("text"))
        for i, s in enumerate(dump["shapes"])
        if s["type"] != "group"
    ]


def _logo_objs(objs: list[Obj], page_w: float, page_h: float) -> list[Obj]:
    shopname_ids = find_shopname_ids(objs, *MASTER_SHOPNAME_HINTS)
    return [o for o in objs if o.id not in shopname_ids and detect_role(o, page_w, page_h) == "logo"]


def _expand(bbox, margin):
    x0, y0, x1, y1 = bbox
    return (x0 - margin, y0 - margin, x1 + margin, y1 + margin)


def _overlaps(a, b) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return ax0 <= bx1 and bx0 <= ax1 and ay0 <= by1 and by0 <= ay1


def cluster(objs: list[Obj], margin: float = 20.0) -> list[list[Obj]]:
    """Union-find on expanded-bbox overlap.

    margin was tuned by hand against the master's own known 2-3 logos
    (see derive_brand_rules output at several margins): 3mm split a small
    badge from the triangle graphic it's actually part of into its own
    tiny "cluster" (giving it a spurious independent repeat rule); 20mm
    merges it back in while keeping the triangle and the separate Dalmia
    wordmark apart; 50mm+ starts merging those two distinct logos into one.
    """
    n = len(objs)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    boxes = [_expand((o.x, o.y, o.x + o.w, o.y + o.h), margin) for o in objs]
    for i in range(n):
        for j in range(i + 1, n):
            if _overlaps(boxes[i], boxes[j]):
                union(i, j)

    groups: dict[int, list[Obj]] = {}
    for i, o in enumerate(objs):
        groups.setdefault(find(i), []).append(o)
    return sorted(groups.values(), key=len, reverse=True)


def bbox_of(objs: list[Obj]) -> tuple[float, float, float, float]:
    x0 = min(o.x for o in objs)
    y0 = min(o.y for o in objs)
    x1 = max(o.x + o.w for o in objs)
    y1 = max(o.y + o.h for o in objs)
    return x0, y0, x1 - x0, y1 - y0


def _size_close(w1, h1, w2, h2, tol=SIZE_MATCH_TOLERANCE) -> bool:
    if w2 <= 0 or h2 <= 0:
        return False
    return abs(w1 / w2 - 1) <= tol and abs(h1 / h2 - 1) <= tol


def main():
    master_dump = _load(MASTER_FILE)
    master_objs = _flattened_leaf_objs(master_dump)
    page_w, page_h = MASTER_PAGE
    logo_objs = _logo_objs(master_objs, page_w, page_h)
    print(f"master: {len(master_objs)} leaf objects, {len(logo_objs)} logo-role")

    m_clusters = cluster(logo_objs)
    # only clusters with >= 3 shapes are treated as real logos; smaller ones
    # are noise (stray decorative curves), left as ordinary "logo" objects
    m_clusters = [c for c in m_clusters if len(c) >= 3]
    m_boxes = [bbox_of(c) for c in m_clusters]
    print(f"master clusters: {[(len(c), round(b[2]), round(b[3])) for c, b in zip(m_clusters, m_boxes)]}")

    # All 4 wide files tile horizontally (axis="x" per _tile_plan), so the
    # non-tiled axis (height) scales by new_h/page_h - what an un-repeated
    # master cluster's size would become in the target, for comparison.
    findings = []
    for fname, (new_w, new_h) in WIDE_FILES.items():
        scale = new_h / page_h
        dump = _load(fname)
        objs = _flattened_leaf_objs(dump)
        real_logo_objs = _logo_objs(objs, new_w, new_h)
        r_clusters = [c for c in cluster(real_logo_objs) if len(c) >= 3]
        r_boxes = [bbox_of(c) for c in r_clusters]

        match_counts = [0] * len(m_clusters)
        unmatched = 0
        for rb in r_boxes:
            matched_any = False
            for mi, mb in enumerate(m_boxes):
                if _size_close(rb[2], rb[3], mb[2] * scale, mb[3] * scale):
                    match_counts[mi] += 1
                    matched_any = True
            if not matched_any:
                unmatched += 1

        aspect = round(new_w / new_h, 2)
        findings.append({"file": fname, "aspect": aspect, "match_counts": match_counts, "unmatched": unmatched})
        print(f"{fname}: aspect={aspect} real_clusters={len(r_clusters)} "
              f"match_counts(by master cluster)={match_counts} unmatched={unmatched}")

    # The master itself is a 5th, baseline data point: at its own aspect
    # ratio every cluster is present exactly once (that's what "master"
    # means). Without this point every cluster looks like a constant (e.g.
    # cluster 1 was 2,2,2,2 across the 4 wide files alone) when it's
    # actually "1 at the master's own aspect, 2 once wide enough" - the
    # interesting, real behaviour this rule exists to capture.
    master_aspect = round(page_w / page_h, 2)
    all_points = [(master_aspect, [1] * len(m_clusters))] + [
        (f["aspect"], f["match_counts"]) for f in findings
    ]

    print("\nper-cluster aspect -> count (including master baseline):")
    groups_out = []
    for mi, (c, b) in enumerate(zip(m_clusters, m_boxes)):
        table = sorted({(a, max(1, counts[mi])) for a, counts in all_points})
        varies = len({count for _, count in table}) > 1
        print(f"  cluster {mi}: {table} {'(varies -> by_aspect)' if varies else '(constant -> never)'}")

        entry = {
            "cluster_id": mi,
            "shape_count_in_master": len(c),
            "bbox_mm": {"x": b[0], "y": b[1], "w": b[2], "h": b[3]},
        }
        if varies:
            entry["repeat"] = "by_aspect"
            entry["repeat_table"] = [{"aspect": a, "count": n} for a, n in table]
            entry["repeat_table_note"] = (
                "count = how many copies of this cluster's bounding-box size were found (size-matched, "
                f"tolerance {SIZE_MATCH_TOLERANCE:.0%}) in the real designer file at that new_w/new_h "
                "aspect ratio, plus the master itself at its own aspect (always count=1). Derived from "
                "backend/dataset_analysis/dumps, not a formula - interpolate/extrapolate by nearest "
                "aspect at layout time."
            )
        else:
            entry["repeat"] = "never"
        groups_out.append(entry)

    rule = {
        "brand": "dalmia",
        "master_file": "02 - 120 X 48 Inch - 2 Nos Double Side GSB - SRI KAVI STEELS.cdr",
        "master_page_mm": {"w": page_w, "h": page_h},
        "derived_from": list(WIDE_FILES.keys()),
        "note": (
            "Derived from backend/tools/derive_brand_rules.py against real designer files, "
            "not hand-tuned. groups[].bbox_mm is in the MASTER's own mm coordinates and is "
            "used to re-match a master's top-level logo shapes to a group at layout time "
            "(nearest bbox centre) - there is no stable shape id/name in these untagged files."
        ),
        "groups": groups_out,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / "dalmia.json"
    out_path.write_text(json.dumps(rule, indent=2), encoding="utf-8")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
