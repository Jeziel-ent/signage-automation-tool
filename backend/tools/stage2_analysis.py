"""Stage 2 offline analyses (no CorelDRAW): typography, master selection (leave-one-out), composition relationships and wide boards.

Reads the Stage 1 evidence under backend/dataset_analysis/stage1 and writes to backend/dataset_analysis/stage2:
  typography_analysis.json, master_selection_evaluation.json, composition_relationships.json, wide_board_analysis.json

  python tools/stage2_analysis.py typography | master | composition | wide | all
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tools"))

import stage1_audit as s1  # noqa: E402

S1 = BACKEND / "dataset_analysis" / "stage1"
S2 = BACKEND / "dataset_analysis" / "stage2"
FT = 304.8


def write(name: str, data) -> None:
    S2.mkdir(parents=True, exist_ok=True)
    (S2 / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def mapping_rows() -> list[dict]:
    return json.loads((S1 / "shop_reference_mapping.json").read_text(encoding="utf-8"))["rows"]


def load_ex(group: str, p: Path):
    return s1.load_extraction(S1, group, p)


# ------------------------------------------------------------------------------------------------------------ master selection


def _lib_without(lib: dict, files: set[str]) -> dict:
    out = copy.copy(lib)
    out["boards"] = [b for b in lib["boards"] if b["file"] not in files]
    return out


def cmd_master(a):
    """Leave-one-out evaluation of the selection score against (a) the production selection and (b) the master each designer board was built from.
    NOTE the second is INFERRED (library signature match / top-level structure): no file records the designer's master, so nothing is 'confirmed'."""
    from app import example_layout as ex, master_selection as ms, orientation_adapter as oa

    lib = ex.library("hangyo")
    EVID = ms.load_evidence("hangyo")
    rows = mapping_rows()
    msum = {m["file"]: m for m in json.loads((S1 / "master_analysis_summary.json").read_text(encoding="utf-8"))}
    cands = [ms.MasterCandidate(f, m["page_mm"][0], m["page_mm"][1], m["orientation"]) for f, m in msum.items()]
    by_sno = defaultdict(list)
    for r in rows:
        by_sno[r["sno"]].append(r["designer_file"])
    results = []
    for r in rows:
        w, h = r["file_w_ft"] * FT, r["file_h_ft"] * FT
        excl = set(by_sno[r["sno"]])                                   # leave the whole SHOP out: a twin side or another board of it must not help
        loo_lib = _lib_without(lib, excl)
        best, ranked = ms.choose(loo_lib, cands, w, h, r.get("sheet_type"), exclude=None, evidence={f: e for f, e in EVID.items() if f not in excl})
        # production selection, reproduced: orientation first, then the example-library preference among that orientation's masters
        want = oa.target_orientation(w, h)
        same = [m for m in cands if m.orientation == want]
        if len(same) >= 2:
            orig_lib = ex.library
            ex.library = lambda b, _l=loo_lib: _l                       # in-process only: preferred_master_file reads the library through this name
            orig_cl = ex.candidate_libraries
            ex.candidate_libraries = lambda b, _l=loo_lib: [_l]
            try:
                prod_file = ex.preferred_master_file("hangyo", w, h, available={m.file for m in same}, board_type=r.get("sheet_type")) or same[0].file
            finally:
                ex.library, ex.candidate_libraries = orig_lib, orig_cl
        else:
            prod_file = same[0].file if same else cands[0].file
        lib_m, struct_m = r.get("library_master"), r.get("structural_master_guess")
        if struct_m and str(struct_m).startswith("tie"):
            struct_m = None
        corroborated = lib_m and struct_m and lib_m == struct_m
        truth = lib_m if corroborated else None
        results.append({"file": r["designer_file"], "sno": r["sno"], "size_ft": f"{r['file_w_ft']}x{r['file_h_ft']}", "shape_class": s1.shape_class(w, h), "orientation": want,
                        "reference_library": lib_m, "reference_structural": struct_m, "reference_status": ("inferred, corroborated by two methods" if corroborated else "inferred, methods disagree or only one available"),
                        "production_selection": prod_file, "score_selection": best.file if best else None,
                        "score_ranked": [{"master": s.file, "total": s.total, "evidence": s.evidence, "aspect": s.aspect, "size": s.size, "brand": s.brand, "orientation_penalty": s.orientation_penalty} for s in ranked],
                        "score_reasons": best.reasons if best else [], "mapping_status": r["status"],
                        "production_matches_corroborated": (prod_file == truth) if truth else None, "score_matches_corroborated": ((best.file == truth) if best else False) if truth else None,
                        "production_matches_library": (prod_file == lib_m) if lib_m else None, "score_matches_library": ((best.file == lib_m) if best else False) if lib_m else None})
    def acc(key, pred):
        v = [x[key] for x in results if x[key] is not None and (pred is None or pred(x))]
        return {"n": len(v), "correct": sum(1 for t in v if t), "accuracy": round(sum(1 for t in v if t) / len(v), 3) if v else None}
    summary = {"corroborated_inferred_truth": {"production": acc("production_matches_corroborated", None), "score": acc("score_matches_corroborated", None)},
               "library_inferred_truth": {"production": acc("production_matches_library", None), "score": acc("score_matches_library", None)},
               "by_shape_class": {}}
    for cls in sorted({x["shape_class"] for x in results}):
        summary["by_shape_class"][cls] = {"corroborated": {"production": acc("production_matches_corroborated", lambda x, c=cls: x["shape_class"] == c), "score": acc("score_matches_corroborated", lambda x, c=cls: x["shape_class"] == c)},
                                          "library": {"production": acc("production_matches_library", lambda x, c=cls: x["shape_class"] == c), "score": acc("score_matches_library", lambda x, c=cls: x["shape_class"] == c)}}
    summary["probable_mapping_boards_kept_separate"] = acc("score_matches_corroborated", lambda x: x["mapping_status"] == "PROBABLE")
    truth = [x for x in results if x["production_matches_corroborated"] is not None]
    summary["majority_baseline_always_6X3"] = {"n": len(truth), "accuracy": round(sum(1 for x in truth if x["reference_library"] == "6 X 3.cdr") / max(len(truth), 1), 3),
                                               "note": "the reference is dominated by 6 X 3, so beating production says more about production's orientation / widest-master rules than about the score"}
    non6 = [x for x in results if x["reference_library"] not in (None, "6 X 3.cdr") or (x["reference_structural"] not in (None, "6 X 3.cdr"))]
    summary["boards_whose_inferred_master_is_not_6X3"] = {"n": len(non6), "production_correct_vs_library_or_structure": sum(1 for x in non6 if x["production_selection"] in (x["reference_library"], x["reference_structural"])),
                                                          "score_correct_vs_library_or_structure": sum(1 for x in non6 if x["score_selection"] in (x["reference_library"], x["reference_structural"]))}
    summary["selection_changes_vs_production"] = sum(1 for x in results if x["production_selection"] != x["score_selection"])
    write("master_selection_evaluation.json", {
        "method": "leave-one-SHOP-out: every designer file of the same S.N is removed from the library the score (and the reproduced production rule) may use. Reference master = INFERRED "
                  "(example-library signature match and an independent top-level-structure match); 'corroborated' = both agree. No master mapping is CONFIRMED: no file records it.",
        "weights": {"evidence": ms.W_EVIDENCE, "aspect": ms.W_ASPECT, "size": ms.W_SIZE, "brand": ms.W_BRAND, "cross_orientation_penalty": ms.CROSS_ORIENTATION_PENALTY, "tau": ms.TAU, "k": ms.K_NEAREST},
        "summary": summary, "boards": results})
    print(json.dumps(summary, indent=1))


def cmd_evidence(a):
    """brand_data/hangyo/master_evidence.json: which master each designer board was most likely built from, with its status. INFERRED only - the designer's choice is not
    recorded in any file. `inferred_corroborated` = the example library's signature match and an independent top-level-structure match agree; a single method counts less;
    a disagreement is kept as `disputed` with both candidates at low weight."""
    rows = mapping_rows()
    out = {"brand": "hangyo", "note": "Inferred from Stage 1 (backend/dataset_analysis/stage1/shop_reference_mapping.json): library signature match + top-level structure match. Nothing here is confirmed by a designer.",
           "boards": {}}
    for r in rows:
        lib_m, st_m = r.get("library_master"), r.get("structural_master_guess")
        if st_m and str(st_m).startswith("tie"):
            st_m = None
        if lib_m and st_m and lib_m == st_m:
            ent = {"masters": {lib_m: 1.0}, "status": "inferred_corroborated"}
        elif lib_m and st_m:
            ent = {"masters": {lib_m: 0.4, st_m: 0.4}, "status": "disputed"}
        elif lib_m or st_m:
            ent = {"masters": {(lib_m or st_m): 0.7}, "status": "inferred_library_only" if lib_m else "inferred_structure_only"}
        else:
            continue
        ent["mapping_status"] = r["status"]
        out["boards"][r["designer_file"]] = ent
    (BACKEND / "brand_data" / "hangyo" / "master_evidence.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(Counter(e["status"] for e in out["boards"].values()))


# --------------------------------------------------------------------------------------------------------------- composition


def _boards_by_class(lib: dict, master_id: str | None = None):
    g = defaultdict(list)
    for b in lib["boards"]:
        if b.get("els") and (master_id is None or b.get("master") == master_id):
            g[s1.shape_class(b["W"], b["H"])].append(b)
    return g


def _rec(rels: list, kind: str, objs: list, files: list, evidence: dict, confidence: str, confirm: bool, **extra) -> None:
    rels.append({"id": f"CR-{len(rels) + 1:03d}", "relationship": kind, "objects": objs, "supporting_files": files[:12], "n_files": len(files), "evidence": evidence,
                 "confidence": confidence, "designer_confirmation_required": confirm, **extra})


def _conf(n: int, spread: float, tight: float = 0.01, loose: float = 0.03) -> str:
    return "high" if n >= 6 and spread <= tight else "medium" if n >= 4 and spread <= loose else "low"


def cmd_composition(a):
    """Relationships measured on EXACT COM geometry: master structure (master_model relations), element-to-element offsets across designer boards of one shape family
    (library element boxes = COM-derived), name panel / name block / photo relations read from the designer extractions. Nothing is inferred from image similarity."""
    from app import example_layout as ex, master_model

    lib = ex.library("hangyo")
    rels: list[dict] = []
    # 1. master structure: groups that move together, containers, overlaps, alignment
    for p in sorted(s1.CREATIVE.glob("*.cdr")):
        e = load_ex("masters", p)
        m = master_model.build_model(e, "s2")
        tops = m.top_level()
        for o in tops:
            if o.kind == "group":
                _rec(rels, "protected_group", [f"{p.name}#{o.id}"], [p.name], {"type": o.type, "nested_objects": o.n_descendants, "box_mm": [round(o.box_mm[k]) for k in ("x", "y", "width", "height")],
                                                                          "basis": "a CorelDRAW group: its children are one artwork (e.g. the logo plus its decorative fragments)"}, "high", False)
            if o.kind == "powerclip":
                kids = [m.get(c) for c in o.children]
                _rec(rels, "background_container", [f"{p.name}#{o.id}"], [p.name], {"covers_page": o.box_mm["width"] >= m.page_w - 3 and o.box_mm["height"] >= m.page_h - 3,
                      "children": [(k.type, k.kind, k.n_descendants) for k in kids if k], "fill": (o.appearance.get("fill") or {}).get("type"), "rotation": o.rotation,
                      "basis": "a PowerClip whose frame is the page; its children are the artwork inside the background"}, "high", False)
        for r in m.relations:
            if r["kind"] == "contains":
                _rec(rels, "intentional_overlap_containment", [f"{p.name}#{r['outer']}", f"{p.name}#{r['inner']}"], [p.name], {"relation": "outer box contains inner box (>=95% of its area)"}, "medium", False)
            elif r["kind"] == "text_on":
                _rec(rels, "text_on_surface", [f"{p.name}#{r['text']}", f"{p.name}#{r['surface']}"], [p.name], {"relation": "text lies inside a filled surface above it in z-order"}, "medium", False)
            elif r["kind"] == "repeat":
                _rec(rels, "repeated_elements", [f"{p.name}#{i}" for i in r["ids"]], [p.name], {"relation": "same type, same nested count, size within 3%"}, "low", True)
            elif r["kind"] == "equal_gap":
                _rec(rels, "equal_spacing", [f"{p.name}#{i}" for i in r["ids"]], [p.name], {"axis": r["axis"], "gap_mm": r["gap_mm"]}, "medium", True)
    # 2. element-to-element relative position across designer boards of one shape family
    for mid, mm_ in {m["id"]: m for m in lib["masters"]}.items():
        for cls, boards in _boards_by_class(lib, mid).items():
            if len(boards) < 4:
                continue
            ks = [e["k"] for e in mm_["elements"]]
            for i, ka in enumerate(ks):
                for kb in ks[i + 1:]:
                    rows = [(b, b["els"].get(ka), b["els"].get(kb)) for b in boards if b["els"].get(ka) and b["els"].get(kb)]
                    if len(rows) < 4:
                        continue
                    dx = [(eb["cx"] - ea["cx"]) * b["W"] / b["H"] for b, ea, eb in rows]          # in units of board height
                    dy = [eb["cy"] - ea["cy"] for b, ea, eb in rows]
                    sp = max(st.pstdev(dx), st.pstdev(dy))
                    sx_ratio = [(eb["w"] * b["W"]) / (ea["w"] * b["W"]) for b, ea, eb in rows]    # relative size of the two elements
                    size_sp = st.pstdev(sx_ratio) / max(st.mean(sx_ratio), 1e-9)
                    if sp <= 0.03:
                        _rec(rels, "rigid_relative_position", [f"{mm_['file']}#{ka}", f"{mm_['file']}#{kb}"], [b["file"] for b, _, _ in rows],
                             {"board_shape_class": cls, "offset_dx_in_board_heights_mean": round(st.mean(dx), 3), "offset_dy_mean": round(st.mean(dy), 3), "offset_spread": round(sp, 4),
                              "relative_width_ratio_spread": round(size_sp, 4), "reading": "the two elements keep the same offset, in units of the board height, on every board of this shape - they scale together"},
                             _conf(len(rows), sp), True)
    # 3. name panel / name block / photo, from the designer extractions
    per_cls = defaultdict(list)
    for r in mapping_rows():
        e = load_ex("designers", s1.WORKING / r["designer_file"])
        pw, ph = e["page"]["width_mm"], e["page"]["height_mm"]
        objs = [o for o in e["objects"] if o.get("visible", True)]
        blocks = {sc: s1.name_block(e, sc) for sc in ("ta", "en")}
        photos = [o for o in objs if o["type"] == "bitmap"]
        any_block = next((b for b in blocks.values() if b), None)
        panel = None
        if any_block:
            bx, by, bw, bh = any_block["box"]
            cands = [o for o in objs if o["type"] != "text" and o["parent"] is None or o["kind"] == "powerclip" or o["parent"] is not None]
            inside = [o for o in objs if o["type"] != "text" and o["x"] <= bx + 2 and o["y"] <= by + 2 and o["x"] + o["w"] >= bx + bw - 2 and o["y"] + o["h"] >= by + bh - 2 and o["w"] * o["h"] < 0.97 * pw * ph]
            if inside:
                panel = min(inside, key=lambda o: o["w"] * o["h"])
        per_cls[s1.shape_class(pw, ph)].append({"file": r["designer_file"], "pw": pw, "ph": ph, "blocks": blocks, "photos": photos, "panel": panel})
    for cls, rows in per_cls.items():
        with_panel = [r for r in rows if r["panel"]]
        if len(with_panel) >= 4:
            wf = [r["panel"]["w"] / r["pw"] for r in with_panel]
            hf = [r["panel"]["h"] / r["ph"] for r in with_panel]
            cy = [(r["panel"]["y"] + r["panel"]["h"] / 2) / r["ph"] for r in with_panel]
            _rec(rels, "name_panel", ["smallest non-text object that contains the name block"], [r["file"] for r in with_panel],
                 {"board_shape_class": cls, "panel_width_share": [round(min(wf), 3), round(st.median(wf), 3), round(max(wf), 3)], "panel_height_share": [round(min(hf), 3), round(st.median(hf), 3), round(max(hf), 3)],
                  "panel_centre_y_share": [round(min(cy), 3), round(st.median(cy), 3), round(max(cy), 3)], "n_boards": len(rows), "with_panel": len(with_panel)},
                 _conf(len(with_panel), max(st.pstdev(hf), st.pstdev(cy)), 0.02, 0.06), True)
        for sc, lab in (("ta", "tamil"), ("en", "english")):
            bs = [(r, r["blocks"][sc]) for r in rows if r["blocks"][sc]]
            if len(bs) < 4:
                continue
            fill = [b["box"][3] / (r["panel"]["h"]) for r, b in bs if r["panel"] and r["panel"]["h"] > 0]
            if len(fill) >= 4:
                _rec(rels, "name_block_inside_panel", [f"{lab} name block", "name panel"], [r["file"] for r, _ in bs],
                     {"board_shape_class": cls, "block_height_over_panel_height": [round(min(fill), 3), round(st.median(fill), 3), round(max(fill), 3)], "n": len(fill),
                      "reading": "how much of the panel's height the name block occupies"}, _conf(len(fill), st.pstdev(fill), 0.04, 0.10), True)
        photo_rows = [(r, [p for p in r["photos"] if p["parent"] is None or True]) for r in rows if r["photos"]]
        gaps = []
        for r, ph_ in photo_rows:
            blk = r["blocks"]["en"] or r["blocks"]["ta"]
            if not blk:
                continue
            top_of_block = blk["box"][1] + blk["box"][3]
            below = [p for p in ph_ if p["y"] + p["h"] / 2 < top_of_block]
            if below:
                gaps.append(((top_of_block - max(p["y"] + p["h"] for p in below)) / r["ph"], r["file"]))
        if len(gaps) >= 4:
            g = [x for x, _ in gaps]
            _rec(rels, "name_to_photo_gap", ["name block", "nearest photo below"], [f for _, f in gaps],
                 {"board_shape_class": cls, "gap_over_board_height": [round(min(g), 3), round(st.median(g), 3), round(max(g), 3)], "n": len(g)}, _conf(len(g), st.pstdev(g), 0.02, 0.06), True)
    # 4. repeated panels (wide boards): copies of one artwork
    reps = []
    for r in mapping_rows():
        e = load_ex("designers", s1.WORKING / r["designer_file"])
        tops = [o for o in e["objects"] if o["parent"] is None and o["kind"] == "group" and len(o["children"]) >= 3]
        sig = defaultdict(list)
        for o in tops:
            sig[(len(o["children"]), round(o["w"] / o["h"], 1) if o["h"] else 0)].append(o)
        n_rep = max((len(v) for v in sig.values()), default=1)
        reps.append((r["designer_file"], e["page"]["width_mm"] / e["page"]["height_mm"], n_rep))
    for lo, hi, lab in ((0, 2.5, "compact / portrait"), (2.5, 4.2, "mid"), (4.2, 99, "wide")):
        sel = [x for x in reps if lo <= x[1] < hi]
        if sel:
            _rec(rels, "repeated_panels", ["top-level groups with equal child count and aspect"], [f for f, _, _ in sel],
                 {"board_shape": lab, "max_copies_of_one_group_per_board": dict(Counter(n for _, _, n in sel)), "n_boards": len(sel)}, "medium", True)
    for i, r in enumerate(rels, 1):
        r["id"] = f"CR-{i:03d}"
    write("composition_relationships.json", {"method": "exact COM geometry (extractions), master_model relations, example-library element boxes (COM-derived); no image similarity is used to name a role",
                                             "counts": dict(Counter(r["relationship"] for r in rels)), "relationships": rels,
                                             "not_found": ["contact information region (the Hangyo masters / boards carry no phone / GST / address text; only the 'ADINN/..' job tag)",
                                                           "gradient / pattern fills (declared fills are uniform CMYK on every extracted object)"]})
    print(Counter(r["relationship"] for r in rels))


# ------------------------------------------------------------------------------------------------------------------- wide boards


def cmd_wide(a):
    """The designers' wide boards (>= 4.2:1: 15x3, 16x3, 20x3, 20x4, 26x3) measured object by object: what is mirrored, what is anchored, how the centre panel grows,
    whether artwork is repeated or only the panel widens, how the name is set. Only principles with enough support become part of the specification; the rest is left
    UNSPECIFIED (legacy behaviour + a manual-review flag) - no composition is invented."""
    rows = [r for r in mapping_rows() if r["file_w_ft"] / r["file_h_ft"] >= 4.2]
    boards = []
    for r in rows:
        e = load_ex("designers", s1.WORKING / r["designer_file"])
        pw, ph = e["page"]["width_mm"], e["page"]["height_mm"]
        tops = [o for o in e["objects"] if o["parent"] is None and o.get("visible", True)]
        big = [o for o in tops if o["type"] != "text" and o["w"] * o["h"] < 0.95 * pw * ph and o["w"] > 0.15 * pw]
        pairs = []
        used = set()
        for i, o in enumerate(tops):
            if i in used or o["type"] == "text" or o["w"] * o["h"] >= 0.95 * pw * ph:
                continue
            cx = (o["x"] + o["w"] / 2) / pw
            for j in range(i + 1, len(tops)):
                q = tops[j]
                if j in used or q["type"] != o["type"] or q["type"] == "text":
                    continue
                cq = (q["x"] + q["w"] / 2) / pw
                if abs((cx + cq) - 1.0) < 0.04 and abs(o["w"] / q["w"] - 1) < 0.06 and abs(o["h"] / q["h"] - 1) < 0.06 and abs((o["y"] + o["h"] / 2) - (q["y"] + q["h"] / 2)) < 0.02 * ph:
                    used.update((i, j))
                    left, right = (o, q) if cx < cq else (q, o)
                    pairs.append({"type": o["type"], "kind": o["kind"], "children": len(o["children"]), "w_mm": round(o["w"]), "h_mm": round(o["h"]), "w_share": round(o["w"] / pw, 4), "h_share": round(o["h"] / ph, 4),
                                  "aspect": round(o["w"] / o["h"], 3), "left_edge_gap_share_of_width": round(left["x"] / pw, 4), "left_edge_gap_mm": round(left["x"]),
                                  "centre_y_share": round((o["y"] + o["h"] / 2) / ph, 3), "rotation_left_right": [round(left["rotation"]), round(right["rotation"])]})
                    break
        blocks = {sc: s1.name_block(e, sc) for sc in ("ta", "en")}
        panel = None
        bl = blocks["ta"] or blocks["en"]
        if bl:
            bx, by, bw, bh = bl["box"]
            inside = [o for o in e["objects"] if o["type"] != "text" and o["x"] <= bx + 2 and o["y"] <= by + 2 and o["x"] + o["w"] >= bx + bw - 2 and o["y"] + o["h"] >= by + bh - 2 and o["w"] * o["h"] < 0.97 * pw * ph]
            panel = min(inside, key=lambda o: o["w"] * o["h"]) if inside else None
        bg = next((o for o in tops if o["w"] * o["h"] >= 0.95 * pw * ph), None)
        boards.append({"file": r["designer_file"], "size_ft": f"{r['file_w_ft']}x{r['file_h_ft']}", "W_mm": round(pw), "H_mm": round(ph), "aspect": round(pw / ph, 2), "language": r.get("content_script"),
                       "mirrored_pairs": pairs, "mirrored_pair_count": len(pairs), "top_level_objects": len(tops),
                       "background": {"type": bg["type"], "kind": bg["kind"], "children": [(c["type"]) for c in e["objects"] if c["parent"] == bg["id"]], "covers_page": bool(bg and abs(bg["x"]) < 2 and bg["w"] >= pw - 3)} if bg else None,
                       "name_panel": ({"w_share": round(panel["w"] / pw, 3), "h_share": round(panel["h"] / ph, 3), "cx_share": round((panel["x"] + panel["w"] / 2) / pw, 3), "w_mm": round(panel["w"])} if panel else None),
                       "tamil_block": ({"lines": blocks["ta"]["lines"], "h_share": round(blocks["ta"]["box"][3] / ph, 3), "w_share": round(blocks["ta"]["box"][2] / pw, 3), "cy_share": round((blocks["ta"]["box"][1] + blocks["ta"]["box"][3] / 2) / ph, 3),
                                        "cx_share": round((blocks["ta"]["box"][0] + blocks["ta"]["box"][2] / 2) / pw, 3), "fonts": blocks["ta"]["fonts"]} if blocks["ta"] else None),
                       "english_block": ({"lines": blocks["en"]["lines"], "h_share": round(blocks["en"]["box"][3] / ph, 3), "w_share": round(blocks["en"]["box"][2] / pw, 3), "cy_share": round((blocks["en"]["box"][1] + blocks["en"]["box"][3] / 2) / ph, 3),
                                         "cx_share": round((blocks["en"]["box"][0] + blocks["en"]["box"][2] / 2) / pw, 3), "fonts": blocks["en"]["fonts"]} if blocks["en"] else None)})
    n = len(boards)

    def rng(vals):
        vals = [v for v in vals if v is not None]
        return None if not vals else {"n": len(vals), "min": round(min(vals), 3), "median": round(st.median(vals), 3), "max": round(max(vals), 3), "spread": round(st.pstdev(vals), 4)}

    principles = []

    def add(pid, statement, ev, confidence, specified, how=None):
        principles.append({"id": pid, "principle": statement, "evidence": ev, "confidence": confidence, "specified": specified, "how_to_apply": how})

    add("W1", "Every wide board has a page-covering background PowerClip (the background stretches with the board).", {"boards": n, "with_cover": sum(1 for b in boards if b["background"] and b["background"]["covers_page"])}, "high", True,
        "keep the master's background stretch (legacy behaviour already does)")
    mp = [b["mirrored_pair_count"] for b in boards]
    add("W2", "Artwork is placed as MIRRORED LEFT / RIGHT PAIRS about the vertical centre line; the board gains width through the centre panel, not by repeating more artwork.",
        {"mirrored_pairs_per_board": dict(Counter(mp)), "boards": n, "boards_with_>=3_pairs": sum(1 for x in mp if x >= 3),
         "width_range_ft": [min(b["W_mm"] for b in boards) / FT, max(b["W_mm"] for b in boards) / FT], "reading": "the pair count does not grow with the width (15x3 .. 26x3)"},
        "medium" if sum(1 for x in mp if x >= 3) >= 0.7 * n else "low", sum(1 for x in mp if x >= 3) >= 0.7 * n,
        "place the master's side artwork as mirrored pairs at the edge gaps below; do not tile it")
    big = [p for b in boards for p in b["mirrored_pairs"] if p["w_share"] > 0.15]
    add("W3", "The large side artwork (logo group) keeps its aspect ratio and fills 70-84% of the board height.",
        {"h_share": rng([p["h_share"] for p in big]), "aspect": rng([p["aspect"] for p in big]), "w_mm": rng([p["w_mm"] for p in big]), "n_pairs": len(big)},
        "medium" if len(big) >= 5 else "low", False, "scale uniformly by height; width follows; the 26x3 board (0.548) deviates - do not specify a single share")
    gaps = [p["left_edge_gap_share_of_width"] for p in big]
    add("W4", "The side artwork keeps a margin to its board edge of a few percent of the width (not a fixed number of mm).",
        {"edge_gap_share_of_width": rng(gaps), "edge_gap_mm": rng([p["left_edge_gap_mm"] for p in big])}, "low", False, "insufficient agreement: leave to legacy")
    pan = [b["name_panel"] for b in boards if b["name_panel"]]
    add("W5", "The name sits on a centred panel (full board height) whose width share varies with the design.",
        {"panel_w_share": rng([p["w_share"] for p in pan]), "panel_h_share": rng([p["h_share"] for p in pan]), "panel_cx_share": rng([p["cx_share"] for p in pan]), "n": len(pan)},
        "medium" if len(pan) >= 6 else "low", False, "centre x is consistent; width is not - leave to legacy")
    ta = [b["tamil_block"] for b in boards if b["tamil_block"]]
    en = [b["english_block"] for b in boards if b["english_block"]]
    add("W6", "Names are centred on the panel (x = 0.50); Tamil is the upper, larger block, English the lower, smaller block.",
        {"tamil_cx": rng([t["cx_share"] for t in ta]), "english_cx": rng([t["cx_share"] for t in en]), "tamil_cy": rng([t["cy_share"] for t in ta]), "english_cy": rng([t["cy_share"] for t in en]),
         "tamil_h_share": rng([t["h_share"] for t in ta]), "english_h_share": rng([t["h_share"] for t in en]), "n": len(ta)},
        "high" if len(ta) >= 6 and rng([t['cx_share'] for t in ta])["spread"] < 0.01 else "medium", True, "centre both blocks on the panel; the vertical split and sizes vary with name length")
    add("W7", "Tamil name line count: mostly 2 lines; English 1-2 lines (the count depends on the name length, not on the board size).",
        {"tamil_lines": dict(Counter(t["lines"] for t in ta)), "english_lines": dict(Counter(t["lines"] for t in en))}, "medium", False, "choose by name length with the fit rule (legacy)")
    add("W8", "Height of the Tamil block: 27-68% of the board height, English 12-36% - a wide range, set per name and design.",
        {"tamil_h_share": rng([t["h_share"] for t in ta]), "english_h_share": rng([t["h_share"] for t in en])}, "low", False, "NOT specified: sizes cannot be inferred from the board shape")
    add("W9", "There are at least two board designs inside the wide family (side logos + centre panel; centre artwork with side logos; a flat 36-curve variant with the logo flattened).",
        {"top_level_object_counts": sorted(b["top_level_objects"] for b in boards)}, "medium", False, "a rule must be chosen per design; flag for review")
    write("wide_board_analysis.json", {"definition": "boards with W:H >= 4.2 (15x3, 16x3, 20x3, 20x4, 26x3)", "n_boards": n, "boards": boards, "principles": principles,
                                      "specification": {"apply": [p["id"] for p in principles if p["specified"]], "leave_to_legacy_and_flag_for_review": [p["id"] for p in principles if not p["specified"]],
                                                        "rules": "see principles marked specified=true; everything else keeps the legacy placement and the board is marked REVIEW (example_layout.WIDE_REVIEW_ASPECT -> confidence)",
                                                        "never": ["stretch a logo or photograph to fill space", "invent a composition without >= 6 supporting boards"]}})
    print({p["id"]: (p["confidence"], p["specified"]) for p in principles})


# ------------------------------------------------------------------------------------------------------------------ typography


def _pdf_fonts(p: Path) -> list[str]:
    import re

    if not p.is_file():
        return []
    raw = p.read_bytes()
    names = set(re.findall(rb"/(?:BaseFont|FontName)\s*/([A-Za-z0-9+_,\-]+)", raw))
    return sorted(n.decode("latin-1") for n in names)


def _crop_ssim(ref_png: Path, gen_png: Path, box_mm: list, page_mm: tuple, tmp: Path) -> float | None:
    from PIL import Image

    import metrics

    try:
        a, b = Image.open(ref_png).convert("RGB"), Image.open(gen_png).convert("RGB")
    except OSError:
        return None
    pw, ph = page_mm

    def crop(im):
        W, H = im.size
        x, y, w, h = box_mm
        pad = 0.01 * pw
        l, r = max(0, int((x - pad) / pw * W)), min(W, int((x + w + pad) / pw * W))
        t, bt = max(0, int((1 - (y + h + pad) / ph) * H)), min(H, int((1 - (y - pad) / ph) * H))
        return im.crop((l, t, max(r, l + 8), max(bt, t + 8)))

    ca, cb = crop(a), crop(b)
    cb = cb.resize(ca.size)
    tmp.mkdir(parents=True, exist_ok=True)
    ca.save(tmp / "a.png")
    cb.save(tmp / "b.png")
    try:
        return float(metrics.ssim_between_files(str(tmp / "b.png"), str(tmp / "a.png")))
    except Exception:  # noqa: BLE001
        return None


def cmd_typography(a):
    """Typography facts and the real-CorelDRAW check. Needs the Stage 2 run's generated extraction (`stage2_eval.py extract --run RUN`): fonts are read from the
    SAVED and RE-OPENED CDRs, the PDFs' embedded font names are parsed, and the Tamil name block is compared against the designer's crop by crop (SSIM)."""
    from app import fonts

    run = getattr(a, "run", None) or "s2_prod"
    R = S2 / "runs" / run
    inst = fonts.installed_fonts()
    registry = [n for n, _ in fonts._registry_entries()[0] if "anek" in n.lower() or "nirmala" in n.lower()] if sys.platform == "win32" else []
    rows = mapping_rows()
    d_fonts, d_sizes = defaultdict(Counter), defaultdict(list)
    bad_sizes = [0]
    for r in rows:
        e = load_ex("designers", s1.WORKING / r["designer_file"])
        for o in e["objects"]:
            if o["type"] == "text" and o.get("text"):
                c = o["text"].get("content") or ""
                sc = "tamil" if s1.TAMIL.search(c) else "english"
                d_fonts[sc][o["text"].get("font") or "(mixed)"] += 1
                if (o["text"].get("size_pt") or 0) > 0:
                    d_sizes[(s1.shape_class(e["page"]["width_mm"], e["page"]["height_mm"]), sc)].append(o["text"]["size_pt"])
                else:
                    bad_sizes[0] += 1                      # Story.Size reads back negative / zero after CorelDRAW scales a text: unreliable, never used for fitting
    def gen_fonts(root: Path, group_dir: str):
        out = defaultdict(Counter)
        for r in rows:
            f = root / "extractions" / "generated" / s1.safe(s1.safe(Path(r["designer_file"]).stem))
            if not (f / "extraction.json").is_file():
                continue
            e = json.loads((f / "extraction.json").read_text(encoding="utf-8"))
            for o in e["objects"]:
                if o["type"] == "text" and o.get("text"):
                    c = o["text"].get("content") or ""
                    out["tamil" if s1.TAMIL.search(c) else "english"][o["text"].get("font") or "(mixed)"] += 1
        return {k: dict(v) for k, v in out.items()}
    s1_fonts = gen_fonts(S1, "stage1")
    s2_fonts = gen_fonts(R, run) if (R / "extractions").is_dir() else None
    per_board = []
    if s2_fonts is not None:
        for r in rows:
            case = s1.safe(Path(r["designer_file"]).stem)
            ref_dir = s1.extraction_dir(S1, "designers", s1.WORKING / r["designer_file"])
            e_ref = json.loads((ref_dir / "extraction.json").read_text(encoding="utf-8"))
            pw, ph = e_ref["page"]["width_mm"], e_ref["page"]["height_mm"]
            blk = s1.name_block(e_ref, "ta")
            row = {"case": case, "size_ft": f"{r['file_w_ft']}x{r['file_h_ft']}", "language": r.get("content_script"), "has_tamil_reference": bool(blk)}
            for tag, root in (("stage1", S1), ("stage2", R)):
                gd = root / "extractions" / "generated" / case
                if (gd / "extraction.json").is_file():
                    g = json.loads((gd / "extraction.json").read_text(encoding="utf-8"))
                    row[f"{tag}_tamil_fonts_after_reopen"] = sorted({(o["text"] or {}).get("font") for o in g["objects"] if o["type"] == "text" and s1.TAMIL.search((o.get("text") or {}).get("content") or "")})
                    row[f"{tag}_pdf_fonts"] = _pdf_fonts(root / "generated" / f"{case}.pdf")
                    if blk:
                        row[f"{tag}_tamil_crop_ssim"] = _crop_ssim(ref_dir / "master_page.png", gd / "master_page.png", blk["box"], (pw, ph), S2 / "_tmp_crop")
            per_board.append(row)
    ta_boards = [b for b in per_board if b["has_tamil_reference"]]
    def med(k):
        v = [b[k] for b in ta_boards if isinstance(b.get(k), float)]
        return round(st.median(v), 3) if v else None
    write("typography_analysis.json", {
        "root_cause": {"where": "corel_util.ensure_tamil_font_renders -> font_renders_tamil(story.Font) -> fonts.font_file(family) -> fonts.resolve_font_file",
                       "why": "Anek Tamil is a VARIABLE font: Windows registers it once, as 'Anek Tamil Medium (TrueType)'. resolve_font_file('Anek Tamil') needs an exact family name or '<family> Regular', so it "
                              "returned None, font_renders_tamil() said False, and every Tamil text set in Anek Tamil (the masters' own Tamil lines and the duplicated Tamil line) was replaced by layout.TAMIL_FONT "
                              "(Nirmala UI). 36 of 36 Tamil boards in Stage 1.",
                       "fix": "fonts.family_files() (any face of the family) used by font_renders_tamil when the exact lookup fails; brand_data/<brand>/typography.json names the brand's Tamil font, used when the "
                              "master's font cannot draw Tamil; both behind SIGNAGE_TYPOGRAPHY_V2 (default on)."},
        "windows": {"registry_names_for_anek_or_nirmala": registry, "installed_families_listing_anek": [f for f in inst.get("fonts", []) if "anek" in f.lower()], "amaranth_installed": "Amaranth" in inst.get("fonts", []),
                    "family_files_anek": fonts.family_files("Anek Tamil"), "font_has_tamil_anek": [fonts.font_has_tamil(p) for p in fonts.family_files("Anek Tamil")]},
        "designer_typography": {"fonts_by_script": {k: dict(v) for k, v in d_fonts.items()}, "unreliable_size_readings_excluded": bad_sizes[0],
                                "size_note": "font size in pt is read back from CorelDRAW after the designers scaled the text; non-positive readings are excluded, and name fitting uses block geometry in mm instead",
                                "size_pt_by_shape_and_script": {f"{k[0]} | {k[1]}": {"n": len(v), "min": round(min(v), 1), "median": round(st.median(v), 1), "max": round(max(v), 1)} for k, v in sorted(d_sizes.items())}},
        "engine_fonts_stage1_baseline": s1_fonts, "engine_fonts_stage2_run": {"run": run, "fonts": s2_fonts},
        "real_corelDRAW_verification": {"source": f"stage2 run '{run}': fonts read back from the saved + re-opened CDR (extraction), PDF embedded font names parsed from the exported PDF, Tamil name block cropped from the real CorelDRAW render and compared with the designer's",
                                        "boards_with_tamil": len(ta_boards), "median_tamil_crop_ssim_stage1": med("stage1_tamil_crop_ssim"), "median_tamil_crop_ssim_stage2": med("stage2_tamil_crop_ssim")},
        "amaranth": {"installed": "Amaranth" in inst.get("fonts", []), "policy": "not installed or distributed; the master's Amaranth text is kept, the engine report lists it as missing and the board as not designer-equivalent",
                     "actual_substitute_font": "not readable through COM (Story.Font returns the requested name); unknown"},
        "per_board": per_board})
    print("typography_analysis.json written; tamil boards:", len(ta_boards))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["typography", "master", "composition", "wide", "evidence", "all"])
    ap.add_argument("--run", default="s2_prod")
    a = ap.parse_args()
    for name in (["typography", "master", "composition", "wide"] if a.what == "all" else [a.what]):
        fn = globals().get("cmd_" + name)
        if fn is None:
            print("not implemented yet:", name)
            continue
        fn(a)


if __name__ == "__main__":
    main()
