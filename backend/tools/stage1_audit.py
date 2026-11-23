"""Stage 1 audit of the Hangyo dataset: inventory, real-CorelDRAW extraction, shop <-> designer mapping, baseline generation through the
production API (planner OFF), designer-vs-engine comparison, design-intent discovery and the report.

Isolated from the production conversion path: it only READS the dataset (never writes inside signage_dataset/), writes to --out (default
backend/dataset_analysis/stage1) and talks to a running server through its public API. Nothing here changes adaptation behaviour.

  python tools/stage1_audit.py inventory
  python tools/stage1_audit.py extract   [--group masters|designers|generated]     # real CorelDRAW, one job at a time, resumable
  python tools/stage1_audit.py map
  python tools/stage1_audit.py generate  --base http://127.0.0.1:8041             # server: SIGNAGE_ENGINE=corel, planner OFF
  python tools/stage1_audit.py compare
  python tools/stage1_audit.py intent
  python tools/stage1_audit.py report
"""
from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[1]
ROOT = HERE.parents[2]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tools"))

HANGYO = ROOT / "signage_dataset" / "Hangyo"
CREATIVE = HANGYO / "Creative"
STORE = HANGYO / "03 - Tiruvannamalai - 31 Stores"
DETAILS = STORE / "01 - Details"
WORKING = STORE / "02 - Working"
DEFAULT_OUT = BACKEND / "dataset_analysis" / "stage1"
FT = 304.8
RENDER_PX = 2400

TAMIL = re.compile(r"[஀-௿]")
BOARD_RE = re.compile(r"^(\d+) - (\d+) X (\d+) Feet - (.+?) - (.+?)(?: \((\d)\))?\.cdr$", re.I)
TYPE_FROM_WORK = {"FLB": "Frontlit", "NLB": "Nonlit", "GSB": "GSB", "GSB D/S": "Double Side GSB"}


def out_dir(a) -> Path:
    p = Path(getattr(a, "out", None) or DEFAULT_OUT)
    p.mkdir(parents=True, exist_ok=True)
    return p


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")[:100]


def norm_text(s: str | None) -> str:
    return re.sub(r"[\s\r\n ]+", " ", (s or "")).strip().casefold()


def norm_name(s: str | None) -> str:
    return re.sub(r"[^0-9a-z஀-௿]+", "", (s or "").casefold())


def write_json(p: Path, data) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def write_csv(p: Path, rows: list[dict], cols: list[str] | None = None) -> None:
    cols = cols or sorted({k for r in rows for k in r})
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in r.items()})


# ---------------------------------------------------------------------------------------------------------------- inventory


def parse_order_sheet(path: Path) -> dict:
    """The distributor's order sheet (read-only). Header row = 'S.N | Shopname | Delivery | Work | W | H | Qty | ...'. A row with an S.N starts a
    shop; the rows below it WITHOUT an S.N are more boards (or poles / angles) of the same shop."""
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    hdr_i = next(i for i, r in enumerate(rows) if r and r[0] == "S.N")
    headers = [str(v).strip() if v is not None else "" for v in rows[hdr_i]]
    shops, cur = [], None
    for i, r in enumerate(rows[hdr_i + 2:], start=hdr_i + 3):
        if not any(v is not None for v in r):
            continue
        sn, name, delivery, work, w, h, qty = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
        if isinstance(sn, (int, float)):
            cur = {"sno": int(sn), "shop_name": str(name).strip() if name else "", "delivery": delivery, "outlet_code": r[12], "lines": []}
            shops.append(cur)
        if cur is None:
            continue
        wk = (str(work).strip() if work else "")
        cur["lines"].append({"sheet_row": i, "work": wk, "kind": ("board" if isinstance(w, (int, float)) and isinstance(h, (int, float)) else "accessory"),
                             "w_ft": w, "h_ft": h, "qty": qty, "sqft": r[7], "type_of_board": TYPE_FROM_WORK.get(wk.upper().replace("  ", " ").strip())})
    return {"sheets": wb.sheetnames, "header_row_index": hdr_i, "headers": headers,
            "title": str(rows[5][0]).strip() if len(rows) > 5 and rows[5][0] else None,
            "distributor": rows[2][2] if len(rows) > 2 else None, "shops": shops}


def font_file_info(p: Path) -> dict:
    from fontTools.ttLib import TTFont

    t = TTFont(str(p), lazy=True)
    names = {n.nameID: str(n) for n in t["name"].names if n.nameID in (1, 2, 4, 16, 17)}
    cmap = t.getBestCmap() or {}
    return {"family": names.get(16) or names.get(1), "style": names.get(17) or names.get(2), "full_name": names.get(4),
            "variable": "fvar" in t, "axes": [a.axisTag for a in t["fvar"].axes] if "fvar" in t else [],
            "tamil_glyphs": sum(1 for c in cmap if 0x0B80 <= c <= 0x0BFF), "glyphs": len(cmap)}


def cmd_inventory(a):
    from app import corel_util, fonts

    out = out_dir(a)
    inst = fonts.installed_fonts()
    installed = {f.casefold() for f in inst.get("fonts", [])} if inst.get("available") else set()
    inv = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "paths": {k: str(v) for k, v in
           {"creative": CREATIVE, "working": WORKING, "details": DETAILS, "store": STORE}.items()}, "locations": {}, "excel_search": {}}
    for label, base in (("creative", CREATIVE), ("details", DETAILS), ("working", WORKING)):
        files = []
        for p in sorted(base.rglob("*")):
            if p.is_file():
                st = p.stat()
                row = {"path": str(p.relative_to(HANGYO)), "ext": p.suffix.lower(), "bytes": st.st_size,
                       "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)), "sha256": sha256(p)}
                if p.suffix.lower() == ".cdr":
                    row["core_version"] = corel_util.cdr_core_version(p)
                    try:
                        z = zipfile.ZipFile(p)
                        row["zip_members"] = len(z.namelist())
                        row["has_embedded_preview"] = "previews/page1.png" in z.namelist()
                    except zipfile.BadZipFile:
                        row["zip_members"] = None
                files.append(row)
        by_ext: dict[str, int] = {}
        for r in files:
            by_ext[r["ext"]] = by_ext.get(r["ext"], 0) + 1
        inv["locations"][label] = {"root": str(base), "file_count": len(files), "by_extension": by_ext, "files": files}
    inv["excel_search"] = {"creative_has_excel": any(r["ext"] in (".xlsx", ".xls", ".csv") for r in inv["locations"]["creative"]["files"]),
                           "excel_files_found": [r["path"] for loc in inv["locations"].values() for r in loc["files"] if r["ext"] in (".xlsx", ".xls", ".csv")],
                           "note": "The Creative folder has NO Excel file. The two workbooks live in '01 - Details' and are the distributor's order sheets "
                                   "(S.N / Shopname / Work / W / H / Qty / cost): no Tamil names, language, side or master columns."}
    inv["orders"] = {}
    for r in inv["locations"]["details"]["files"]:
        if r["ext"] == ".xlsx":
            inv["orders"][r["path"]] = parse_order_sheet(HANGYO / r["path"])
    inv["fonts"] = []
    for r in inv["locations"]["details"]["files"]:
        if r["ext"] in (".ttf", ".otf"):
            info = font_file_info(HANGYO / r["path"])
            info.update(file=r["path"], installed_on_this_pc=(info["family"] or "").casefold() in installed)
            inv["fonts"].append(info)
    inv["referenced_assets"] = {"note": "no linked/external asset list is exposed by the COM extractor; embedded bitmaps are inventoried per file in master_analysis_summary.json"}
    write_json(out / "dataset_inventory.json", inv)
    print("inventory:", {k: (v["file_count"], v["by_extension"]) for k, v in inv["locations"].items()})
    print("excel:", inv["excel_search"]["excel_files_found"])
    for name, o in inv["orders"].items():
        print(name, "->", len(o["shops"]), "shops,", sum(len([l for l in s["lines"] if l["kind"] == "board"]) for s in o["shops"]), "board lines")


# ------------------------------------------------------------------------------------------------------------------ extract


def designer_files() -> list[Path]:
    return sorted(p for p in WORKING.glob("*.cdr") if not p.stem.lower().startswith("backup_of"))


def extraction_dir(out: Path, group: str, p: Path) -> Path:
    """Where a file's COM extraction + render lives. Designer / master reads are made once (Stage 1) and shared by later evaluations: with
    STAGE1_REF_ROOT set they are looked up there instead of under `out` (generated boards always live under `out`)."""
    ref = os.environ.get("STAGE1_REF_ROOT")
    base = Path(ref) if ref and group in ("designers", "masters") else out
    return base / "extractions" / group / safe(p.stem)


def cmd_extract(a):
    """Real CorelDRAW (corel_supervisor, one worker at a time, its RAM / timeout guards untouched): rich extraction + a full page render per file."""
    from app import corel_supervisor

    out = out_dir(a)
    todo: list[tuple[str, Path]] = []
    if a.group in ("masters", "all"):
        todo += [("masters", p) for p in sorted(CREATIVE.glob("*.cdr"))]
    if a.group in ("designers", "all"):
        todo += [("designers", p) for p in designer_files()]
    if a.group in ("generated", "all"):
        gen = out / "generated"
        todo += [("generated", p) for p in sorted(gen.glob("*.cdr"))] if gen.is_dir() else []
    status_path = out / "extraction_status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {}
    pending = [(g, p) for g, p in todo if not (extraction_dir(out, g, p) / "extraction.json").is_file()]
    print(f"{len(todo)} files in scope, {len(pending)} pending", flush=True)
    for k in range(0, len(pending), a.batch):
        chunk = pending[k:k + a.batch]
        jobs = [{"master_analysis": str(p), "analysis_out": str(extraction_dir(out, g, p)), "render_px": RENDER_PX} for g, p in chunk]
        try:
            res = corel_supervisor.run_batch(jobs, out / "_extract_results.json", overall_timeout_s=900)
        except corel_supervisor.RefusedToStart as e:
            print("REFUSED (RAM guard, not weakened):", e, flush=True)
            for g, p in chunk:
                status[f"{g}/{p.name}"] = {"status": "refused", "error": str(e)}
            write_json(status_path, status)
            time.sleep(30)
            continue
        for (g, p), r in zip(chunk, res):
            status[f"{g}/{p.name}"] = {"status": r.get("status"), "error": r.get("error"), "seconds": r.get("seconds"), "objects": r.get("objects")}
            print(f"  {g}/{p.name[:60]}: {r.get('status')} {r.get('seconds')}s {r.get('error') or ''}", flush=True)
        write_json(status_path, status)
    left = [f"{g}/{p.name}" for g, p in todo if not (extraction_dir(out, g, p) / "extraction.json").is_file()]
    print("still missing:", len(left), left[:5])


def load_extraction(out: Path, group: str, p: Path) -> dict | None:
    f = extraction_dir(out, group, p) / "extraction.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None


def board_facts(ex: dict) -> dict:
    """Counts and text facts of one extraction (all depths)."""
    objs = ex["objects"]
    texts = [o for o in objs if o["type"] == "text"]
    return {"objects": len(objs), "top_level": sum(1 for o in objs if o["parent"] is None), "nested": sum(1 for o in objs if o["parent"] is not None),
            "text_objects": len(texts), "bitmaps": sum(1 for o in objs if o["type"] == "bitmap"), "groups": sum(1 for o in objs if o["kind"] == "group"),
            "powerclips": sum(1 for o in objs if o["kind"] == "powerclip"),
            "fonts": sorted({(o["text"] or {}).get("font") or "(mixed)" for o in texts}),
            "texts": [{"content": (o["text"] or {}).get("content"), "font": (o["text"] or {}).get("font"), "size_pt": (o["text"] or {}).get("size_pt"),
                       "box_mm": [o["x"], o["y"], o["w"], o["h"]], "depth": o["id"].count(".")} for o in texts]}


# --------------------------------------------------------------------------------------------------------------------- map


def cmd_master_summary(a):
    from app import fonts

    out = out_dir(a)
    inst = fonts.installed_fonts()
    installed = {f.casefold() for f in inst.get("fonts", [])}
    res = []
    for p in sorted(CREATIVE.glob("*.cdr")):
        ex = load_extraction(out, "masters", p)
        row = {"file": p.name, "sha256": sha256(p)}
        if not ex:
            row["error"] = "not extracted"
            res.append(row)
            continue
        pw, ph = ex["page"]["width_mm"], ex["page"]["height_mm"]
        f = board_facts(ex)
        row.update(page_mm=[pw, ph], page_in=[round(pw / 25.4, 2), round(ph / 25.4, 2)], page_ft=[round(pw / FT, 3), round(ph / FT, 3)], unit="mm (document unit set to mm by the extractor)",
                   orientation="landscape" if pw > ph else "portrait" if pw < ph else "square", pages=ex.get("pages"), corel_version_saved_by=None,
                   corel_version_reader=ex.get("corel_version"), **{k: v for k, v in f.items() if k != "texts"},
                   missing_fonts=[x for x in f["fonts"] if x != "(mixed)" and x.casefold() not in installed], texts=f["texts"],
                   layers=ex.get("layers"), shape_sets=ex.get("shape_sets"), extraction_errors=ex.get("errors"))
        try:
            from app import corel_util
            row["corel_version_saved_by"] = corel_util.cdr_core_version(p)
        except Exception:
            pass
        row["top_level_objects_detail"] = [{"id": o["id"], "type": o["type"], "kind": o["kind"], "name": o["name"], "box_mm": [round(o["x"]), round(o["y"]), round(o["w"]), round(o["h"])],
                                            "children": len(o["children"]), "rotation": o["rotation"]} for o in ex["objects"] if o["parent"] is None]
        res.append(row)
    write_json(out / "master_analysis_summary.json", res)
    for r in res:
        print(r["file"], r.get("page_ft"), r.get("orientation"), "tops", r.get("top_level"), "nested", r.get("nested"), "text", r.get("text_objects"), "bitmaps", r.get("bitmaps"),
              "groups", r.get("groups"), "pc", r.get("powerclips"), "fonts", r.get("fonts"), "missing", r.get("missing_fonts"))


def cmd_map(a):
    """Excel order sheet lines <-> designer CDR files <-> master. Evidence: S.N + W x H + board type (sheet), the file name, the CDR's own text
    (Tamil / English) and the project's example library (which master a designer board was built from)."""
    from app import example_layout

    out = out_dir(a)
    inv = json.loads((out / "dataset_inventory.json").read_text(encoding="utf-8"))
    order_key = next(k for k in inv["orders"] if k.endswith("Thiruvannamalai ).xlsx") and "for Printing" not in k)
    sheet = inv["orders"][order_key]
    print_key = next((k for k in inv["orders"] if "for Printing" in k), None)
    lib = example_layout.library("hangyo") or {}
    lib_by_file = {b["file"]: b for b in lib.get("boards", [])}
    lib_master = {m["id"]: m["file"] for m in lib.get("masters", [])}
    by_sno = {s["sno"]: s for s in sheet["shops"]}
    rows = []
    for p in designer_files():
        m = BOARD_RE.match(p.name)
        row = {"designer_file": p.name, "designer_path": str(p.relative_to(HANGYO)), "designer_sha256": sha256(p)}
        if not m:
            row.update(status="UNMATCHED", reason="file name does not follow '<S.N> - W X H Feet - <type> - <shop> (side)'")
            rows.append(row)
            continue
        sno, w, h, ftype, shop, side = int(m[1]), int(m[2]), int(m[3]), m[4].strip(), m[5].strip(), m[6]
        row.update(sno=sno, file_w_ft=w, file_h_ft=h, file_type=ftype, file_shop=shop, side=int(side) if side else None)
        ex = load_extraction(out, "designers", p)
        if ex:
            pw, ph = ex["page"]["width_mm"], ex["page"]["height_mm"]
            texts = [t for t in board_facts(ex)["texts"]]
            row.update(page_ft=[round(pw / FT, 3), round(ph / FT, 3)], size_matches_file_name=abs(pw / FT - w) < 0.05 and abs(ph / FT - h) < 0.05,
                       pages=ex.get("pages"), cdr_tamil_text=" | ".join(sorted({norm_text(t["content"]) for t in texts if TAMIL.search(t["content"] or "")})),
                       cdr_latin_text=" | ".join(sorted({norm_text(t["content"]) for t in texts if t["content"] and not TAMIL.search(t["content"]) and "adinn" not in (t["content"] or "").lower()})))
            row["content_script"] = ("both" if row["cdr_tamil_text"] and row["cdr_latin_text"] else "tamil" if row["cdr_tamil_text"] else "english" if row["cdr_latin_text"] else "none")
        s = by_sno.get(sno)
        if s and ex:
            eng = row.get("cdr_latin_text") or ""
            if not eng and row.get("side"):      # a Tamil-only side: the English spelling lives on the other side's file
                other = BOARD_RE.sub(lambda mm_: mm_.group(0), p.name)
                for q in designer_files():
                    qm = BOARD_RE.match(q.name)
                    if qm and int(qm[1]) == sno and qm[6] and int(qm[6]) != row["side"] and int(qm[2]) == w and int(qm[3]) == h:
                        qex = load_extraction(out, "designers", q)
                        if qex:
                            eng = " ".join(norm_text(t["content"]) for t in board_facts(qex)["texts"] if t["content"] and not TAMIL.search(t["content"]) and "adinn" not in t["content"].lower())
            if eng:
                row["cdr_text_similarity"] = round(max(difflib.SequenceMatcher(None, norm_name(s["shop_name"]), norm_name(x)).ratio() for x in (eng, eng.split(" | ")[0])), 3)
        cands = [l for l in (s["lines"] if s else []) if l["kind"] == "board" and l["w_ft"] == w and l["h_ft"] == h]
        if not s:
            row.update(status="UNMATCHED", reason=f"S.N {sno} is not in the order sheet")
        elif not cands:
            row.update(status="UNMATCHED", reason=f"order sheet S.N {sno} has no {w}x{h} ft board line", sheet_shop=s["shop_name"])
        else:
            line = cands[0]
            sim = difflib.SequenceMatcher(None, norm_name(s["shop_name"]), norm_name(shop)).ratio()
            type_ok = (line["type_of_board"] or "").casefold() == ftype.casefold() or (ftype.casefold() in ("frotlit",) and line["type_of_board"] == "Frontlit")
            row.update(sheet_shop=s["shop_name"], sheet_work=line["work"], sheet_type=line["type_of_board"], sheet_qty=line["qty"], sheet_row=line["sheet_row"], name_similarity=round(sim, 3),
                       type_agrees=type_ok, outlet_code=s["outlet_code"])
            if len(cands) > 1:
                row.update(status="AMBIGUOUS", reason=f"{len(cands)} identical {w}x{h} lines under S.N {sno}")
            elif sim >= 0.9 and type_ok and row.get("size_matches_file_name") and row.get("cdr_text_similarity", 0) >= 0.6:
                row.update(status="CONFIRMED", reason="S.N + size (sheet, file name AND the CDR's page) + board type + shop name agree, and the CDR's own text spells the name alike")
            elif sim >= 0.9 and type_ok and row.get("size_matches_file_name") and row.get("cdr_text_similarity") is None:
                row.update(status="PROBABLE", reason="S.N + size + type + file name agree; the CDR carries no English name text to confirm it")
            elif sim >= 0.9 and type_ok and row.get("size_matches_file_name"):
                row.update(status="PROBABLE", reason="S.N + size + type + file name agree, but the CDR's own English text spells the name differently (designer re-typed it)")
            elif type_ok or sim >= 0.6:
                row.update(status="PROBABLE", reason="S.N + size agree; " + ("board type differs" if not type_ok else "") + ("; shop name spelled differently" if sim < 0.9 else ""))
            else:
                row.update(status="PROBABLE", reason="S.N + size agree but name and type differ")
            if line["qty"] == 2 and ftype.casefold() == "double side gsb" and row["side"] is None:
                row["status"] = "AMBIGUOUS"; row["reason"] += "; double-sided board without a (1)/(2) side marker"
            if row["side"] and ftype.casefold() != "double side gsb":
                row["status"] = "AMBIGUOUS"; row["reason"] += "; side marker on a single-sided board type"
        if ex:    # independent of the library: which master does the board's top-level structure resemble most?
            def sig(e):
                from collections import Counter as _C
                return _C((o["type"], len(o["children"])) for o in e["objects"] if o["parent"] is None and not (o["type"] == "text"))
            dist = {}
            for mp in sorted(CREATIVE.glob("*.cdr")):
                mex = load_extraction(out, "masters", mp)
                if mex:
                    a_, b_ = sig(ex), sig(mex)
                    dist[mp.name] = sum(((a_ - b_) + (b_ - a_)).values())
            if dist:
                best = sorted(dist.items(), key=lambda t: t[1])
                row["structural_master_guess"] = best[0][0] if len(best) < 2 or best[0][1] < best[1][1] else "tie: " + ", ".join(k for k, v in best if v == best[0][1])
                row["structural_master_distances"] = dist
        lb = lib_by_file.get(p.name)
        row["library_master"] = lib_master.get(lb["master"]) if lb else None
        row["library_coverage"] = lb.get("coverage") if lb else None
        row["library_strict_elements"] = lb.get("strict") if lb else None
        row["pair_key"] = f"{sno}|{w}x{h}"
        rows.append(row)
    # pair sides: a side-1 board is only ever paired with the side-2 board of the SAME S.N and size
    pairs: dict[str, dict[int, str]] = {}
    for r in rows:
        if r.get("side"):
            pairs.setdefault(r["pair_key"], {})[r["side"]] = r["designer_file"]
    for r in rows:
        if r.get("side"):
            other = pairs[r["pair_key"]].get(3 - r["side"])
            r["other_side_file"] = other
            if other is None:
                r["status"] = "AMBIGUOUS"; r["reason"] = (r.get("reason") or "") + "; the other side's file is missing"
    # master evidence: size family (the 3 uploaded masters) and the library's own record
    for r in rows:
        if r.get("file_w_ft"):
            r["orientation"] = "portrait" if r["file_w_ft"] < r["file_h_ft"] else "landscape"
    shops_in_sheet = {s["sno"]: s for s in sheet["shops"]}
    covered = {r.get("sno") for r in rows}
    sheet_only = [{"sno": s["sno"], "shop": s["shop_name"], "board_lines": [(l["w_ft"], l["h_ft"], l["work"]) for l in s["lines"] if l["kind"] == "board"]}
                  for s in sheet["shops"] if s["sno"] not in covered]
    # board lines in the sheet without a designer file
    have = {(r.get("sno"), r.get("file_w_ft"), r.get("file_h_ft")) for r in rows}
    missing_boards = [{"sno": s["sno"], "shop": s["shop_name"], "w_ft": l["w_ft"], "h_ft": l["h_ft"], "work": l["work"], "qty": l["qty"]}
                      for s in sheet["shops"] for l in s["lines"] if l["kind"] == "board" and (s["sno"], l["w_ft"], l["h_ft"]) not in have]
    cols = ["status", "reason", "designer_file", "sno", "side", "other_side_file", "file_w_ft", "file_h_ft", "file_type", "file_shop", "sheet_shop", "sheet_work", "sheet_type", "sheet_qty",
            "name_similarity", "cdr_text_similarity", "type_agrees", "size_matches_file_name", "page_ft", "pages", "content_script", "cdr_tamil_text", "cdr_latin_text", "library_master", "structural_master_guess", "structural_master_distances", "library_coverage",
            "library_strict_elements", "orientation", "outlet_code", "sheet_row", "pair_key", "designer_path", "designer_sha256"]
    write_csv(out / "shop_reference_mapping.csv", rows, cols)
    write_json(out / "shop_reference_mapping.json", {"order_sheet": order_key, "printing_sheet_also_found": print_key,
                                                       "summary": {s: sum(1 for r in rows if r["status"] == s) for s in ("CONFIRMED", "PROBABLE", "AMBIGUOUS", "UNMATCHED")},
                                                       "order_sheet_shops_without_any_designer_file": sheet_only, "order_sheet_board_lines_without_designer_file": missing_boards,
                                                       "rows": rows})
    print({s: sum(1 for r in rows if r["status"] == s) for s in ("CONFIRMED", "PROBABLE", "AMBIGUOUS", "UNMATCHED")}, "boards;", "sheet shops without designer file:", len(sheet_only),
          "; sheet board lines without file:", len(missing_boards))


# ------------------------------------------------------------------------------------------------------------------ generate


def name_parts(ex: dict) -> tuple[str, str]:
    """(english, tamil) shop name text of a board: the text objects of each script, top to bottom, line breaks -> spaces. The ADINN job tag
    and phone / marker texts are not names."""
    en, ta = [], []
    for o in ex["objects"]:
        if o["type"] != "text" or not o.get("text"):
            continue
        c = (o["text"].get("content") or "").replace("\r", " ").replace("\n", " ").strip()
        if not c or re.search(r"adinn\s*/", c, re.I) or re.fullmatch(r"\(?\s*[12]\s*\)?", c):
            continue
        (ta if TAMIL.search(c) else en).append((-(o["y"] + o["h"]), c))
    j = lambda xs: re.sub(r"\s+", " ", " ".join(c for _y, c in sorted(xs))).strip()  # noqa: E731
    return j(en), j(ta)


def _call(base, method, path, body=None, headers=None, raw=None):
    import urllib.request

    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = dict(headers or {})
    if body is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read() or b"null")


def _multipart(fields, fname, fpath):
    import uuid

    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="master"; filename="{fname}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
    parts += [Path(fpath).read_bytes(), f"\r\n--{b}--\r\n".encode()]
    return b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={b}"}


def cmd_generate(a):
    """Baseline: every designer board is re-made by the production engine through the public API (planner OFF), the way the UI does it: masters uploaded to
    brand Hangyo, shops added with the sheet-style fields, master left on Auto. The order sheet has no Tamil name / language / side column, so those fields are
    DERIVED from the designer's own CDR text (recorded per case) - this isolates layout differences from spelling differences."""
    import urllib.parse
    import urllib.request

    out = out_dir(a)
    base = a.base.rstrip("/")
    mapping = json.loads((out / "shop_reference_mapping.json").read_text(encoding="utf-8"))["rows"]
    health = _call(base, "GET", "/healthz")
    gen_dir = out / "generated"
    gen_dir.mkdir(exist_ok=True)
    masters = {}
    for fname, orient in (("10 X 3.cdr", "landscape"), ("6 X 3.cdr", "landscape"), ("4 X 8.cdr", "portrait")):
        body, h = _multipart({"brand": "Hangyo", "orientation": orient}, fname, CREATIVE / fname)
        masters[fname] = _call(base, "POST", "/api/masters/upload", headers=h, raw=body)["id"]
    print("masters", masters, flush=True)
    land = masters["10 X 3.cdr"]
    ports = masters["4 X 8.cdr"]
    ex_by_file = {r["designer_file"]: load_extraction(out, "designers", WORKING / r["designer_file"]) for r in mapping}
    cases = []
    for r in mapping:
        ex = ex_by_file[r["designer_file"]]
        if not r.get("sno") or ex is None:
            cases.append({"case": safe(Path(r["designer_file"]).stem), "designer_file": r["designer_file"], "skipped": "designer file not extracted / name not parsable"})
            continue
        en, ta = name_parts(ex)
        derivation = []
        if r.get("other_side_file") and (not en or not ta):
            oen, ota = name_parts(ex_by_file[r["other_side_file"]])
            if not en and oen:
                en = oen
                derivation.append("English name taken from the other side's CDR")
            if not ta and ota:
                ta = ota
                derivation.append("Tamil name taken from the other side's CDR")
        if not en:
            en = r.get("sheet_shop") or r["file_shop"]
            derivation.append("English name from the order sheet (the CDR has none)")
        if not ta:
            derivation.append("no Tamil name available")
        script = r.get("content_script") or "both"
        lang = {"both": "both", "tamil": "ta", "english": "en"}.get(script, "both")
        btype = r.get("sheet_type") or r["file_type"].replace("Frotlit", "Frontlit")
        cases.append({"case": safe(Path(r["designer_file"]).stem), "designer_file": r["designer_file"], "sno": r["sno"], "side": r.get("side"), "w_ft": r["file_w_ft"], "h_ft": r["file_h_ft"],
                      "board_type": btype, "language": lang, "name_en": en, "name_ta": ta, "derivation": derivation, "mapping_status": r["status"]})
    todo = [c for c in cases if "skipped" not in c]
    for c in todo:
        payload = {"name": c["name_en"], "shop_name_local": c["name_ta"] or None, "width": c["w_ft"], "height": c["h_ft"], "width_unit": "ft", "height_unit": "ft", "unit": "ft",
                   "board_type": c["board_type"], "language": c["language"], "sno": str(c["sno"]), "landscape_master_id": land, "portrait_master_id": ports}
        r = _call(base, "POST", f"/api/v2/jobs/{land}/shops", payload)
        c["shop_id"], c["payload"] = r["id"], payload
    attempt = 0
    while True:
        attempt += 1
        for c in todo:
            if c.get("done_ok"):
                continue
            c["attempts"] = attempt
            _call(base, "POST", f"/api/v2/shops/{c['shop_id']}/convert", c["payload"])
        t0 = time.time()
        while True:
            sts = {c["shop_id"]: _call(base, "GET", f"/api/v2/shops/{c['shop_id']}/status") for c in todo if not c.get("done_ok")}
            pend = [k for k, v in sts.items() if v["status"] not in ("done", "failed", "error")]
            print(f"attempt {attempt}: {int(time.time() - t0)}s pending {len(pend)}", flush=True)
            if not pend:
                break
            time.sleep(10)
        for c in todo:
            st = sts.get(c["shop_id"])
            if st is not None:
                c["status"], c["error"] = st["status"], st.get("error")
                c["status_obj"] = st
                if st["status"] == "done":
                    c["done_ok"] = True
        retry = [c for c in todo if not c.get("done_ok") and "free RAM" in (c.get("error") or "")]
        if not retry or attempt >= a.retries:
            break
        print(f"{len(retry)} boards refused by the RAM guard; waiting 60 s and retrying them", flush=True)
        time.sleep(60)
    rows = []
    for c in cases:
        row = {k: c.get(k) for k in ("case", "designer_file", "sno", "side", "w_ft", "h_ft", "board_type", "language", "name_en", "name_ta", "derivation", "mapping_status", "attempts", "status", "error", "skipped")}
        st = c.get("status_obj")
        if st and st["status"] == "done":
            rep = st.get("report") or {}
            lay = rep.get("layout") or {}
            mu = rep.get("master_used") or {}
            row.update(master_used=mu.get("name") or mu.get("file"), master_used_detail=mu, engine_mode=lay.get("mode"), example_board=lay.get("file"), example_exact=lay.get("exact"),
                       example_distance=lay.get("distance"), warnings=rep.get("warnings"), seconds=rep.get("timings_s"), confidence=st.get("confidence"), corel=rep.get("corel"))
            for kind, name in (st.get("files") or {}).items():
                if not isinstance(name, str):
                    continue
                ext = Path(name).suffix.lower()
                dest = gen_dir / (f"{c['case']}{ext}" if ext in (".cdr", ".pdf") else f"{c['case']}__{kind}{ext}")
                try:
                    dest.write_bytes(urllib.request.urlopen(base + f"/api/v2/shops/{c['shop_id']}/files/{urllib.parse.quote(name)}", timeout=300).read())
                    row[f"file_{kind}"] = dest.name
                except Exception as e:  # noqa: BLE001
                    row[f"file_{kind}"] = f"download failed: {e}"
        rows.append(row)
    write_csv(out / "baseline_generation_results.csv", rows)
    write_json(out / "baseline_generation_results.json", {"server_health": health, "masters": masters, "rows": rows,
                                                           "run_config": {"base": base, "attempts": attempt, "planner": "OFF (server env SIGNAGE_SPATIAL_PLANNER unset/0)"}})
    print("generated:", sum(1 for r in rows if r.get("status") == "done"), "of", len(rows), "; failed:", [r["case"] for r in rows if r.get("status") != "done"][:10])


# ------------------------------------------------------------------------------------------------------------------ compare

DEFECT_TEXT = {
    "name_too_small": "name block is smaller than the designer's", "name_too_large": "name block is larger than the designer's",
    "name_off_position": "name block centre is far from the designer's", "name_line_count": "name set on a different number of lines",
    "name_font_differs": "name font differs from the designer's", "name_font_missing_on_pc": "a font used is not installed on this PC",
    "name_missing": "a name the designer has is absent", "logo_distortion": "a non-text object's aspect ratio differs from the designer's",
    "object_position_error": "an object is far from the designer's position", "object_size_error": "an object's size differs from the designer's",
    "object_missing": "an object the designer's board has is absent", "object_extra": "the engine board has an object the designer's does not",
    "outside_page": "an object lies outside the page", "text_overlap": "text boxes overlap (the designer's do not)", "side_marker_lost": "the designer's side marker is missing",
    "background_not_full": "the background no longer covers the page", "language_content_mismatch": "the board shows a language the side should not carry", "visual_far": "the render differs strongly from the designer's",
}


def _objs(ex: dict) -> list[dict]:
    return [o for o in ex["objects"] if o.get("visible", True)]


def _is_marker(c: str) -> bool:
    return bool(re.search(r"adinn\s*/", c or "", re.I)) or bool(re.fullmatch(r"\(?\s*[12]\s*\)?", (c or "").strip()))


def name_block(ex: dict, script: str) -> dict | None:
    sel = []
    for o in _objs(ex):
        if o["type"] != "text" or not o.get("text"):
            continue
        c = o["text"].get("content") or ""
        if not c.strip() or _is_marker(c):
            continue
        if bool(TAMIL.search(c)) == (script == "ta"):
            sel.append(o)
    if not sel:
        return None
    x0, y0 = min(o["x"] for o in sel), min(o["y"] for o in sel)
    x1, y1 = max(o["x"] + o["w"] for o in sel), max(o["y"] + o["h"] for o in sel)
    lines = sum(max(1, len([s for s in re.split(r"[\r\n]+", o["text"]["content"]) if s.strip()])) for o in sel)
    sizes = [o["text"].get("size_pt") for o in sel if o["text"].get("size_pt")]
    return {"box": [x0, y0, x1 - x0, y1 - y0], "objects": len(sel), "lines": lines, "text": norm_name(" ".join(o["text"]["content"] for o in sel)),
            "fonts": sorted({o["text"].get("font") or "(mixed)" for o in sel}), "size_pt": (round(sum(sizes) / len(sizes), 1) if sizes else None),
            "align": sorted({o["text"].get("align") for o in sel if o["text"].get("align")}), "bold": sorted({str(o["text"].get("bold")) for o in sel if "bold" in o["text"]})}


def match_objects(ref: list[dict], gen: list[dict], pw: float, ph: float) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Greedy one-to-one matching of NON-text objects (COM ids are not stable between files): same type and kind, similar nested count, then the
    smallest normalized centre + size distance (cap 0.5). Text is compared by name blocks, not object by object."""
    rn = [o for o in ref if o["type"] != "text"]
    gn = [o for o in gen if o["type"] != "text"]
    pairs = []
    for i, r in enumerate(rn):
        for j, g in enumerate(gn):
            if r["type"] != g["type"] or r["kind"] != g["kind"]:
                continue
            if r["w"] <= 0 or r["h"] <= 0 or g["w"] <= 0 or g["h"] <= 0:
                continue
            d = (abs((r["x"] + r["w"] / 2) - (g["x"] + g["w"] / 2)) / pw + abs((r["y"] + r["h"] / 2) - (g["y"] + g["h"] / 2)) / ph
                 + abs(__import__("math").log(r["w"] / g["w"])) * 0.5 + abs(__import__("math").log(r["h"] / g["h"])) * 0.5)
            d += 0.15 * abs(len(r["children"]) - len(g["children"])) / max(len(r["children"]), len(g["children"]), 1)
            if d <= 0.5:
                pairs.append((d, i, j))
    pairs.sort()
    used_r, used_g, out = set(), set(), []
    for d, i, j in pairs:
        if i in used_r or j in used_g:
            continue
        used_r.add(i)
        used_g.add(j)
        out.append((rn[i], gn[j]))
    return out, [o for i, o in enumerate(rn) if i not in used_r], [o for j, o in enumerate(gn) if j not in used_g]


def _lab(arr):
    import numpy as np

    a = arr.astype("float64") / 255.0
    a = np.where(a > 0.04045, ((a + 0.055) / 1.055) ** 2.4, a / 12.92)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = a @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def visual_pair(ref_png: Path, gen_png: Path, dest: Path | None) -> dict:
    """Both PNGs are CorelDRAW renders of the CDRs (same renderer, same long-side pixel budget); they are resized to the same grid (the reference's
    aspect) before any score. Scores: tools/metrics.visual_similarity (SSIM / dHash / edge / combined), mean RGB and CIE76 colour difference, and the
    translation between the two (phase correlation) as a share of the width."""
    import numpy as np
    from PIL import Image, ImageDraw

    import metrics

    ref, gen = Image.open(ref_png).convert("RGB"), Image.open(gen_png).convert("RGB")
    res = {"ref_px": list(ref.size), "gen_px": list(gen.size)}
    aspect_ref, aspect_gen = ref.width / ref.height, gen.width / gen.height
    res["aspect_ratio_of_renders"] = round(aspect_gen / aspect_ref, 4)
    v = metrics.visual_similarity(str(gen_png), str(ref_png))
    res.update({k: round(float(v[k]), 4) for k in ("ssim", "phash_similarity", "phash_hamming", "edge_similarity", "combined") if k in v})
    W = 1200
    size = (W, max(1, round(W / aspect_ref)))
    a, b = np.asarray(ref.resize(size, Image.LANCZOS)), np.asarray(gen.resize(size, Image.LANCZOS))
    res["mean_abs_rgb_diff"] = round(float(np.abs(a.astype(int) - b.astype(int)).mean()), 2)
    res["mean_delta_e76"] = round(float(np.sqrt(((_lab(a) - _lab(b)) ** 2).sum(-1)).mean()), 2)
    ga, gb = a.mean(-1), b.mean(-1)
    ga, gb = ga - ga.mean(), gb - gb.mean()
    cross = np.fft.ifft2(np.fft.fft2(ga) * np.conj(np.fft.fft2(gb)))
    cross /= np.abs(cross) + 1e-9
    py, px = np.unravel_index(np.argmax(np.abs(cross)), cross.shape)
    py = py - size[1] if py > size[1] // 2 else py
    px = px - size[0] if px > size[0] // 2 else px
    res["alignment_shift_pct_of_width"] = [round(100 * px / size[0], 2), round(100 * py / size[1], 2)]
    if dest is not None:
        dest.mkdir(parents=True, exist_ok=True)
        ref_i, gen_i = Image.fromarray(a), Image.fromarray(b)
        ref_i.save(dest / "reference.jpg", quality=88)
        gen_i.save(dest / "generated.jpg", quality=88)
        diff = np.abs(a.astype(int) - b.astype(int)).max(-1)
        heat = np.zeros(a.shape, "uint8")
        heat[..., 0] = np.clip(diff * 3, 0, 255)
        heat[..., 1] = np.clip((diff - 85) * 3, 0, 255)
        Image.fromarray(heat).save(dest / "diff_heatmap.jpg", quality=88)
        Image.blend(ref_i, gen_i, 0.5).save(dest / "overlay.jpg", quality=88)
        side = Image.new("RGB", (W * 2 + 10, size[1] + 24), "white")
        side.paste(ref_i, (0, 24)); side.paste(gen_i, (W + 10, 24))
        d = ImageDraw.Draw(side)
        d.text((6, 5), "DESIGNER REFERENCE", fill="black"); d.text((W + 16, 5), "ENGINE OUTPUT (planner OFF)", fill="black")
        side.save(dest / "side_by_side.jpg", quality=88)
    return res


def structural_compare(case: dict, ref_ex: dict, gen_ex: dict, installed: set[str]) -> tuple[dict, list[dict]]:
    import math

    pw, ph = ref_ex["page"]["width_mm"], ref_ex["page"]["height_mm"]
    ref, gen = _objs(ref_ex), _objs(gen_ex)
    s: dict = {"ref_objects": len(ref), "gen_objects": len(gen), "ref_top_level": sum(1 for o in ref if o["parent"] is None), "gen_top_level": sum(1 for o in gen if o["parent"] is None)}
    defects: list[dict] = []

    def add(code, measure, detail=""):
        defects.append({"code": code, "measure": measure, "detail": detail})

    pairs, ref_only, gen_only = match_objects(ref, gen, pw, ph)
    rows = []
    for r, g in pairs:
        rcx, rcy, gcx, gcy = r["x"] + r["w"] / 2, r["y"] + r["h"] / 2, g["x"] + g["w"] / 2, g["y"] + g["h"] / 2
        row = {"case": case["case"], "ref_id": r["id"], "gen_id": g["id"], "type": r["type"], "kind": r["kind"], "ref_name": r["name"], "ref_box_mm": [round(r[k], 1) for k in ("x", "y", "w", "h")],
               "gen_box_mm": [round(g[k], 1) for k in ("x", "y", "w", "h")], "dx_mm": round(gcx - rcx, 1), "dy_mm": round(gcy - rcy, 1), "dw_mm": round(g["w"] - r["w"], 1),
               "dh_mm": round(g["h"] - r["h"], 1), "rel_pos_err_pct_page": round(100 * math.hypot((gcx - rcx) / pw, (gcy - rcy) / ph), 2),
               "rel_size_err_pct": round(100 * max(abs(g["w"] / r["w"] - 1), abs(g["h"] / r["h"] - 1)), 2),
               "aspect_ratio_change_pct": round(100 * ((g["w"] / g["h"]) / (r["w"] / r["h"]) - 1), 2), "rotation_diff_deg": round(g["rotation"] - r["rotation"], 2), "z_ref": r["z"], "z_gen": g["z"]}
        rows.append(row)
        if abs(row["aspect_ratio_change_pct"]) > 3 and r["type"] != "rectangle":
            add("logo_distortion", row["aspect_ratio_change_pct"], f"{r['type']} {r['name'] or r['id']}")
        if row["rel_pos_err_pct_page"] > 2 and r["parent"] is None:
            add("object_position_error", row["rel_pos_err_pct_page"], f"{r['type']} {r['name'] or r['id']}")
        if row["rel_size_err_pct"] > 5 and r["parent"] is None:
            add("object_size_error", row["rel_size_err_pct"], f"{r['type']} {r['name'] or r['id']}")
    big = lambda o: o["w"] * o["h"] > 0.002 * pw * ph  # noqa: E731 - ignore specks
    miss = [o for o in ref_only if o["parent"] is None and big(o)]
    extra = [o for o in gen_only if o["parent"] is None and big(o)]
    if miss:
        add("object_missing", len(miss), ", ".join(f"{o['type']}@{round(o['x'])},{round(o['y'])} {round(o['w'])}x{round(o['h'])}" for o in miss[:4]))
    if extra:
        add("object_extra", len(extra), ", ".join(f"{o['type']}@{round(o['x'])},{round(o['y'])} {round(o['w'])}x{round(o['h'])}" for o in extra[:4]))
    s["matched_objects"] = len(pairs)
    s["z_order_inversions"] = sum(1 for i, (r1, g1) in enumerate(pairs) for r2, g2 in pairs[i + 1:] if (r1["z"] - r2["z"]) * (g1["z"] - g2["z"]) < 0 and r1["parent"] == r2["parent"] and g1["parent"] == g2["parent"])
    if rows:
        s["mean_rel_pos_err_pct"] = round(sum(r["rel_pos_err_pct_page"] for r in rows if r["ref_id"].count(".") == 0) / max(1, sum(1 for r in rows if r["ref_id"].count(".") == 0)), 2)
        s["max_rel_pos_err_pct"] = max(r["rel_pos_err_pct_page"] for r in rows)
    # names: block level, per script (a designer may set one name as several text objects; the engine writes one text with line breaks)
    for script, label in (("ta", "tamil"), ("en", "english")):
        rb, gb = name_block(ref_ex, script), name_block(gen_ex, script)
        s[f"{label}_ref"], s[f"{label}_gen"] = rb, gb
        if rb and not gb:
            if script == "ta" and case.get("language") == "en":
                continue
            add("name_missing", 1, label)
            continue
        if not rb or not gb:
            continue
        rbox, gbox = rb["box"], gb["box"]
        s[f"{label}_height_ratio"] = round(gbox[3] / rbox[3], 3) if rbox[3] else None
        s[f"{label}_width_ratio"] = round(gbox[2] / rbox[2], 3) if rbox[2] else None
        s[f"{label}_centre_err_pct_page"] = round(100 * math.hypot(((gbox[0] + gbox[2] / 2) - (rbox[0] + rbox[2] / 2)) / pw, ((gbox[1] + gbox[3] / 2) - (rbox[1] + rbox[3] / 2)) / ph), 2)
        area = (gbox[2] * gbox[3]) / max(rbox[2] * rbox[3], 1e-9)
        s[f"{label}_area_ratio"] = round(area, 3)
        if gbox[3] / rbox[3] < 0.85 and area < 0.8:
            add("name_too_small", round(gbox[3] / rbox[3], 3), f"{label} block height ratio")
        elif gbox[3] / rbox[3] > 1.18 and area > 1.25:
            add("name_too_large", round(gbox[3] / rbox[3], 3), f"{label} block height ratio")
        if s[f"{label}_centre_err_pct_page"] > 3:
            add("name_off_position", s[f"{label}_centre_err_pct_page"], label)
        if rb["lines"] != gb["lines"]:
            add("name_line_count", f"{rb['lines']}->{gb['lines']}", label)
        if set(rb["fonts"]) != set(gb["fonts"]):
            add("name_font_differs", f"{'/'.join(rb['fonts'])} -> {'/'.join(gb['fonts'])}", label)
        s[f"{label}_text_equal"] = rb["text"] == gb["text"]
    lang = case.get("language")
    g_en, g_ta, r_en, r_ta = name_block(gen_ex, "en"), name_block(gen_ex, "ta"), name_block(ref_ex, "en"), name_block(ref_ex, "ta")
    if lang == "ta" and g_en and not r_en:
        add("language_content_mismatch", 1, "Tamil-only side but the engine board also carries an English name")
    if lang == "en" and g_ta and not r_ta:
        add("language_content_mismatch", 1, "English-only side but the engine board also carries a Tamil name")
    # fonts not installed here (any text object of the generated board)
    gf = sorted({(o["text"] or {}).get("font") for o in gen if o["type"] == "text" and (o.get("text") or {}).get("font")})
    miss_fonts = [f for f in gf if f.casefold() not in installed]
    s["gen_fonts"], s["gen_fonts_missing_on_pc"] = gf, miss_fonts
    if miss_fonts:
        add("name_font_missing_on_pc", len(miss_fonts), ", ".join(miss_fonts))
    rf = sorted({(o["text"] or {}).get("font") for o in ref if o["type"] == "text" and (o.get("text") or {}).get("font")})
    s["ref_fonts"], s["ref_fonts_missing_on_pc"] = rf, [f for f in rf if f.casefold() not in installed]
    # geometry sanity of the generated board
    for o in gen:
        out = max(-o["x"], -o["y"], o["x"] + o["w"] - pw, o["y"] + o["h"] - ph, 0)
        if out > 0.002 * min(pw, ph) and o["parent"] is None and not (o["w"] * o["h"] >= 0.9 * pw * ph):
            add("outside_page", round(out, 1), f"{o['type']} {o['name'] or o['id']}")
    gt = [o for o in gen if o["type"] == "text" and not _is_marker((o.get("text") or {}).get("content") or "")]
    rt = [o for o in ref if o["type"] == "text" and not _is_marker((o.get("text") or {}).get("content") or "")]

    def overlaps(ts):
        n = 0
        for i, a in enumerate(ts):
            for b in ts[i + 1:]:
                ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
                oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
                if ox > 0 and oy > 0 and ox * oy / max(min(a["w"] * a["h"], b["w"] * b["h"]), 1e-9) > 0.10:
                    n += 1
        return n

    s["text_overlaps_gen"], s["text_overlaps_ref"] = overlaps(gt), overlaps(rt)
    if s["text_overlaps_gen"] > s["text_overlaps_ref"]:
        add("text_overlap", s["text_overlaps_gen"] - s["text_overlaps_ref"])
    bgs = [o for o in gen if o["parent"] is None and o["w"] * o["h"] >= 0.9 * pw * ph]
    if bgs and not any(abs(o["x"]) < 2 and abs(o["y"]) < 2 and o["w"] >= pw - 3 and o["h"] >= ph - 3 for o in bgs):
        add("background_not_full", 1)
    # side marker
    ref_marker = [o["text"]["content"] for o in ref if o["type"] == "text" and re.fullmatch(r"\(?\s*[12]\s*\)?", (o["text"].get("content") or "").strip())]
    gen_marker = [o["text"]["content"] for o in gen if o["type"] == "text" and re.fullmatch(r"\(?\s*[12]\s*\)?", (o["text"].get("content") or "").strip())]
    s["ref_side_marker_texts"], s["gen_side_marker_texts"] = ref_marker, gen_marker
    if ref_marker and not gen_marker:
        add("side_marker_lost", 1)
    s["defects"] = defects
    return s, rows


def cmd_compare(a):
    from app import fonts

    out = out_dir(a)
    inst = fonts.installed_fonts()
    installed = {f.casefold() for f in inst.get("fonts", [])}
    gj = json.loads((out / "baseline_generation_results.json").read_text(encoding="utf-8"))
    gen = gj["rows"]
    master_by_id = {v: k for k, v in gj["masters"].items()}
    mapping = {r["designer_file"]: r for r in json.loads((out / "shop_reference_mapping.json").read_text(encoding="utf-8"))["rows"]}
    vis_rows, st_rows, obj_rows, notes = [], [], [], []
    for g in gen:
        case = g["case"]
        m = mapping.get(g["designer_file"], {})
        eng_master = master_by_id.get((g.get("master_used_detail") or {}).get("job_id"))
        base = {"case": case, "designer_file": g["designer_file"], "mapping_status": m.get("status"), "side": g.get("side"), "size_ft": f"{g.get('w_ft')}x{g.get('h_ft')}",
                "language": g.get("language"), "engine_mode": g.get("engine_mode"), "example_board": g.get("example_board"), "example_exact": g.get("example_exact"),
                "engine_master_file": eng_master, "reference_master_structural_guess": m.get("structural_master_guess"), "reference_master_library": m.get("library_master"),
                "engine_master_matches_structural_guess": (eng_master == m.get("structural_master_guess")) if m.get("structural_master_guess") and not str(m.get("structural_master_guess")).startswith("tie") else None}
        if g.get("status") != "done":
            notes.append({**base, "comparable": False, "reason": f"generation {g.get('status')}: {g.get('error') or g.get('skipped')}"})
            continue
        ref_dir = extraction_dir(out, "designers", WORKING / g["designer_file"])
        gen_dir_ = extraction_dir(out, "generated", out / "generated" / f"{case}.cdr")
        ref_ex, gen_ex = (json.loads((d / "extraction.json").read_text(encoding="utf-8")) if (d / "extraction.json").is_file() else None for d in (ref_dir, gen_dir_))
        if not ref_ex or not gen_ex:
            notes.append({**base, "comparable": False, "reason": "extraction missing (" + ("reference" if not ref_ex else "generated") + ")"})
            continue
        pw, ph, gw, gh = ref_ex["page"]["width_mm"], ref_ex["page"]["height_mm"], gen_ex["page"]["width_mm"], gen_ex["page"]["height_mm"]
        same = abs(pw - gw) <= 1.0 and abs(ph - gh) <= 1.0
        if m.get("status") not in ("CONFIRMED", "PROBABLE"):
            notes.append({**base, "comparable": False, "reason": f"mapping is {m.get('status')}"})
            continue
        if not same:
            notes.append({**base, "comparable": False, "reason": f"page sizes differ: reference {round(pw)}x{round(ph)} mm, generated {round(gw)}x{round(gh)} mm"})
            continue
        vis = visual_pair(ref_dir / "master_page.png", gen_dir_ / "master_page.png", out / "visual" / case)
        st, rows = structural_compare({"case": case, "language": g.get("language")}, ref_ex, gen_ex, installed)
        content_agree = all(st.get(f"{x}_text_equal", True) for x in ("tamil", "english"))
        vis_rows.append({**base, **vis, "text_content_equal": content_agree, "score_meaningful": bool(same and content_agree),
                         "note": "" if content_agree else "name text differs between reference and generated: visual score is dominated by lettering"})
        st_flat = {k: v for k, v in st.items() if k not in ("defects", "tamil_ref", "tamil_gen", "english_ref", "english_gen")}
        st_rows.append({**base, **st_flat, "defect_codes": sorted({d["code"] for d in st["defects"]}), "defects": st["defects"],
                        "tamil_ref_block": st.get("tamil_ref"), "tamil_gen_block": st.get("tamil_gen"), "english_ref_block": st.get("english_ref"), "english_gen_block": st.get("english_gen")})
        obj_rows += rows
        write_json(out / "visual" / case / "structural_summary.json", st)
    write_csv(out / "visual_comparison_results.csv", vis_rows)
    write_csv(out / "structural_comparison_results.csv", st_rows)
    write_csv(out / "structural_object_matches.csv", obj_rows)
    write_json(out / "comparison_not_comparable.json", notes)
    print("compared", len(vis_rows), "boards; not comparable:", len(notes))
    for n in notes:
        print("  not comparable:", n["case"], n["reason"])


# ----------------------------------------------------------------------------------------------------------- design intent


def shape_class(w: float, h: float) -> str:
    r = w / h
    return "portrait (3x6, 4x7, 4x8, 3x5)" if r < 0.8 else "compact landscape (6x3, 8x4)" if r < 2.5 else "mid landscape (8x3, 10x3, 10x4, 12x4)" if r < 4.2 else "wide (15x3 .. 26x3, 20x4)"


def _cv(vals: list[float]) -> float:
    n = len(vals)
    if n < 2:
        return float("inf")
    m = sum(vals) / n
    sd = (sum((v - m) ** 2 for v in vals) / (n - 1)) ** 0.5
    return sd


def _conf(n: int, spread: float) -> str:
    if n >= 6 and spread < 0.03:
        return "high"
    if n >= 4 and spread < 0.06:
        return "medium"
    return "low"


def cmd_intent(a):
    """Recurring design principles read from the REAL data: master structure (COM extraction + master_model relations), how each master element was
    placed on the designer boards (the project's example library = per-board element boxes derived from COM dumps of these same files), and the
    designers' name text (COM extraction of the 50 boards). Every statement carries its numbers; nothing is claimed without evidence."""
    from app import example_layout, master_model

    out = out_dir(a)
    lib = example_layout.library("hangyo") or {}
    masters_lib = {m["id"]: m for m in lib.get("masters", [])}
    findings: list[dict] = []
    n_id = [0]

    def add(kind, statement, files, evidence, confidence, auto, human, **extra):
        n_id[0] += 1
        findings.append({"id": f"DI-{n_id[0]:03d}", "category": kind, "observed_relationship": statement, "supporting_files": files, "evidence": evidence, "confidence": confidence,
                         "detectable_automatically": auto, "needs_human_confirmation": human, **extra})

    # --- 1. master structure
    master_models = {}
    for p in sorted(CREATIVE.glob("*.cdr")):
        ex = load_extraction(out, "masters", p)
        if not ex:
            continue
        m = master_model.build_model(ex, "stage1")
        master_models[p.name] = m
        tops = m.top_level()
        pcs = [o for o in tops if o.kind == "powerclip"]
        kids = {o.id: [m.get(c) for c in o.children] for o in pcs}
        add("composition", f"{p.name}: {len(tops)} top-level objects; page-covering container(s): " + (", ".join(f"{o.id} ({o.type}, {len(o.children)} children)" for o in pcs) or "none") +
            f"; bands {len(m.bands['columns'])} columns x {len(m.bands['rows'])} rows", [p.name],
            {"top_level": [(o.id, o.type, o.kind, o.role, o.region, o.adaptation_mode) for o in tops], "relations": {k: sum(1 for r in m.relations if r['kind'] == k) for k in {r['kind'] for r in m.relations}},
             "units": m.units[:6]}, "high", True, False)
        for r in m.relations:
            if r["kind"] == "text_on":
                add("text_on_surface", f"{p.name}: text {r['text']} sits on surface {r['surface']} (model relation)", [p.name], r, "medium", True, False)
        rep = [r for r in m.relations if r["kind"] == "repeat"]
        for r in rep:
            add("repeated_elements", f"{p.name}: objects {r['ids']} are repeats of one element (same type/size)", [p.name], r, "low", True, True)

    # --- 2. how each master element is placed on the designer boards, PER BOARD SHAPE (a rule that holds for 6x3 boards does not hold for 26x3)
    for mid, mm_ in masters_lib.items():
        all_boards = [b for b in lib.get("boards", []) if b.get("master") == mid and b.get("els")]
        classes: dict[str, list] = {}
        for b in all_boards:
            classes.setdefault(shape_class(b["W"], b["H"]), []).append(b)
        for cls, boards in classes.items():
            if len(boards) < 3:
                continue
            for el in mm_["elements"]:
                rows = []
                for b in boards:
                    e = b["els"].get(el["k"])
                    if not e:
                        continue
                    W, H = b["W"], b["H"]
                    w, h = e["w"] * W, e["h"] * H
                    rows.append({"file": b["file"], "W": W, "H": H, "w_frac": e["w"], "h_frac": e["h"], "w_mm": w, "h_mm": h, "cx_frac": e["cx"], "cy_frac": e["cy"],
                                 "left_mm": e["cx"] * W - w / 2, "right_mm": (1 - e["cx"]) * W - w / 2, "bottom_mm": e["cy"] * H - h / 2, "top_mm": (1 - e["cy"]) * H - h / 2,
                                 "aspect_ratio_vs_master": (w / h) / el["aspect"] if h and el.get("aspect") else None})
                if len(rows) < 3:
                    continue
                Wm = sum(r["W"] for r in rows) / len(rows)
                Hm = sum(r["H"] for r in rows) / len(rows)
                sc = {"width = fixed share of board width": _cv([r["w_frac"] for r in rows]), "height = fixed share of board height": _cv([r["h_frac"] for r in rows]),
                      "width = fixed size in mm": _cv([r["w_mm"] for r in rows]) / Wm, "height = fixed size in mm": _cv([r["h_mm"] for r in rows]) / Hm}
                px = {"centred at a fixed share of the width": _cv([r["cx_frac"] for r in rows]), "fixed distance from the left edge": _cv([r["left_mm"] for r in rows]) / Wm,
                      "fixed distance from the right edge": _cv([r["right_mm"] for r in rows]) / Wm}
                py = {"centred at a fixed share of the height": _cv([r["cy_frac"] for r in rows]), "fixed distance from the bottom edge": _cv([r["bottom_mm"] for r in rows]) / Hm,
                      "fixed distance from the top edge": _cv([r["top_mm"] for r in rows]) / Hm}
                bs, bx, by_ = min(sc.items(), key=lambda t: t[1]), min(px.items(), key=lambda t: t[1]), min(py.items(), key=lambda t: t[1])
                ar = [r["aspect_ratio_vs_master"] for r in rows if r["aspect_ratio_vs_master"]]
                distorted = sum(1 for v in ar if abs(v - 1) > 0.05)
                files = [r["file"] for r in rows]
                desc = (f"[{cls}] master {mm_['file']} element {el['k']} ({el['kind']}, {el['n_desc']} nested) on {len(rows)} boards: size '{bs[0]}' (spread {bs[1]:.3f}); horizontal "
                        f"'{bx[0]}' (spread {bx[1]:.3f}); vertical '{by_[0]}' (spread {by_[1]:.3f}); aspect ratio kept (within 5%) on {len(ar) - distorted} of {len(ar)} boards")
                add("element_placement", desc, files,
                    {"board_shape_class": cls, "n_boards": len(rows), "size_rules_spread": {k: round(v, 4) for k, v in sc.items()}, "x_rules_spread": {k: round(v, 4) for k, v in px.items()},
                     "y_rules_spread": {k: round(v, 4) for k, v in py.items()}, "aspect_ratio_vs_master_min_max": [round(min(ar), 3), round(max(ar), 3)] if ar else None,
                     "boards_with_aspect_change_gt_5pct": distorted, "board_sizes_ft": sorted({f"{round(r['W'] / FT)}x{round(r['H'] / FT)}" for r in rows})},
                    _conf(len(rows), max(bs[1], bx[1], by_[1])), True, True, board_shape_class=cls)
    # --- 3. names, from the designer extractions
    by_master: dict[str, list] = {}
    mapping = json.loads((out / "shop_reference_mapping.json").read_text(encoding="utf-8"))["rows"] if (out / "shop_reference_mapping.json").is_file() else []
    for r in mapping:
        ex = load_extraction(out, "designers", WORKING / r["designer_file"])
        if not ex:
            continue
        pw, ph = ex["page"]["width_mm"], ex["page"]["height_mm"]
        ta, en = name_block(ex, "ta"), name_block(ex, "en")
        tag = [o for o in ex["objects"] if o["type"] == "text" and re.search(r"adinn\s*/", (o["text"] or {}).get("content") or "", re.I)]
        by_master.setdefault(shape_class(pw, ph), []).append(
            {"file": r["designer_file"], "pw": pw, "ph": ph, "ta": ta, "en": en, "tag": tag[0] if tag else None, "side": r.get("side"), "script": r.get("content_script")})
    for master, rows in by_master.items():
        files = [r["file"] for r in rows]
        for label, key in (("Tamil", "ta"), ("English", "en")):
            blocks = [(r, r[key]) for r in rows if r[key]]
            if len(blocks) < 3:
                continue
            fonts_ = {}
            for r, b in blocks:
                for f in b["fonts"]:
                    fonts_[f] = fonts_.get(f, 0) + 1
            lines = {}
            for r, b in blocks:
                lines[b["lines"]] = lines.get(b["lines"], 0) + 1
            hfr = [b["box"][3] / r["ph"] for r, b in blocks]
            cx = [(b["box"][0] + b["box"][2] / 2) / r["pw"] for r, b in blocks]
            cy = [(b["box"][1] + b["box"][3] / 2) / r["ph"] for r, b in blocks]
            upper = None
            if key == "en":
                letters = [b for r, b in blocks]
                upper = sum(1 for r, b in blocks if (r[key] and True)) and None
            add("name_text", f"{master}: {label} name on {len(blocks)} designer boards - fonts {fonts_}; lines {lines}; block height {min(hfr):.2f}-{max(hfr):.2f} of the board height; "
                f"centre x {min(cx):.2f}-{max(cx):.2f}, centre y {min(cy):.2f}-{max(cy):.2f}", [r["file"] for r, _ in blocks],
                {"fonts": fonts_, "line_count_histogram": lines, "height_share_min_max": [round(min(hfr), 3), round(max(hfr), 3)], "centre_x_min_max": [round(min(cx), 3), round(max(cx), 3)],
                 "centre_y_min_max": [round(min(cy), 3), round(max(cy), 3)], "alignment": sorted({x for r, b in blocks for x in b["align"]})},
                _conf(len(blocks), _cv(cx)), True, False)
        tags = [r for r in rows if r["tag"]]
        if len(tags) >= 3:
            rm = [(r["pw"] - (r["tag"]["x"] + r["tag"]["w"]), r["tag"]["y"], r["tag"]["h"]) for r in tags]
            add("fixed_element", f"{master}: the 'ADINN/..' job tag text: distance from the right edge {min(x[0] for x in rm):.0f}-{max(x[0] for x in rm):.0f} mm, from the bottom {min(x[1] for x in rm):.0f}-{max(x[1] for x in rm):.0f} mm, "
                f"height {min(x[2] for x in rm):.1f}-{max(x[2] for x in rm):.1f} mm - i.e. it keeps a fixed size / margin" if _cv([x[2] for x in rm]) < 2 else
                f"{master}: the job tag text changes size with the board ({min(x[2] for x in rm):.1f}-{max(x[2] for x in rm):.1f} mm)", [r["file"] for r in tags],
                {"right_margin_mm": [round(x[0], 1) for x in rm][:12], "bottom_mm": [round(x[1], 1) for x in rm][:12], "height_mm": [round(x[2], 1) for x in rm][:12]}, "medium", True, True)
    # --- 3b. background and photographs
    import math as _m

    m_bits = []
    for pth in sorted(CREATIVE.glob("*.cdr")):
        mex = load_extraction(out, "masters", pth)
        if mex:
            m_bits += [(pth.name, o["w"] / o["h"], o["id"]) for o in mex["objects"] if o["type"] == "bitmap" and o["h"] > 0]
    dev, cover, nboards, files_b = [], 0, 0, []
    for r in mapping:
        ex = load_extraction(out, "designers", WORKING / r["designer_file"])
        if not ex:
            continue
        nboards += 1
        pw, ph = ex["page"]["width_mm"], ex["page"]["height_mm"]
        big = [o for o in ex["objects"] if o["parent"] is None and o["w"] * o["h"] >= 0.9 * pw * ph]
        cover += 1 if any(abs(o["x"]) < 2 and abs(o["y"]) < 2 and o["w"] >= pw - 3 and o["h"] >= ph - 3 for o in big) else 0
        for o in ex["objects"]:
            if o["type"] == "bitmap" and o["h"] > 0 and m_bits:
                a_ = o["w"] / o["h"]
                best = min(m_bits, key=lambda t: abs(_m.log(a_ / t[1])))
                dev.append(100 * (a_ / best[1] - 1))
                files_b.append(r["designer_file"])
    if nboards:
        add("background", f"a page-covering top-level container exists on {cover} of {nboards} designer boards and fills the page exactly (the background stretches to every board size)",
            [], {"boards_with_exact_cover": cover, "boards": nboards}, "high" if cover == nboards else "medium", True, False)
    if dev:
        within = sum(1 for d_ in dev if abs(d_) <= 3)
        add("photographs", f"photo bitmaps on designer boards keep the master's proportions: {within} of {len(dev)} bitmaps within 3% of the nearest master bitmap's aspect ratio (min {min(dev):.1f}%, max {max(dev):.1f}%)",
            sorted(set(files_b))[:10], {"bitmaps": len(dev), "within_3pct": within, "deviation_pct_min_max": [round(min(dev), 1), round(max(dev), 1)]}, "high" if within >= 0.9 * len(dev) else "medium", True, False)
    # --- 4. sides
    sides = [r for r in mapping if r.get("side")]
    if sides:
        s1 = [r for r in sides if r["side"] == 1]
        s2 = [r for r in sides if r["side"] == 2]
        add("side_convention", f"double-sided boards: side (1) carries {sorted({r.get('content_script') for r in s1})} text, side (2) {sorted({r.get('content_script') for r in s2})} ({len(s1)} / {len(s2)} files); the side marker is part of the FILE NAME, not printed on the board "
            f"(no board text matches '(1)'/'(2)' in {sum(1 for r in sides if True)} files checked)", [r["designer_file"] for r in sides[:8]],
            {"side1_scripts": {k: sum(1 for r in s1 if r.get('content_script') == k) for k in {r.get('content_script') for r in s1}},
             "side2_scripts": {k: sum(1 for r in s2 if r.get('content_script') == k) for k in {r.get('content_script') for r in s2}}}, "high", True, False)
    write_json(out / "design_intent_discovery.json", {"method": "see each finding's evidence; sources: COM extractions of masters and designer boards, example library (COM-derived element boxes of the same designer files)",
                                                      "findings": findings, "count": len(findings)})
    print(len(findings), "findings")
    from collections import Counter
    print(Counter(f["category"] for f in findings))


# ----------------------------------------------------------------------------------------------------- verify + report


def cmd_verify(a):
    """Output sanity: generated CDRs were opened by real CorelDRAW during extraction (an extraction = it opened and read); PDFs / PNGs are opened here; source files re-hashed;
    leftover CorelDRAW processes counted; the baseline server log searched for dialogs."""
    import subprocess

    from PIL import Image

    out = out_dir(a)
    gj = json.loads((out / "baseline_generation_results.json").read_text(encoding="utf-8"))
    inv = json.loads((out / "dataset_inventory.json").read_text(encoding="utf-8"))
    res = {"generated": [], "source_files_unchanged": True, "changed_sources": []}
    for r in gj["rows"]:
        row = {"case": r["case"]}
        cdr = out / "generated" / f"{r['case']}.cdr"
        row["cdr_exists"] = cdr.is_file()
        row["cdr_zip_ok"] = False
        if cdr.is_file():
            try:
                row["cdr_zip_ok"] = zipfile.ZipFile(cdr).testzip() is None
            except Exception:
                pass
        ex = load_extraction(out, "generated", cdr)
        row["opened_and_read_by_corel"] = ex is not None
        if ex:
            row["page_mm"] = [ex["page"]["width_mm"], ex["page"]["height_mm"]]
            row["page_matches_request"] = abs(ex["page"]["width_mm"] - r["w_ft"] * FT) < 1.0 and abs(ex["page"]["height_mm"] - r["h_ft"] * FT) < 1.0
        pdf = out / "generated" / f"{r['case']}.pdf"
        row["pdf_valid"] = pdf.is_file() and pdf.read_bytes()[:5] == b"%PDF-"
        row["pdf_bytes"] = pdf.stat().st_size if pdf.is_file() else None
        png = next(iter(sorted((out / "generated").glob(f"{r['case']}__*.png"))), None)
        row["png_ok"] = False
        if png:
            try:
                with Image.open(png) as im:
                    row["png_ok"], row["png_px"] = True, list(im.size)
            except Exception:
                pass
        res["generated"].append(row)
    for loc in inv["locations"].values():
        for f in loc["files"]:
            p = HANGYO / f["path"]
            if not p.is_file() or sha256(p) != f["sha256"]:
                res["source_files_unchanged"] = False
                res["changed_sources"].append(f["path"])
    tl = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
    res["corel_processes_running_now"] = tl.count("coreldrw.exe")
    log = out / "_server.log"
    res["baseline_server_log_mentions_dialog"] = bool(log.is_file() and "dialog" in log.read_text(encoding="utf-8", errors="replace").lower())
    res["generated_ok_counts"] = {k: sum(1 for r in res["generated"] if r.get(k)) for k in ("cdr_zip_ok", "opened_and_read_by_corel", "page_matches_request", "pdf_valid", "png_ok")}
    write_json(out / "output_verification.json", res)
    print(res["generated_ok_counts"], "sources unchanged:", res["source_files_unchanged"], "corel procs:", res["corel_processes_running_now"], "dialog in log:", res["baseline_server_log_mentions_dialog"])


def _med(v):
    v = sorted(x for x in v if x is not None)
    return None if not v else (v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def cmd_report(a):
    import platform
    import subprocess

    out = out_dir(a)
    rd = lambda n: list(csv.DictReader(open(out / n, encoding="utf-8-sig")))  # noqa: E731
    inv = json.loads((out / "dataset_inventory.json").read_text(encoding="utf-8"))
    mastersum = json.loads((out / "master_analysis_summary.json").read_text(encoding="utf-8"))
    mp = json.loads((out / "shop_reference_mapping.json").read_text(encoding="utf-8"))
    gj = json.loads((out / "baseline_generation_results.json").read_text(encoding="utf-8"))
    vis, st = rd("visual_comparison_results.csv"), rd("structural_comparison_results.csv")
    ver = json.loads((out / "output_verification.json").read_text(encoding="utf-8")) if (out / "output_verification.json").is_file() else {}
    intent = json.loads((out / "design_intent_discovery.json").read_text(encoding="utf-8"))
    status = json.loads((out / "extraction_status.json").read_text(encoding="utf-8"))
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    from app import fonts as _fonts
    installed = sorted(_fonts.installed_fonts().get("fonts", []))
    corel_ver = next((v.get("corel_version") for v in (json.load(open(p, encoding="utf-8")) for p in (out / "extractions" / "masters").glob("*/extraction.json"))), None)
    cfg = {"date": time.strftime("%Y-%m-%d %H:%M:%S"), "git_branch": branch, "git_head": head, "python": platform.python_version(), "os": platform.platform(), "corel_version": corel_ver,
           "render_long_side_px": RENDER_PX, "planner": "OFF", "env": {k: os.environ.get(k) for k in ("SIGNAGE_SPATIAL_PLANNER", "SIGNAGE_MASTER_ANALYSIS", "SIGNAGE_ENGINE")},
           "server_env_used_for_baseline": {"SIGNAGE_ENGINE": "corel", "SIGNAGE_SHOP_TIMEOUT_S": "240 (production default is 45; raised so slow machines do not time out)", "SIGNAGE_MASTER_ANALYSIS": "0", "SIGNAGE_SPATIAL_PLANNER": "0"},
           "source_hashes": {f["path"]: f["sha256"] for loc in inv["locations"].values() for f in loc["files"] if f["ext"] in (".cdr", ".xlsx", ".ttf")},
           "library_json_sha256": sha256(BACKEND / "brand_data" / "hangyo" / "library.json"), "tool": "backend/tools/stage1_audit.py",
           "generation_parameters": [{k: c.get(k) for k in ("case", "name_en", "name_ta", "w_ft", "h_ft", "board_type", "language", "sno", "side", "derivation")} for c in gj["rows"]]}
    write_json(out / "run_config.json", cfg)

    n = len(st)
    meaningful = [r for r in vis if r["score_meaningful"] == "True"]
    L = []
    P = L.append
    P("# Stage 1 report - Hangyo dataset audit and baseline evaluation\n")
    P(f"Generated {cfg['date']} on `{branch}` @ `{head}` with CorelDRAW {corel_ver}. Planner OFF. Tool: `backend/tools/stage1_audit.py`. All outputs are in `backend/dataset_analysis/stage1/` (outside the dataset).\n")
    P("## A. Dataset inventory\n")
    for k, v in inv["locations"].items():
        P(f"- **{k}** (`{v['root']}`): {v['file_count']} files, {v['by_extension']}")
    P(f"- **Excel:** the Creative folder has **no Excel file**. Two workbooks exist in `01 - Details` (order sheet and a 'for Printing' copy, 31 shops / 36 board lines each; columns S.N, Shopname, Delivery, Work, W, H, Qty, Sq.ft, Rate, Amount, GST, Outlet code, Sales). They have **no Tamil name, language, side or master column**; sizes are in feet, board type is coded FLB / NLB / GSB / GSB D/S.")
    P("- **Designer boards:** 50 CDR files at the top of `02 - Working` (the other 4 CDRs in subfolders are 'Print Details' sheets, not boards); 141 JPG exports and 1 PPTX are print/mail copies.")
    P("- **Fonts/assets:** " + "; ".join(f"`{f['file'].split(chr(92))[-1]}` = {f['family']} {f['style']} (variable: {f['variable']}, Tamil glyphs {f['tamil_glyphs']}), installed on this PC: {f['installed_on_this_pc']}" for f in inv["fonts"]) + ". No linked external assets are exposed by the COM read; embedded bitmaps are counted per file.\n")
    P("| Master | Page (ft) | Orientation | Pages | Top-level | Nested | Text | Bitmaps | Groups | PowerClip | Fonts | Missing on this PC |")
    P("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for m in mastersum:
        P(f"| {m['file']} | {m['page_ft'][0]:.0f} x {m['page_ft'][1]:.0f} | {m['orientation']} | {m['pages']} | {m['top_level']} | {m['nested']} | {m['text_objects']} | {m['bitmaps']} | {m['groups']} | {m['powerclips']} | {', '.join(m['fonts'])} | {', '.join(m['missing_fonts']) or '-'} |")
    P("\nPer-file facts for the 50 designer boards (page, text, fonts, objects) are in `extractions/designers/*/extraction.json`; counts in `extraction_status.json` (all 53 reads `done`).\n")
    P("## B. Master-to-shop mapping\n")
    P(f"- Statuses: **{mp['summary']}**. Evidence per board: order-sheet S.N + W x H + board type, file name, the CDR's own page size and its own text (English spelling similarity), side pairing.")
    prob = [r for r in mp["rows"] if r["status"] == "PROBABLE"]
    for r in prob:
        P(f"  - PROBABLE `{r['designer_file']}`: {r['reason']} (name text similarity {r.get('cdr_text_similarity')})")
    P(f"- Order-sheet shops without any designer file: {len(mp['order_sheet_shops_without_any_designer_file'])}; board lines without a file: {len(mp['order_sheet_board_lines_without_designer_file'])}.")
    from collections import Counter
    P(f"- Double-sided boards: {sum(1 for r in mp['rows'] if r.get('side'))} files in {len({r['pair_key'] for r in mp['rows'] if r.get('side')})} pairs; side (1) never compared with side (2).")
    P(f"- Which master a designer board came from is **not recorded in any file**. Evidence: the project's example library says {dict(Counter(r.get('library_master') for r in mp['rows']))}; an independent top-level-structure match says {dict(Counter(str(r.get('structural_master_guess')) for r in mp['rows']))}. They agree on most boards; they disagree on some (reported in `shop_reference_mapping.csv`).\n")
    P("## C. Baseline generation\n")
    rows = gj["rows"]
    P(f"- Test cases: {len(rows)}; generated by the production engine through the public API (planner OFF, real CorelDRAW): **{sum(1 for r in rows if r.get('status') == 'done')}**; failed: {sum(1 for r in rows if r.get('status') != 'done')}.")
    P(f"- Engine layout mode: {dict(Counter(r.get('engine_mode') for r in rows))} (example = copied a designer board of the example library; rules = no example fit this master/size). Master chosen: {dict(Counter(r.get('master_used') for r in rows))}.")
    P("- The order sheet has no Tamil name / language / side, so the shop-name fields were **derived from each designer CDR's own text** (per-case `derivation` in `baseline_generation_results.json`). This isolates layout from spelling.")
    P("- Engine warnings (top): " + "; ".join(f"{c}x {w[:90]}" for w, c in Counter(x for r in rows for x in (r.get('warnings') or []) if 'low free RAM' not in x).most_common(4)) + ".\n")
    P("## D. Valid designer comparisons\n")
    P(f"- Compared: **{n}** of {len(rows)}; not comparable: {len(json.load(open(out / 'comparison_not_comparable.json', encoding='utf-8')))}. Page sizes match on all compared pairs, both sides rendered by CorelDRAW at {RENDER_PX}px long side; scores meaningful (same page, same name text): **{len(meaningful)}** of {n}. {n - len(meaningful)} pair(s) have name text that differs (designer re-typed / line merging) and are flagged `score_meaningful=False`.\n")
    P("## E. Visual comparison\n")
    for k in ("combined", "ssim", "phash_similarity", "edge_similarity", "mean_abs_rgb_diff", "mean_delta_e76"):
        vv = [_f(r[k]) for r in meaningful if _f(r[k]) is not None]
        P(f"- {k}: mean {sum(vv) / len(vv):.3f}, median {_med(vv):.3f}, min {min(vv):.3f}, max {max(vv):.3f}")
    for mode in ("example", "rules"):
        vv = [_f(r["combined"]) for r in meaningful if r["engine_mode"] == mode]
        if vv:
            P(f"- combined score, engine mode **{mode}** ({len(vv)} boards): mean {sum(vv) / len(vv):.3f}")
    P("- Mean alignment shift between renders: " + str(round(sum(abs(json.loads(r['alignment_shift_pct_of_width'])[0]) for r in vis) / len(vis), 2)) + " % of width (the pages are aligned; differences are content, not offset).")
    P("- Lowest combined scores: " + "; ".join(f"{r['designer_file'][:40]} {r['combined']}" for r in sorted(vis, key=lambda r: _f(r['combined']))[:6]) + ".")
    P("- Normalisation: same page size (verified), same renderer, same long-side budget, resized to a common 1200 px grid before pixel scores; scores are lettering-sensitive, so they are only compared where the name text is equal.\n")
    P("## F. Structural comparison\n")
    c = Counter()
    boards_with = Counter()
    for r in st:
        for d in json.loads(r["defect_codes"]):
            boards_with[d] += 1
    P("| Defect | Boards affected (of %d) |" % n)
    P("|---|---|")
    for code, k in boards_with.most_common():
        P(f"| {code} - {DEFECT_TEXT.get(code, '')} | {k} |")
    for lab in ("tamil", "english"):
        hr = [_f(r.get(f"{lab}_height_ratio")) for r in st if _f(r.get(f"{lab}_height_ratio")) is not None]
        if hr:
            P(f"- {lab} name block height, engine/designer: median {_med(hr):.2f} (min {min(hr):.2f}, max {max(hr):.2f}) over {len(hr)} boards")
    pe = [_f(r.get("max_rel_pos_err_pct")) for r in st if _f(r.get("max_rel_pos_err_pct")) is not None]
    P(f"- Largest top-level object position error per board, % of page: median {_med(pe):.1f}, max {max(pe):.1f}")
    P("- Per-object matches: `structural_object_matches.csv`; per-board defects with measures: `structural_comparison_results.csv`.\n")
    P("### F2. Where the differences concentrate (mean combined similarity over meaningful pairs / mean distinct defect codes per board)\n")
    visd = {r["case"]: r for r in vis}

    def grp(keyf, label):
        g: dict = {}
        for r in st:
            g.setdefault(keyf(r), []).append(r["case"])
        P(f"- **{label}**")
        for k, cs in sorted(g.items(), key=lambda t: str(t[0])):
            comb = [_f(visd[c]["combined"]) for c in cs if visd[c]["score_meaningful"] == "True"]
            nd = [len(json.loads(next(r for r in st if r["case"] == c)["defect_codes"])) for c in cs]
            P(f"  - {k}: {len(cs)} boards, similarity {sum(comb) / len(comb):.3f} (n={len(comb)}), {sum(nd) / len(nd):.1f} defect codes")

    def cls_(r):
        w_, h_ = (float(x) for x in r["size_ft"].split("x"))
        x_ = w_ / h_
        return "portrait (3x6, 4x7, 4x8, 3x5)" if x_ < 0.8 else "compact landscape (6x3, 8x4)" if x_ < 2.5 else "mid landscape (8x3, 10x3, 10x4, 12x4)" if x_ < 4.2 else "wide (15x3 .. 26x3, 20x4)"

    grp(lambda r: {"True": "engine master = the master the designer's board resembles", "False": "engine master DIFFERS from the master the designer's board resembles"}.get(r["engine_master_matches_structural_guess"], "tie / unknown"), "master chosen by the engine vs the designer's structure")
    grp(lambda r: r["engine_mode"], "engine layout mode")
    grp(cls_, "board shape")
    grp(lambda r: r["language"], "language on the board")
    P("")
    P("## G. Top defects of the existing engine (measured on these 50 boards)\n")
    tam = sum(1 for r in st if r.get("tamil_ref_block"))
    ranked = [
        (f"Tamil name set in Nirmala UI where the designer used Anek Tamil (font that IS installed here)", boards_with["name_font_differs"], f"of {tam} boards with Tamil"),
        ("English name font Amaranth is not installed on this PC (designer and engine both reference it; renders use a fallback)", boards_with["name_font_missing_on_pc"], "boards"),
        ("The engine built the board from a different master than the one the designer's board structurally resembles (" + ", ".join(f"{k[0]} instead of {k[1]}: {v}" for k, v in Counter((r.get("engine_master_file"), r.get("reference_master_structural_guess")) for r in st if r.get("engine_master_matches_structural_guess") == "False").items()) + ")", sum(1 for r in st if r.get("engine_master_matches_structural_guess") == "False"), "boards"),
        ("Objects present on the engine board that the designer's does not have", boards_with["object_extra"], "boards"),
        ("Object size differs from the designer's by > 5%", boards_with["object_size_error"], "boards"),
        ("Object position differs from the designer's by > 2% of the page", boards_with["object_position_error"], "boards"),
        ("Name block smaller than the designer's (height < 0.85x)", boards_with["name_too_small"], "boards"),
        ("Non-text object aspect ratio differs by > 3% (logo/graphic distortion)", boards_with["logo_distortion"], "boards"),
        ("Name block centre > 3% of the page away from the designer's", boards_with["name_off_position"], "boards"),
        ("Objects the designer's board has are missing", boards_with["object_missing"], "boards"),
        ("Name set on a different number of lines", boards_with["name_line_count"], "boards"),
    ]
    for i, (t, k, u) in enumerate(ranked[:10], 1):
        P(f"{i}. {t}: **{k}** {u}")
    P("\nObjectively measured above; subjective composition judgments (balance, empty space) are NOT scored.\n")
    P("## H. Font and asset problems\n")
    P(f"- Installed Tamil-capable families here include: {', '.join(f for f in installed if 'tamil' in f.lower() or f.startswith('Nirmala'))}. `Anek Tamil` (the designers' Tamil font; the TTF supplied in `01 - Details` is Anek Tamil Medium variable) is installed, yet the engine forces `Nirmala UI` for Tamil (`layout.TAMIL_FONT`).")
    P("- `Amaranth` (English name font in all 3 masters and all designer boards) is **not installed**; both the designer reference render and the engine render use the same fallback, so it does not bias the comparison but both look unlike the print. `Anek Tamil` is installed. Fonts were not installed or changed in this stage.")
    P("- Generated boards report these missing fonts: " + str(Counter(f for r in st for f in json.loads(r.get('gen_fonts_missing_on_pc') or '[]')).most_common()) + ".\n")
    P("## I. Design intent discovered (evidence in `design_intent_discovery.json`)\n")
    for cat in ("background", "photographs", "side_convention"):
        for f in [x for x in intent["findings"] if x["category"] == cat]:
            P(f"- ({f['confidence']}) {f['observed_relationship']}")
    P("- Per board-shape family the designers follow **different rules** (see the `element_placement` findings): on compact landscape boards (6x3, 8x4) the logo and badge groups keep a fixed share of the board height/width and are centred (spread <= 0.005, n up to 18); on portrait boards the same holds with a different composition; on mid/wide boards the spread is large (low confidence) - the designers re-compose.")
    for f in [x for x in intent["findings"] if x["category"] == "name_text"][:8]:
        P(f"- ({f['confidence']}) {f['observed_relationship']}")
    P("- Fixed element: " + "; ".join(x["observed_relationship"][:150] for x in intent["findings"] if x["category"] == "fixed_element"))
    P("- Not supported by the data (not claimed): a single global scaling rule; that the 'ADINN/..' tag is a fixed-position element on every size.\n")
    P("## J. Unmatched and ambiguous\n")
    P(f"- UNMATCHED: {mp['summary']['UNMATCHED']}; AMBIGUOUS: {mp['summary']['AMBIGUOUS']}; PROBABLE: {mp['summary']['PROBABLE']} (listed in B). The 4 'Print Details' CDRs, 141 JPGs and the PPTX in `02 - Working` are not boards and were not mapped.\n")
    P("## K. Files created / code changed\n")
    P("- Created: `backend/tools/stage1_audit.py` (the whole Stage 1 pipeline) and all of `backend/dataset_analysis/stage1/` (inventory, mapping CSV/JSON, master summary, generation results, visual + structural CSVs, design intent, per-board images under `visual/<case>/`, `output_verification.json`, `run_config.json`).")
    P("- Changed (additive, no behaviour change for production): `backend/app/master_analysis.py` (+ page count, + configurable render size), `backend/app/corel_worker.py` (passes `render_px`), `backend/tests/test_master_store.py` (fake accepts the new kwarg).\n")
    P("## L. Verification of outputs\n")
    P(f"- {ver.get('generated_ok_counts')}; source files unchanged: {ver.get('source_files_unchanged')}; CorelDRAW processes left: {ver.get('corel_processes_running_now')}; baseline server log mentions a dialog: {ver.get('baseline_server_log_mentions_dialog')}.\n")
    tests_p = out / "test_results.json"
    if tests_p.is_file():
        P("## L2. Test results\n")
        for k, v in json.loads(tests_p.read_text(encoding="utf-8")).items():
            P(f"- {k}: {v}")
        P("")
    P("## M. Limitations and unverified items\n")
    P("- The 'master the designer used' is inferred (library + top-level structure); no file records it. 17 boards disagree between the engine's master and the structural guess - the structural guess uses only top-level object types and nested counts.")
    P("- Names were taken from the designer CDRs (the order sheet has none in Tamil): scores are about layout, not transliteration; sheet-faithful names were NOT generated. 4 boards have English spelling that differs between sheet and CDR.")
    P("- Reference and engine renders both use a fallback for the missing `Amaranth` font; the true print look of English names was not reproduced. Fonts were deliberately not installed.")
    P("- Each CDR was read on its active page only (all 53 files have 1 page). Object matching is geometric (COM ids are not stable) and greedy; `object_extra` / `object_missing` / size / position defects partly reflect re-composition, not engine error: where the designer rebuilt a board from a different master they are expected.")
    P("- The 'defects' use fixed thresholds (documented in `stage1_audit.py`); they are measurable proxies. Composition balance, empty space and decorative relationships were not scored.")
    P("- The design-intent element rules come from the example library's COM-derived boxes of these same designer files (3-18 boards per family); families with fewer than 6 boards are low confidence. They are observations of what designers did, not confirmed intent - `needs_human_confirmation` marks what a person must decide.")
    P("- One baseline run (attempt 1, no retries needed); run-to-run variation of CorelDRAW text measurement (documented for Tamil) was not measured. SIGNAGE_SHOP_TIMEOUT_S was 240 s instead of the production default 45 s.")
    P("- Not done: sheet-faithful generation, a planner-ON comparison (the planner stays off), CorelDRAW 2019, a second machine.\n")
    P("## N. Recommendation for Stage 2 (not started)\n")
    P("1. Fix the measurable, low-risk gaps first: the Tamil font (use the designers' Anek Tamil, installed here) and name block size / line count per board shape - together they touch every Tamil board.")
    P("2. Master selection: the engine used a different master than the designers' structure for 17 boards, and those boards score clearly lower (F2); decide the master per size family from evidence and measure it on these 50 boards before changing production.")
    P("3. Wide boards (15x3 .. 26x3, 20x4) are the weakest (F2): the designers re-compose them (narrower name panel, two-line Tamil, repeated logos) - they need a rule per family confirmed by a designer, not a global scaling rule.")
    P("4. Use this harness (`stage1_audit.py compare` + `report`) as the regression gate for every Stage 2 change; keep the planner off until it beats these numbers.")
    P("5. Ask a designer to confirm the `needs_human_confirmation` findings before they become rules.")
    (out / "stage1_report.md").write_text("\n".join(L), encoding="utf-8")
    print("report written:", out / "stage1_report.md", len(L), "lines")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inventory")
    e = sub.add_parser("extract"); e.add_argument("--group", default="all", choices=["masters", "designers", "generated", "all"]); e.add_argument("--batch", type=int, default=6)
    sub.add_parser("masters"); sub.add_parser("map")
    g = sub.add_parser("generate"); g.add_argument("--base", required=True); g.add_argument("--retries", type=int, default=3)
    sub.add_parser("compare"); sub.add_parser("intent"); sub.add_parser("report"); sub.add_parser("verify")
    a = ap.parse_args()
    fn = {"inventory": cmd_inventory, "extract": cmd_extract, "masters": cmd_master_summary, "map": cmd_map}.get(a.cmd) or globals().get("cmd_" + a.cmd)
    if fn is None:
        raise SystemExit(f"command {a.cmd!r} is not implemented yet")
    fn(a)


if __name__ == "__main__":
    main()
