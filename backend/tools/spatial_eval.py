"""Planner ON vs OFF evaluation on real CorelDRAW (Hangyo masters, rules-mode brands so no designer example is used).

  python tools/spatial_eval.py run     --base http://127.0.0.1:8021 --out DIR       # converts every scenario through a running server
  python tools/spatial_eval.py compare --off DIR_OFF --on DIR_ON --on-data SERVER_DATA_ON --out report.json
  python tools/spatial_eval.py verify  --dir DIR [--dump]                           # PDF / PNG / CDR sanity (CDR opened by real CorelDRAW with --dump)

`run` is meant to be pointed at two servers started one after the other (NEVER two CorelDRAW servers at once): one with
SIGNAGE_SPATIAL_PLANNER=1 SIGNAGE_MASTER_ANALYSIS=1, one with SIGNAGE_SPATIAL_PLANNER=0. Brands "dalmia" (rules mode: no example library) and
"reflowtest" are used on purpose: a Hangyo-branded shop would be laid out from a designer example and the planner would be bypassed.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CREATIVE = ROOT / "signage_dataset" / "Hangyo" / "Creative"
WORKING = ROOT / "signage_dataset" / "Hangyo" / "03 - Tiruvannamalai - 31 Stores" / "02 - Working"
FT = 304.8

# (id, category, master key, target W ft, target H ft)
SCENARIOS = [
    ("same_6x3_to_12x6", "same aspect", "M6", 12, 6),
    ("same_10x3_to_20x6", "same aspect", "M10", 20, 6),
    ("same_4x8_to_6x12", "same aspect (portrait)", "P48", 6, 12),
    ("same_4x8_to_3x6", "same aspect (portrait)", "P48", 3, 6),
    ("diff_6x3_to_8x4", "different aspect", "M6", 8, 4),
    ("diff_6x3_to_7x3", "different aspect", "M6", 7, 3),
    ("diff_10x3_to_12x4", "different aspect", "M10", 12, 4),
    ("diff_4x8_to_4x7", "different aspect (portrait)", "P48", 4, 7),
    ("diff_4x8_to_5x8", "different aspect (portrait)", "P48", 5, 8),
    ("wide_6x3_to_20x4", "very wide", "M6", 20, 4),
    ("wide_10x3_to_26x3", "very wide", "M10", 26, 3),
    ("reflow_10x3_to_4x8", "orientation change", "R10", 4, 8),
    ("reflow_10x3_to_3x6", "orientation change", "R10", 3, 6),
]
MASTER_FILES = {"M10": "10 X 3.cdr", "M6": "6 X 3.cdr", "P48": "4 X 8.cdr", "R10": "10 X 3.cdr"}


# ------------------------------------------------------------------------------------------------------------------ run


def _call(base, method, path, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = dict(headers or {})
    if body is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read() or b"null")


def _multipart(fields, fname, fpath):
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="master"; filename="{fname}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
    parts += [Path(fpath).read_bytes(), f"\r\n--{b}--\r\n".encode()]
    return b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={b}"}


def _upload(base, brand, orientation, key):
    body, h = _multipart({"brand": brand, "orientation": orientation}, MASTER_FILES[key], CREATIVE / MASTER_FILES[key])
    return _call(base, "POST", "/api/masters/upload", headers=h, raw=body)["id"]


def _wait_analysis(base, mid, secs=600):
    end = time.time() + secs
    st = "?"
    while time.time() < end:
        st = _call(base, "GET", f"/api/masters/{mid}/analysis")["status"]
        if st in ("completed", "failed", "disabled"):
            return st
        time.sleep(3)
    return st


def cmd_run(a):
    base, out = a.base.rstrip("/"), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    masters = {"M10": _upload(base, "dalmia", "landscape", "M10"), "M6": _upload(base, "dalmia", "landscape", "M6"),
               "P48": _upload(base, "dalmia", "portrait", "P48"), "R10": _upload(base, "reflowtest", "landscape", "R10")}
    analysis = {k: _wait_analysis(base, v) for k, v in masters.items()}
    print("masters", masters, "analysis", analysis, flush=True)
    shops = []
    for sid, cat, mk, w, h in SCENARIOS:
        payload = {"name": "EVAL SHOP", "shop_name_local": "மதிப்பீடு கடை", "width": w, "height": h, "width_unit": "ft", "height_unit": "ft",
                   "unit": "ft", "language": "both"}
        if mk == "R10":
            payload["landscape_master_id"] = masters[mk]                      # the ONLY master of that brand: a portrait board must reflow it
        else:
            payload["master_id"] = masters[mk]
        job = masters[mk]
        r = _call(base, "POST", f"/api/v2/jobs/{job}/shops", payload)
        shops.append({"scenario": sid, "category": cat, "master": mk, "master_id": masters[mk], "w_ft": w, "h_ft": h, "shop_id": r["id"], "payload": payload})
    for s in shops:                                  # queue them all: the server converts one at a time
        _call(base, "POST", f"/api/v2/shops/{s['shop_id']}/convert", s["payload"])
    t0 = time.time()
    while True:
        sts = {s["shop_id"]: _call(base, "GET", f"/api/v2/shops/{s['shop_id']}/status") for s in shops}
        pend = [k for k, v in sts.items() if v["status"] not in ("done", "failed", "error")]
        print(f"{int(time.time() - t0)}s pending {len(pend)}/{len(shops)}", flush=True)
        if not pend:
            break
        time.sleep(10)
    results = []
    for s in shops:
        st = sts[s["shop_id"]]
        rep = st.get("report") or {}
        row = {k: s[k] for k in ("scenario", "category", "master", "master_id", "w_ft", "h_ft", "shop_id")}
        row.update(status=st["status"], error=st.get("error"), warnings=rep.get("warnings") or [], layout=rep.get("layout"),
                   objects=rep.get("objects"), page_mm=rep.get("target_mm") or {"w": s["w_ft"] * FT, "h": s["h_ft"] * FT},
                   master_used=rep.get("master_used"), seconds=rep.get("timings_s"), files={})
        for kind, name in (st.get("files") or {}).items():
            if not isinstance(name, str):
                continue
            try:
                data = urllib.request.urlopen(base + f"/api/v2/shops/{s['shop_id']}/files/{urllib.parse.quote(name)}", timeout=120).read()
                dest = out / f"{s['scenario']}__{Path(name).name}"
                dest.write_bytes(data)
                row["files"][kind] = dest.name
            except Exception as e:  # noqa: BLE001
                row["files"][kind] = f"download failed: {e}"
        results.append(row)
    (out / "results.json").write_text(json.dumps({"masters": masters, "analysis": analysis, "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", out / "results.json")


# --------------------------------------------------------------------------------------------------------------- compare


def _aspect(b):
    return b["w"] / b["h"] if b["h"] > 0 else None


def _box(o):
    return {"x": o["x"], "y": o["y"], "w": o["w"], "h": o["h"]}


def _overlap_share(a, b):
    ox = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    oy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    return ox * oy / max(min(a["w"] * a["h"], b["w"] * b["h"]), 1e-9)


def board_metrics(objs, page_w, page_h):
    """Quality counters of ONE board from its placed objects (report.objects). Same function for OFF and ON."""
    m = {"stretched": [], "out_of_page": [], "new_overlaps": [], "edge": {}, "bg_ok": None, "text_off_board": []}
    fg = [o for o in objs if o.get("role") not in ("bg", "frame") and "_tile" not in o["id"]]
    for o in objs:
        orig, b = o.get("orig") or {}, _box(o)
        if orig.get("h"):
            if o["role"] not in ("bg", "frame") and abs(_aspect(b) / _aspect(orig) - 1) > 0.01 and o["role"] != "shopname":
                m["stretched"].append({"id": o["id"], "aspect_change": round(_aspect(b) / _aspect(orig), 3)})
        out = max(-b["x"], -b["y"], b["x"] + b["w"] - page_w, b["y"] + b["h"] - page_h, 0.0)
        if o["role"] != "bg" and out > 0.002 * min(page_w, page_h):
            m["out_of_page"].append({"id": o["id"], "mm": round(out, 1)})
            if o["role"] in ("shopname", "text"):
                m["text_off_board"].append(o["id"])
        if o["role"] == "bg":
            m["bg_ok"] = abs(b["x"]) < 1 and abs(b["y"]) < 1 and abs(b["w"] - page_w) < 2 and abs(b["h"] - page_h) < 2
    for i, a in enumerate(fg):
        for b in fg[i + 1:]:
            was = _overlap_share(a.get("orig") or {"x": 0, "y": 0, "w": 0, "h": 0}, b.get("orig") or {"x": 0, "y": 0, "w": 0, "h": 0})
            now = _overlap_share(_box(a), _box(b))
            if now >= 0.10 and was < 0.05:
                m["new_overlaps"].append([a["id"], b["id"], round(now, 2)])
    return m


def cmd_compare(a):
    sys.path.insert(0, str(ROOT / "backend"))
    sys.path.insert(0, str(ROOT / "backend" / "tools"))
    from app import adaptation_plan, master_model
    try:
        import metrics
    except Exception:  # noqa: BLE001
        metrics = None
    off = json.loads((Path(a.off) / "results.json").read_text(encoding="utf-8"))
    on = json.loads((Path(a.on) / "results.json").read_text(encoding="utf-8"))
    off_by = {r["scenario"]: r for r in off["results"]}
    report = {"scenarios": []}
    for r_on in on["results"]:
        sid = r_on["scenario"]
        r_off = off_by.get(sid)
        sc = {"scenario": sid, "category": r_on["category"], "target_ft": [r_on["w_ft"], r_on["h_ft"]], "status_off": r_off and r_off["status"],
              "status_on": r_on["status"]}
        report["scenarios"].append(sc)
        if not r_off or r_off["status"] != "done" or r_on["status"] != "done":
            sc["verdict"] = "not comparable (a conversion did not finish)"
            sc["errors"] = {"off": r_off and r_off.get("error"), "on": r_on.get("error")}
            continue
        spatial = (r_on.get("layout") or {}).get("spatial") or next((w for w in r_on["warnings"] if "spatial planner" in w), None)
        sc["planner_on"] = spatial
        used = isinstance(spatial, dict) and spatial.get("used") or (isinstance(spatial, str) and "not used" not in spatial)
        sc["planner_active"] = bool(used)
        tw, th = r_on["w_ft"] * FT, r_on["h_ft"] * FT
        o_off = {o["id"]: o for o in r_off["objects"]}
        o_on = {o["id"]: o for o in r_on["objects"]}
        # why each object moved: the plan itself (deterministic) from the stored model of the ON server
        plan_by = {}
        mp = Path(a.on_data) / "jobs_v2" / r_on["master_id"] / "spatial_model.json"
        if mp.is_file():
            model = master_model.MasterModel.from_dict(json.loads(mp.read_text(encoding="utf-8")))
            plan = adaptation_plan.plan_adaptation(model, tw, th)
            sc["plan"] = {"strategy": plan.strategy, "supported": plan.supported, "reason": plan.reason,
                          "validation": [(v["severity"], v["rule"]) for v in plan.validation]}
            plan_by = {p.id: p for p in plan.objects}
        rows, moved = [], 0
        for oid, x in o_off.items():
            y = o_on.get(oid)
            if not y:
                continue
            d = {k: round(y[k] - x[k], 2) for k in ("x", "y", "w", "h")}
            changed = max(abs(v) for v in d.values()) > 0.5
            moved += changed
            p = plan_by.get(oid)
            rows.append({"id": oid, "role": x["role"], "name": x["name"], "legacy_box": [round(x[k], 1) for k in ("x", "y", "w", "h")],
                         "planner_box": [round(y[k], 1) for k in ("x", "y", "w", "h")], "delta_mm": d, "changed": changed,
                         "why": (f"{p.mode}: {p.reason}" if p else "no plan entry") if changed else
                                ("identical" if not (p and p.mode) else f"identical ({p.mode}: same result as legacy)")})
        sc["objects"] = rows
        sc["objects_changed"] = moved
        sc["max_shift_mm"] = max((max(abs(v) for v in r["delta_mm"].values()) for r in rows), default=0)
        sc["metrics_off"], sc["metrics_on"] = board_metrics(r_off["objects"], tw, th), board_metrics(r_on["objects"], tw, th)
        viol = lambda m: len(m["stretched"]) + len(m["out_of_page"]) + len(m["new_overlaps"]) + (0 if m["bg_ok"] in (True, None) else 1)  # noqa: E731
        sc["violations"] = {"off": viol(sc["metrics_off"]), "on": viol(sc["metrics_on"])}
        # visual similarity: OFF vs ON, and each against a designer board of the same size (a different shop, so only the layout compares)
        png_off, png_on = (Path(a.off) / (r_off["files"].get("preview") or r_off["files"].get("png") or "")), (Path(a.on) / (r_on["files"].get("preview") or r_on["files"].get("png") or ""))
        if metrics and png_off.is_file() and png_on.is_file():
            v = metrics.visual_similarity(str(png_off), str(png_on))
            sc["visual_off_vs_on"] = {k: round(v[k], 3) for k in ("ssim", "phash", "edge", "combined") if k in v}
            des = _designer_preview(r_on["w_ft"], r_on["h_ft"])
            if des is not None:
                dpath = Path(a.out).with_suffix("").parent / f"designer_{r_on['w_ft']}x{r_on['h_ft']}.png"
                dpath.write_bytes(des)
                so, sn = metrics.visual_similarity(str(png_off), str(dpath)), metrics.visual_similarity(str(png_on), str(dpath))
                sc["visual_vs_designer"] = {"off": round(so["combined"], 3), "on": round(sn["combined"], 3), "delta": round(sn["combined"] - so["combined"], 3)}
        # verdict - nothing is called an improvement unless a number says so
        if not sc["planner_active"]:
            sc["verdict"] = "planner not active: output identical to legacy (no evidence either way)"
        elif moved == 0:
            sc["verdict"] = "planner active, no object differs from legacy (equal)"
        else:
            vd = sc.get("visual_vs_designer")
            if sc["violations"]["on"] > sc["violations"]["off"]:
                sc["verdict"] = "WORSE: more rule violations with the planner"
            elif sc["violations"]["on"] < sc["violations"]["off"]:
                sc["verdict"] = "BETTER: fewer rule violations with the planner"
            elif vd and vd["delta"] >= 0.01:
                sc["verdict"] = "BETTER (visual similarity to the designer board)"
            elif vd and vd["delta"] <= -0.01:
                sc["verdict"] = "WORSE (visual similarity to the designer board)"
            else:
                sc["verdict"] = "equal on every measure (no violation or similarity difference)"
    Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for sc in report["scenarios"]:
        print(f"{sc['scenario']:24} active={sc.get('planner_active')} changed={sc.get('objects_changed')} viol={sc.get('violations')} "
              f"vsDesigner={sc.get('visual_vs_designer')} -> {sc['verdict']}")


_DESIGNERS: dict | None = None


def _designer_preview(w_ft: int, h_ft: int) -> bytes | None:
    global _DESIGNERS
    if _DESIGNERS is None:
        _DESIGNERS = {}
        for f in sorted(WORKING.glob("*.cdr")):
            m = re.match(r"(\d+) - (\d+) X (\d+) Feet", f.name)
            if m:
                _DESIGNERS.setdefault((int(m[2]), int(m[3])), f)
    f = _DESIGNERS.get((w_ft, h_ft))
    if not f:
        return None
    try:
        return zipfile.ZipFile(f).read("previews/page1.png")
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------------------------------------------- verify


def cmd_verify(a):
    from PIL import Image

    d = Path(a.dir)
    res = json.loads((d / "results.json").read_text(encoding="utf-8"))["results"]
    rows, cdrs = [], []
    for r in res:
        row = {"scenario": r["scenario"], "status": r["status"]}
        for kind, name in (r.get("files") or {}).items():
            p = d / name
            if not p.is_file():
                continue
            if name.lower().endswith(".pdf"):
                head = p.read_bytes()[:5]
                row["pdf"] = {"ok": head == b"%PDF-", "bytes": p.stat().st_size}
            elif name.lower().endswith(".png"):
                try:
                    with Image.open(p) as im:
                        row["png"] = {"ok": True, "size": im.size}
                except Exception as e:  # noqa: BLE001
                    row["png"] = {"ok": False, "error": str(e)}
            elif name.lower().endswith(".cdr"):
                try:
                    z = zipfile.ZipFile(p)
                    row["cdr_zip"] = {"ok": z.testzip() is None, "bytes": p.stat().st_size}
                except Exception as e:  # noqa: BLE001
                    row["cdr_zip"] = {"ok": False, "error": str(e)}
                cdrs.append((r["scenario"], p))
        rows.append(row)
    if a.dump:                      # open every CDR in real CorelDRAW (read-only) and read its page size back
        sys.path.insert(0, str(ROOT / "backend"))
        from app import corel_supervisor

        jobs = [{"dump_only": str(p), "dump_cache": str(d / (p.stem + ".dump.json"))} for _s, p in cdrs]
        out = corel_supervisor.run_batch(jobs, d / "_verify_results.json", overall_timeout_s=900)
        by = {Path(e["dump_only"]).name: e for e in out}
        for row in rows:
            for s, p in cdrs:
                if s == row["scenario"]:
                    e = by.get(str(p))
                    e = e or next((v for k, v in by.items() if k == str(p)), None) or next((x for x in out if x["dump_only"] == str(p)), None)
                    if e:
                        dump = e.get("dump") or {}
                        row["cdr_open_in_corel"] = {"ok": e["status"] == "done", "error": e.get("error"),
                                                    "page_mm": dump.get("page_mm"), "shapes": dump.get("shape_count")}
    (d / "verify.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    for row in rows:
        print(row)


# ------------------------------------------------------------------------------------------------------- offline (no CorelDRAW)


OFFLINE_MASTERS = [
    ("hangyo_10x3", "real_ma", "10_X_3"), ("hangyo_6x3", "real_ma", "6_X_3"), ("hangyo_4x8", "real_ma", "4_X_8"),
    ("dalmia_120x48", "dumps", "dalmia_master_120x48.json"), ("agarpathi_12x4", "dumps", "agar_16_master_12x4ft.json"),
    ("agarpathi_36x48", "dumps", "agar_76_36x48in.json"),
]
# (label, fx, fy): target = master size x (fx, fy); None = a portrait / landscape swap
OFFLINE_FACTORS = [("same x1.5", 1.5, 1.5), ("same x2", 2.0, 2.0), ("wider 1.2x0.9", 1.2, 0.9), ("wider 1.5x0.8", 1.5, 0.8),
                   ("taller 0.8x1.2", 0.8, 1.2), ("taller 1.0x1.5", 1.0, 1.5), ("wide 2.5x1.0", 2.5, 1.0), ("very wide 3.0x1.2", 3.0, 1.2),
                   ("tall 1.0x2.5", 1.0, 2.5), ("swap (rotate)", None, None)]


def _offline_extraction(spec):
    """An extraction in master_analysis' format for an offline master: the real extraction when we have one, else a cached COM dump of its
    TOP-LEVEL shapes (no nested objects, no PowerClip children: a stated limitation of this offline run)."""
    kind, key = spec[1], spec[2]
    base = ROOT / "backend"
    if kind == "real_ma":
        sp = Path(sys.argv_scratch) if hasattr(sys, "argv_scratch") else None                      # set by cmd_offline
        return json.loads((sp / "real_ma" / key / "extraction.json").read_text(encoding="utf-8"))
    d = json.loads((base / "dataset_analysis" / "dumps" / key).read_text(encoding="utf-8"))
    objs, top = [], -1
    for sh in d["shapes"]:
        if sh.get("group_path"):
            continue
        top += 1
        e = {"id": str(top), "scene_id": None, "parent": None, "children": [], "z": top, "name": sh["name"], "type": sh["type"],
             "kind": "group" if sh["type"] == "group" else "shape", "x": sh["x"], "y": sh["y"], "w": sh["w"], "h": sh["h"],
             "rotation": 0.0, "visible": True, "locked": bool(sh.get("locked"))}
        if sh["type"] == "text":
            e["text"] = {"kind": "artistic", "content": sh.get("text"), "font": sh.get("font"), "size_pt": sh.get("font_size")}
        objs.append(e)
    return {"extractor_version": 1, "corel_version": "cached dump", "page": {"width_mm": d["page_mm"]["w"], "height_mm": d["page_mm"]["h"], "unit": "mm",
            "origin": "bottom-left"}, "layers": [], "shape_sets": {"equal": True}, "objects": objs, "truncated": False, "errors": {}}


def cmd_offline(a):
    """Legacy `layout.compute_layout` vs the same result after `adaptation_plan.apply_to_placed`, on REAL master geometry (real extractions /
    cached COM dumps), many targets. No CorelDRAW: object boxes only - a layout-level comparison, not a rendering."""
    sys.path.insert(0, str(ROOT / "backend"))
    sys.argv_scratch = a.scratch
    from app import adaptation_plan, layout, master_model

    out = {"rows": []}
    for spec in OFFLINE_MASTERS:
        ex = _offline_extraction(spec)
        model = master_model.build_model(ex, "offline")
        pw, ph = model.page_w, model.page_h
        objs = [layout.Obj(o.id, o.name, master_model._layout_kind(o), o.box_mm["x"], o.box_mm["y"], o.box_mm["width"], o.box_mm["height"],
                           (o.text or {}).get("content"), master_model._cmyk_of(o), o.n_descendants if o.type == "group" else 0) for o in model.top_level()]
        for label, fx, fy in OFFLINE_FACTORS:
            tw, th = (ph, pw) if fx is None else (pw * fx, ph * fy)
            row = {"master": spec[0], "master_mm": [round(pw), round(ph)], "target": label, "target_mm": [round(tw), round(th)], "objects": len(objs)}
            try:
                placed = layout.compute_layout(objs, pw, ph, tw, th, tile=True)
                legacy = [p.to_dict() for p in placed]
                summary = adaptation_plan.apply_to_placed(placed, model, objs, tw, th, page_w=pw, page_h=ph)
                after = [p.to_dict() for p in placed]
            except Exception as e:  # noqa: BLE001
                row["error"] = f"{type(e).__name__}: {e}"
                out["rows"].append(row)
                continue
            by = {p["id"]: p for p in legacy}
            diffs = []
            for p in after:
                q = by.get(p["id"])
                if not q:
                    continue
                d = {k: round(p[k] - q[k], 2) for k in ("x", "y", "w", "h")}
                if max(abs(v) for v in d.values()) > 0.5:
                    diffs.append({"id": p["id"], "role": p["role"], "name": p["name"], "delta_mm": d})
            ml, mp = board_metrics(legacy, tw, th), board_metrics(after, tw, th)
            vl = lambda m: len(m["stretched"]) + len(m["out_of_page"]) + len(m["new_overlaps"]) + (0 if m["bg_ok"] in (True, None) else 1)  # noqa: E731
            plan = adaptation_plan.plan_adaptation(model, tw, th)
            row.update(strategy=summary.get("strategy") or plan.strategy, used=summary.get("used"), reason=summary.get("reason"),
                       changed=len(diffs), max_shift_mm=max((max(abs(v) for v in x["delta_mm"].values()) for x in diffs), default=0),
                       violations_legacy=vl(ml), violations_planner=vl(mp), legacy_detail={k: v for k, v in ml.items() if v and k != "edge"},
                       planner_detail={k: v for k, v in mp.items() if v and k != "edge"}, diffs=diffs[:12],
                       plan_issues=[(v["severity"], v["rule"]) for v in plan.validation])
            out["rows"].append(row)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in out["rows"]:
        print(f"{r['master']:16} {r['target']:18} {str(r.get('target_mm')):14} {str(r.get('strategy')):8} used={str(r.get('used')):5} changed={r.get('changed')} "
              f"viol {r.get('violations_legacy')}->{r.get('violations_planner')} {('| ' + r['reason'][:70]) if r.get('reason') else ''} {r.get('error', '')}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--base", required=True); r.add_argument("--out", required=True); r.set_defaults(fn=cmd_run)
    c = sub.add_parser("compare"); c.add_argument("--off", required=True); c.add_argument("--on", required=True)
    c.add_argument("--on-data", required=True); c.add_argument("--out", required=True); c.set_defaults(fn=cmd_compare)
    v = sub.add_parser("verify"); v.add_argument("--dir", required=True); v.add_argument("--dump", action="store_true"); v.set_defaults(fn=cmd_verify)
    o = sub.add_parser("offline"); o.add_argument("--scratch", required=True); o.add_argument("--out", required=True); o.set_defaults(fn=cmd_offline)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
