"""Phase 1 metric suite: scores one generated board ("ours") against the
designer's own real file ("real") on four independent axes, plus layout
checks that need no ground truth at all. Pure Python + PIL/numpy/scipy -
no CorelDRAW here; callers must already have the PNGs and shape dumps
(cache_real_renders.py / cache_ours_dumps.py / validate_all.py produce
them over COM - this module never touches COM itself).

(a) Visual similarity - three independent, cheap signals on the same
    greyscale, common-width downscale of both PNGs:
      - SSIM (image_compare.py, pre-existing)
      - a perceptual hash (dHash, gradient-based) compared by Hamming
        distance
      - an edge-map correlation (Sobel magnitude, normalized dot product)
    None of these alone is reliable - SSIM is fooled by a uniform colour
    shift, phash is coarse, edges ignore colour/fill entirely - reporting
    all three plus a weighted combination is more honest than picking one
    metric and calling it done.

(b) Cluster-level comparison - reuses derive_brand_rules.cluster() (bbox
    -proximity union-find) to collapse each file's own non-bg/frame leaf
    shapes into logical clusters (so ~130 loose curves become 2-3
    comparable blobs instead of shattering an object-level diff, the same
    reasoning as validate_all.py's leaf-flattening), then greedy-matches
    ours vs real clusters by role + nearest centre + similar size (same
    discipline as validate_all.py._match, one level up in granularity).

(c) Counts - shape/text/cluster counts ours vs real.

(d) No-ground-truth layout checks, from ours' own shape dump alone:
    nothing outside the page, no two clusters overlapping more than a
    threshold, minimum margin from the page edge, no text shape below a
    legibility floor (point size, as CorelDRAW itself reports it post
    -layout).

Every threshold lives in metrics_config.json (load_config()) - see that
file's _note and CLAUDE.md's "rules of engagement": calibrate by eye, never
by tuning against the pass rate.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.layout import Obj  # noqa: E402
from tools.derive_brand_rules import cluster as _cluster_objs, bbox_of  # noqa: E402
from tools.image_compare import load_gray_downscaled, ssim_between_files  # noqa: E402
from tools.validate_all import _bucket_role, _flatten_leaves, _size_similar  # noqa: E402

CONFIG_PATH = Path(__file__).with_name("metrics_config.json")


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- (a) visual

def _dhash(img: Image.Image, hash_size: int = 8) -> int:
    """Gradient-based perceptual hash: resize to (n+1)x n greyscale, hash
    bit i = "is this pixel brighter than the one to its left". Robust to
    resizing/compression, unlike a byte-exact pixel hash; coarser than SSIM,
    which is exactly why it's a useful second signal rather than a
    replacement for it.
    """
    small = img.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
    pixels = np.asarray(small, dtype=np.int16)
    diff = pixels[:, 1:] > pixels[:, :-1]
    bits = 0
    for b in diff.flatten():
        bits = (bits << 1) | int(b)
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def phash_similarity(path_a, path_b, hash_size: int = 8) -> dict:
    ha, hb = _dhash(Image.open(path_a), hash_size), _dhash(Image.open(path_b), hash_size)
    dist = _hamming(ha, hb)
    total_bits = hash_size * hash_size
    return {"hamming_distance": dist, "total_bits": total_bits, "similarity": 1 - dist / total_bits}


def _edge_map(gray: np.ndarray) -> np.ndarray:
    sx = ndimage.sobel(gray, axis=0)
    sy = ndimage.sobel(gray, axis=1)
    mag = np.hypot(sx, sy)
    peak = mag.max()
    return mag / peak if peak > 0 else mag


def edge_similarity(path_a, path_b, width: int = 300) -> float:
    """Normalized cross-correlation of Sobel edge-magnitude maps - high
    when the same lines/silhouettes fall in the same places, independent of
    colour or fill, which SSIM and phash both partly depend on.
    """
    a = load_gray_downscaled(path_a, width)
    b = load_gray_downscaled(path_b, width)
    h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
    ea, eb = _edge_map(a[:h, :w]), _edge_map(b[:h, :w])
    num = float(np.sum(ea * eb))
    den = float(np.sqrt(np.sum(ea ** 2)) * np.sqrt(np.sum(eb ** 2)))
    if den == 0:
        return 1.0 if num == 0 else 0.0
    return max(0.0, min(1.0, num / den))


def visual_similarity(ours_png, real_png, config: dict | None = None) -> dict:
    config = config or load_config()
    vc = config["visual"]
    s = ssim_between_files(ours_png, real_png)
    p = phash_similarity(ours_png, real_png)
    e = edge_similarity(ours_png, real_png)
    combined = vc["ssim_weight"] * s + vc["phash_weight"] * p["similarity"] + vc["edge_weight"] * e
    return {
        "ssim": s, "phash_similarity": p["similarity"], "phash_hamming": p["hamming_distance"],
        "edge_similarity": e, "combined": combined, "pass": combined >= vc["pass_threshold"],
    }


# ------------------------------------------------------------- (b) clusters

def _bbox_clusters(shapes: list[dict], margin: float) -> list[dict]:
    """Cluster every non-bg/frame leaf shape by bbox proximity. bg/frame are
    excluded because they typically span (or nearly span) the whole page and
    would bbox-overlap with everything else, merging every real cluster into
    one - they're compared directly (there's exactly one of each, almost
    always) rather than through clustering.
    """
    indexed = [(i, s) for i, s in enumerate(shapes) if s["role"] not in ("bg", "frame")]
    objs = [Obj(str(i), s.get("name", ""), s.get("type", "shape"), s["x"], s["y"], s["w"], s["h"], s.get("text"))
            for i, s in indexed]
    role_by_id = {str(i): s["role"] for i, s in indexed}
    out = []
    for c in _cluster_objs(objs, margin):
        x, y, w, h = bbox_of(c)
        roles = [role_by_id[o.id] for o in c]
        dominant = max(set(roles), key=roles.count)
        out.append({"bbox": {"x": x, "y": y, "w": w, "h": h}, "shape_count": len(c), "role": dominant})
    return out


def _match_clusters(ours: list[dict], real: list[dict], factor: float):
    def size_similar(a, b):
        return _size_similar(a["bbox"], b["bbox"], factor)

    remaining = list(range(len(real)))
    pairs = []
    for oc in ours:
        ocx = oc["bbox"]["x"] + oc["bbox"]["w"] / 2
        ocy = oc["bbox"]["y"] + oc["bbox"]["h"] / 2
        candidates = [j for j in remaining if real[j]["role"] == oc["role"] and size_similar(oc, real[j])]
        if not candidates:
            continue
        best = min(candidates, key=lambda j: (real[j]["bbox"]["x"] + real[j]["bbox"]["w"] / 2 - ocx) ** 2
                                             + (real[j]["bbox"]["y"] + real[j]["bbox"]["h"] / 2 - ocy) ** 2)
        pairs.append((oc, real[best]))
        remaining.remove(best)
    unmatched_ours = [c for c in ours if not any(c is p[0] for p in pairs)]
    unmatched_real = [real[j] for j in remaining]
    return pairs, unmatched_ours, unmatched_real


def cluster_compare(ours_shapes: list[dict], real_shapes: list[dict], page_w: float, page_h: float,
                     config: dict | None = None) -> dict:
    config = config or load_config()
    cc = config["cluster"]
    ours_clusters = _bbox_clusters(ours_shapes, cc["cluster_margin_mm"])
    real_clusters = _bbox_clusters(real_shapes, cc["cluster_margin_mm"])
    pairs, unmatched_ours, unmatched_real = _match_clusters(ours_clusters, real_clusters, cc["size_similar_factor"])

    diffs = []
    for o, r in pairs:
        ob, rb = o["bbox"], r["bbox"]
        dx = abs(ob["x"] - rb["x"]) / page_w * 100
        dy = abs(ob["y"] - rb["y"]) / page_h * 100
        dw = abs(ob["w"] - rb["w"]) / page_w * 100
        dh = abs(ob["h"] - rb["h"]) / page_h * 100
        diffs.append({"role": o["role"], "dx_pct": dx, "dy_pct": dy, "dw_pct": dw, "dh_pct": dh,
                      "max_pct": max(dx, dy, dw, dh)})

    return {
        "ours_clusters": len(ours_clusters), "real_clusters": len(real_clusters), "matched": len(pairs),
        "unmatched_ours": len(unmatched_ours), "unmatched_real": len(unmatched_real),
        "diffs": diffs,
        "max_diff_pct": max((d["max_pct"] for d in diffs), default=None),
    }


# --------------------------------------------- (b2) geometric accuracy, in mm

def geometric_accuracy(ours_shapes: list[dict], real_shapes: list[dict], config: dict | None = None) -> dict:
    """Step 2: per-cluster position/size error IN MILLIMETRES (not % of page,
    unlike `cluster_compare` - a mm number is comparable across boards of
    different target sizes, which a %-of-page number is not), using the same
    cluster matching as `cluster_compare` (so a cluster either has both a %
    and an mm figure, from one matching pass, or neither).

    `position_error_mm` = Euclidean distance between the matched clusters'
    centres. `size_error_mm` = Euclidean distance between their (w, h) pairs.
    `area_matched_pct[t]` = % of the real file's total non-bg/frame cluster
    area that belongs to a cluster matched with both errors <= t mm - see
    metrics_config.json's `geometric` note for why this is area-weighted
    rather than a plain count.
    """
    config = config or load_config()
    cc, gc = config["cluster"], config.get("geometric", {"area_match_tolerances_mm": [2.0, 5.0, 10.0]})
    ours_clusters = _bbox_clusters(ours_shapes, cc["cluster_margin_mm"])
    real_clusters = _bbox_clusters(real_shapes, cc["cluster_margin_mm"])
    pairs, unmatched_ours, unmatched_real = _match_clusters(ours_clusters, real_clusters, cc["size_similar_factor"])

    per_cluster = []
    for o, r in pairs:
        ob, rb = o["bbox"], r["bbox"]
        ocx, ocy = ob["x"] + ob["w"] / 2, ob["y"] + ob["h"] / 2
        rcx, rcy = rb["x"] + rb["w"] / 2, rb["y"] + rb["h"] / 2
        position_error_mm = math.hypot(ocx - rcx, ocy - rcy)
        size_error_mm = math.hypot(ob["w"] - rb["w"], ob["h"] - rb["h"])
        per_cluster.append({
            "role": o["role"],
            "dx_mm": ob["x"] - rb["x"], "dy_mm": ob["y"] - rb["y"],
            "dw_mm": ob["w"] - rb["w"], "dh_mm": ob["h"] - rb["h"],
            "position_error_mm": position_error_mm, "size_error_mm": size_error_mm,
            "real_area_mm2": rb["w"] * rb["h"],
        })

    total_real_area = sum(r["bbox"]["w"] * r["bbox"]["h"] for r in real_clusters)
    area_matched_pct = []
    for t in gc["area_match_tolerances_mm"]:
        matched_area = sum(c["real_area_mm2"] for c in per_cluster
                          if max(c["position_error_mm"], c["size_error_mm"]) <= t)
        area_matched_pct.append({
            "tolerance_mm": t,
            "pct": (matched_area / total_real_area * 100) if total_real_area > 0 else None,
        })

    pos_errs = [c["position_error_mm"] for c in per_cluster]
    size_errs = [c["size_error_mm"] for c in per_cluster]
    return {
        "per_cluster": per_cluster,
        "matched": len(pairs), "unmatched_ours": len(unmatched_ours), "unmatched_real": len(unmatched_real),
        "position_error_mm": {"max": max(pos_errs) if pos_errs else None,
                              "mean": statistics.mean(pos_errs) if pos_errs else None},
        "size_error_mm": {"max": max(size_errs) if size_errs else None,
                          "mean": statistics.mean(size_errs) if size_errs else None},
        "area_matched_pct": area_matched_pct,
        "total_real_area_mm2": total_real_area,
    }


# ---------------------------------------------------------------- (c) counts

def counts_compare(ours_shapes: list[dict], real_shapes: list[dict]) -> dict:
    def n_text(shapes):
        return sum(1 for s in shapes if s["role"] in ("text", "shopname"))

    return {
        "ours_shapes": len(ours_shapes), "real_shapes": len(real_shapes),
        "ours_text": n_text(ours_shapes), "real_text": n_text(real_shapes),
    }


# ----------------------------------------------------- (d) layout checks

def layout_checks(ours_shapes: list[dict], page_w: float, page_h: float, config: dict | None = None) -> list[dict]:
    """Checks that need no designer ground truth - can run on any generated
    board, real or synthetic, at deployment time (see Phase 6's "master
    check" / warnings idea).
    """
    config = config or load_config()
    lc, cc = config["layout"], config["cluster"]
    tol = lc["page_tolerance_mm"]
    results = []

    outside = [s.get("name") or s["role"] for s in ours_shapes
               if s["x"] < -tol or s["y"] < -tol or s["x"] + s["w"] > page_w + tol or s["y"] + s["h"] > page_h + tol]
    results.append({
        "check": "within_page", "status": "pass" if not outside else "fail",
        "detail": f"{len(outside)} shape(s) outside page bounds" + (f": {outside[:5]}" if outside else ""),
    })

    clusters = _bbox_clusters(ours_shapes, cc["cluster_margin_mm"])
    overlap_pairs = 0
    for i in range(len(clusters)):
        for j in range(i + 1, len(clusters)):
            a, b = clusters[i]["bbox"], clusters[j]["bbox"]
            ox = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
            oy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
            smaller_area = min(a["w"] * a["h"], b["w"] * b["h"])
            if smaller_area > 0 and (ox * oy) / smaller_area > lc["max_overlap_ratio"]:
                overlap_pairs += 1
    results.append({
        "check": "no_cluster_overlap", "status": "pass" if overlap_pairs == 0 else "fail",
        "detail": f"{overlap_pairs} cluster pair(s) overlap more than {lc['max_overlap_ratio']:.0%} of the smaller one's area",
    })

    def _is_full_bleed(b: dict) -> bool:
        spans_width = b["w"] >= page_w - tol
        spans_height = b["h"] >= page_h - tol
        area_ratio = (b["w"] * b["h"]) / (page_w * page_h) if page_w > 0 and page_h > 0 else 0
        return spans_width or spans_height or area_ratio >= lc["full_bleed_area_ratio"]

    margin_offenders = []
    for c in clusters:
        b = c["bbox"]
        if _is_full_bleed(b):
            continue  # a deliberate edge-to-edge element (accent strip, background), not a misplaced object
        m = min(b["x"], b["y"], page_w - (b["x"] + b["w"]), page_h - (b["y"] + b["h"]))
        if m < lc["min_margin_mm"]:
            margin_offenders.append(round(m, 1))
    results.append({
        "check": "min_margin", "status": "pass" if not margin_offenders else "warn",
        "detail": f"{len(margin_offenders)} cluster(s) closer than {lc['min_margin_mm']}mm to a page edge"
                  + (f": {margin_offenders[:5]}mm" if margin_offenders else ""),
    })

    small_text = [round(s["font_size"], 1) for s in ours_shapes
                  if s["role"] in ("text", "shopname") and s.get("font_size") and s["font_size"] < lc["min_text_pt"]]
    results.append({
        "check": "text_legibility", "status": "pass" if not small_text else "warn",
        "detail": f"{len(small_text)} text shape(s) below {lc['min_text_pt']}pt"
                  + (f": {small_text}" if small_text else ""),
    })

    # Text must never overlap at all (unlike general clusters, where some
    # overlap tolerance is normal for adjacent decorative elements) - a
    # long replacement shop name colliding with the phone/GST line is
    # exactly the failure mode CorelEngine._fit_text (shrink/wrap) exists to
    # avoid at generation time; this is the check that catches it if it
    # still happens. Zero-tolerance: any bbox overlap at all is a fail.
    #
    # Deliberately NOT built from _bbox_clusters: two text shapes close
    # enough to overlap are, by definition, close enough to have already
    # been union-find-merged into one cluster by _bbox_clusters (its
    # proximity margin is far more generous than "touching"), which would
    # hide exactly the overlap this check exists to catch. Checked directly
    # on leaf text shapes instead - real masters use one literal CorelDRAW
    # text shape per label (shop name, phone/GST), not fragmented curves
    # the way logos are, so no clustering step is needed here.
    text_objs = [s for s in ours_shapes if s["role"] in ("text", "shopname")]
    overlapping_text = 0
    for i in range(len(text_objs)):
        for j in range(i + 1, len(text_objs)):
            a, b = text_objs[i], text_objs[j]
            ox = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
            oy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
            if ox > 0 and oy > 0:
                overlapping_text += 1
    results.append({
        "check": "text_overlap", "status": "pass" if overlapping_text == 0 else "fail",
        "detail": f"{overlapping_text} pair(s) of text/shopname shapes overlap",
    })

    # The shop name belongs in the same bottom text bar as the other fixed
    # text (footer, phone/GST) - confirmed on every real dalmia board,
    # tiled or not. Before this check existed, a wide/tiled board's
    # gap-centring shopname placement put it near the page's vertical
    # centre instead - visibly wrong on inspection, but nothing flagged it.
    # "Bottom bar" is defined from the OTHER text shapes' own vertical
    # span, not a fixed page fraction, since it should track wherever that
    # band actually is on this specific master.
    text_only = [s for s in ours_shapes if s["role"] == "text"]
    shopname_only = [s for s in ours_shapes if s["role"] == "shopname"]
    if not text_only or not shopname_only:
        results.append({
            "check": "shopname_in_bottom_bar", "status": "pass",
            "detail": "no shopname or no other fixed text to compare its position against",
        })
    else:
        bar_y0 = min(s["y"] for s in text_only)
        bar_y1 = max(s["y"] + s["h"] for s in text_only)
        margin = lc["bottom_bar_margin_mm"]
        outside = [s for s in shopname_only if not (bar_y0 - margin <= s["y"] + s["h"] / 2 <= bar_y1 + margin)]
        results.append({
            "check": "shopname_in_bottom_bar", "status": "pass" if not outside else "fail",
            "detail": f"{len(outside)} shopname shape(s) outside the bottom text bar "
                      f"(y {bar_y0:.0f}-{bar_y1:.0f}mm +/-{margin:.0f}mm)",
        })

    return results


# --------------------------------------------------------------- top-level

def score_board(ours_png, real_png, ours_dump: dict, real_dump: dict, page_w: float, page_h: float,
                 shopname_hints: list[str], config: dict | None = None) -> dict:
    """Run all four metric categories for one board. Dumps are raw
    dump_objects.py output (full shape list, page_mm); this buckets roles
    and flattens groups itself so callers can pass dumps straight through.
    """
    config = config or load_config()
    ours_shapes = _bucket_role(_flatten_leaves(ours_dump), page_w, page_h, shopname_hints)
    real_shapes = _bucket_role(_flatten_leaves(real_dump), page_w, page_h, shopname_hints)

    return {
        "visual": visual_similarity(ours_png, real_png, config),
        "clusters": cluster_compare(ours_shapes, real_shapes, page_w, page_h, config),
        "geometric": geometric_accuracy(ours_shapes, real_shapes, config),
        "counts": counts_compare(ours_shapes, real_shapes),
        "layout_checks": layout_checks(ours_shapes, page_w, page_h, config),
    }
