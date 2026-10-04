"""Real-engine evaluation of the example-library layout against a brand's designer files (any brand).
    python tools/example_eval.py <brand> <dump_cache_dir> <dataset_dir> known|unseen [--only 16,27] [--limit N]
known  = production behaviour (the designer board of the same size is in the library)
unseen = boards of the SAME SIZE as the target are hidden from the library (a size the designer never made)
Phase 1 generates every board with the real CorelEngine (corel_supervisor worker, master kept open between shops).
Phase 2 COM-dumps each generated .cdr (with PowerClip contents) in separate batches and compares it element by element with
the designer's CDR (the library's own record of it).  Reference picture = the preview stored inside the designer's CDR."""
from __future__ import annotations

import json
import os
import re
import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["SIGNAGE_DUMP_CLIPS"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import corel_supervisor  # noqa: E402
from app import example_layout as X  # noqa: E402
from app.batch_import import parse_shop_lines  # noqa: E402
from tools.build_example_library import name_indexes, shop_name_of  # noqa: E402
from tools.metrics import load_config, visual_similarity  # noqa: E402
from tools.validate_all import _safe  # noqa: E402

OUT = ROOT / "dataset_analysis" / "example_eval"
EXACT = (2.0, 3.0)   # % of the page: centre / size
CLOSE = (5.0, 6.0)
KEYS_NOTE = "names: Tamil compared by right edge + height (the designer's Tamil spelling differs from the transliteration)"


def tamil(names):
    import subprocess
    js = "import('./frontend/src/utils/tamilTranslit.js').then(m=>console.log(JSON.stringify(JSON.parse(process.argv[1]).map(m.toTamil))))"
    out = subprocess.run(["node", "-e", js, json.dumps(names)], cwd=ROOT.parent, capture_output=True, text=True, encoding="utf-8")
    return json.loads(out.stdout)


def compare(ours: dict, des: dict) -> dict:
    """Per-element deviations in % of the page between two board records (example_layout.board_record).
    Not scored: the paper backdrop and the footer band (a designer stretches them freely; they only have to cover the page)."""
    res = {}
    for k, d in des["els"].items():
        o = ours["els"].get(k)
        if o is None:
            res[k] = {"missing": True}
            continue
        loose = d["loose"]
        res[k] = {"dx": (o["cx"] - d["cx"]) * 100, "dy": (o["cy"] - d["cy"]) * 100,
                  "dw": 0.0 if loose else (o["w"] - d["w"]) * 100, "dh": 0.0 if loose else (o["h"] - d["h"]) * 100}
    for k, dc in (des.get("copies") or {}).items():              # repeated copies of an element, matched left to right
        oc = sorted((ours.get("copies") or {}).get(k, []), key=lambda t: t["cx"])
        for i, d in enumerate(sorted(dc, key=lambda t: t["cx"])):
            o = oc[i] if i < len(oc) else None
            res[f"{k}_copy{i + 1}"] = ({"missing": True} if o is None else
                                       {"dx": (o["cx"] - d["cx"]) * 100, "dy": (o["cy"] - d["cy"]) * 100,
                                        "dw": (o["w"] - d["w"]) * 100, "dh": (o["h"] - d["h"]) * 100})
    dcb = des.get("cbm") or {}
    if dcb:
        for k, d in dcb.items():             # the clipped pictures (sticks, box, table, ...), each by its own proportions
            if d.get("bd"):
                continue                      # the paper backdrop only has to cover the page
            o = (ours.get("cbm") or {}).get(k)
            res["pic_" + k] = ({"missing": True} if o is None else
                               {"dx": (o["cx"] - d["cx"]) * 100, "dy": (o["cy"] - d["cy"]) * 100,
                                "dw": (o["w"] - d["w"]) * 100, "dh": (o["h"] - d["h"]) * 100})
    elif des["clip"].get("composite"):
        d, o = des["clip"]["composite"], ours["clip"].get("composite")
        res["composite"] = ({"missing": True} if o is None else
                            {"dx": (o["cx"] - d["cx"]) * 100, "dy": (o["cy"] - d["cy"]) * 100,
                             "dw": (o["w"] - d["w"]) * 100, "dh": (o["h"] - d["h"]) * 100})
    for k, d in des["texts"].items():
        o = ours["texts"].get(k)
        if o is None:
            res[k] = {"missing": True}
            continue
        # the line is positioned by the edge it is anchored to: centre (stacked boards), left half -> left edge, else right edge
        if abs(d["cx"] - 0.5) < 0.12:
            dx = o["cx"] - d["cx"]
        elif d["cx"] < 0.5:
            dx = o["x"] - d["x"]
        else:
            dx = (o["x"] + o["w"]) - (d["x"] + d["w"])
        dh = (o["h"] - d["h"]) * 100          # a name's block height follows how many lines its text wraps into: only a big gap counts
        res[k] = {"dx": dx * 100, "dy": (o["cy"] - d["cy"]) * 100, "dw": 0.0, "dh": dh if abs(dh) > 15 else 0.0}
    return res


NAME_RELAX = 4.0               # position tolerance multiplier for shop-name lines
COMPOSITE_RELAX = (1.5, 5.0)   # the table/box composite may differ more: designers stretch it, the engine keeps it in proportion


def verdict(cmp: dict, tol) -> tuple[bool, list]:
    bad = []
    for k, e in cmp.items():
        if e.get("missing"):
            bad.append((k, "missing"))
            continue
        pt, st = (tol[0] * COMPOSITE_RELAX[0], tol[1] * COMPOSITE_RELAX[1] / 3) if k == "composite" else tol
        if k.startswith("name"):
            pt = tol[0] * NAME_RELAX       # she centres each name by eye: her own boards of one size differ by 5-10 % of the page
        if max(abs(e["dx"]), abs(e["dy"])) > pt or max(abs(e["dw"]), abs(e["dh"])) > st:
            bad.append((k, round(max(abs(e["dx"]), abs(e["dy"]), abs(e["dw"]), abs(e["dh"])), 1)))
    return not bad, bad


def cdr_preview(cdr: Path, dest: Path) -> Path | None:
    try:
        z = zipfile.ZipFile(cdr)
        name = next(n for n in ("previews/page1.png", "previews/thumbnail.png") if n in z.namelist())
        dest.write_bytes(z.read(name))
        return dest
    except Exception:
        return None


def main():
    brand, cache, dataset, mode = sys.argv[1:5]
    only = {int(x) for x in sys.argv[sys.argv.index("--only") + 1].split(",")} if "--only" in sys.argv else None
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    lib = X.library(brand)
    run = OUT / brand / mode
    run.mkdir(parents=True, exist_ok=True)
    (run / "refs").mkdir(exist_ok=True)
    files = {f.name: f for f in Path(dataset).rglob("*.cdr")}
    masters = {m["id"]: files[m["file"]] for m in lib["masters"]}
    jobs, meta = [], []
    for b in lib["boards"]:
        f = files.get(b["file"])
        if f is None:
            continue
        parsed = parse_shop_lines(f.stem).shops
        if not parsed:                       # a bare master ("6 X 3.cdr") is not a shop board
            continue
        shop = parsed[0]
        if only and (b.get("sno") not in only):
            continue
        safe = _safe(f.stem)
        sd = {"name": shop.name, "width": b["W"], "height": b["H"], "unit": "mm", "brand": brand,
              "master_shop_name": lib["masters"][int(b["master"][1:])].get("shop_name") or shop_name_of(lib["masters"][int(b["master"][1:])]["file"]),
              "file_base": f"{(b.get('sno') or 0):02d}_{safe[:60]}"}
        names = set(b.get("texts") or {})                   # the designer's board shows one language or both
        if names == {"name_ta"}:
            sd["language"] = "ta"
        elif names == {"name_en"}:
            sd["language"] = "en"
        sd["name"] = re.sub(r"\s*\(\d+\)\s*$", "", sd["name"])      # "(1)" / "(2)" mark the double-sided variants
        if mode == "unseen":
            sd["template_exclude"] = [x["file"] for x in lib["boards"] if X._same_size(x, b["W"], b["H"])]
        ref = cdr_preview(f, run / "refs" / f"{safe[:70]}.png")
        out_dir = run / "out" / safe
        if len(str(out_dir)) + len(sd["file_base"]) > 190:     # CorelDRAW cannot save to a path beyond ~260 characters
            out_dir = run / "out" / f"{b.get('sno') or 0}_{b['W']:.0f}x{b['H']:.0f}"
            sd["file_base"] = f"{(b.get('sno') or 0):02d}_{_safe(shop.name)[:40]}"
        jobs.append({"master_path": str(masters[b["master"]]), "shop": sd, "out_dir": str(out_dir)})
        meta.append({"sno": b.get("sno"), "file": b["file"], "name": shop.name, "w": shop.width, "h": shop.height,
                     "unit": shop.unit, "master": b["master"], "safe": safe, "ref": str(ref) if ref else None})
    order = sorted(range(len(jobs)), key=lambda i: (meta[i]["master"], meta[i]["sno"] or 0))   # one master at a time
    jobs, meta = [jobs[i] for i in order], [meta[i] for i in order]
    if limit:
        jobs, meta = jobs[:limit], meta[:limit]
    for j, ta in zip(jobs, tamil([j["shop"]["name"] for j in jobs])):
        j["shop"]["shop_name_local"] = ta
    cfg = load_config()
    rows_path = run / "results.json"
    rows = json.loads(rows_path.read_text(encoding="utf-8")) if rows_path.exists() else []
    done = {r["file"] for r in rows if r.get("status") == "done"}
    rows[:] = [r for r in rows if r["file"] in done]
    todo = [(j, m) for j, m in zip(jobs, meta) if m["file"] not in done]
    print(f"{brand}/{mode}: {len(todo)} boards to generate ({len(done)} done)", flush=True)
    chunks = [todo[k:k + 5] for k in range(0, len(todo), 5)]
    for pass_no in (1, 2):
        if pass_no == 2:   # a batch member that failed on a reused CorelDRAW is retried alone in a fresh one (as the app does)
            bad = {r["file"] for r in rows if r["status"] != "done"}
            chunks = [[x] for x in todo if x[1]["file"] in bad]
            rows[:] = [r for r in rows if r["status"] == "done"]
            print(f"retrying {len(chunks)} failed boards alone", flush=True)
        for chunk in chunks:
            def on_progress(i, entry, chunk=chunk):
                j, m = chunk[i]
                row = dict(m)
                row["status"], row["seconds"], row["error"] = entry.get("status"), entry.get("seconds"), entry.get("error")
                if entry.get("status") == "done":
                    res = entry["result"]
                    row["out_dir"] = j["out_dir"]
                    row["warnings"] = res["report"].get("warnings", [])
                    row["files"] = res["files"]
                    pv = res["files"].get("preview")
                    png = Path(pv) if pv and Path(pv).is_absolute() else (Path(j["out_dir"]) / Path(pv).name if pv else None)
                    row["png"] = str(png) if png else None
                    try:
                        if png and png.exists() and png.suffix.lower() == ".png" and m["ref"]:
                            row["visual"] = visual_similarity(png, m["ref"], cfg)
                    except Exception as e:
                        row["visual_error"] = repr(e)
                rows.append(row)
                rows_path.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
                print(f"[{len(rows)}] {m['sno']} {m['name'][:28]} {row['status']} {row.get('seconds')}s "
                      f"visual={row.get('visual', {}).get('combined')}", flush=True)
            try:
                corel_supervisor.run_batch([c[0] for c in chunk], run / "worker_results.json", overall_timeout_s=400,
                                           job_timeout_s=240, on_progress=on_progress)
            except corel_supervisor.RefusedToStart as e:
                print("REFUSED:", e, flush=True)
                break
    dump_and_compare(brand, lib, rows, rows_path, run)


def clean_name(n: str) -> str:
    """"Sri Sai cafe (1)": the (1) / (2) marks the double-sided variants, it is not part of the shop name."""
    return re.sub(r"\s*\(\d+\)\s*$", "", n)


def dump_name(r: dict) -> str:
    """Unique per board (boards can share sno, size and a name prefix: the S.No 76 pair, '(1)' / '(2)' variants)."""
    import hashlib
    return f"{r['sno']}_{r['w']:g}x{r['h']:g}_{hashlib.md5(r['file'].encode()).hexdigest()[:8]}.json"


def dump_and_compare(brand, lib, rows, rows_path, run):
    todo = [r for r in rows if r.get("status") == "done" and not r.get("geometry")]
    dumps = run / "ours_dumps"
    dumps.mkdir(exist_ok=True)
    jobs = []
    for r in todo:
        cdr = Path(r["files"]["cdr"])
        cdr = cdr if cdr.is_absolute() else Path(r.get("out_dir") or run / "out" / r["safe"]) / cdr.name
        if not cdr.exists():                                  # the engine shortens very long names on save
            found = [p for p in cdr.parent.glob("*.cdr") if not p.stem.lower().startswith("backup_of")]
            cdr = found[0] if found else cdr
        r["_dump"] = dumps / dump_name(r)
        if not r["_dump"].exists():
            jobs.append({"dump_only": str(cdr), "dump_cache": str(r["_dump"])})
    print(f"dumping {len(jobs)} generated files", flush=True)
    for k in range(0, len(jobs), 5):
        try:
            corel_supervisor.run_batch(jobs[k:k + 5], run / "dump_results.json", overall_timeout_s=400,
                                       on_progress=lambda i, e: print("  dumped", Path(e.get("dump_only", "")).name[:45], e.get("status"), flush=True))
        except corel_supervisor.RefusedToStart as e:
            print("REFUSED:", e, flush=True)
            break
    boards = {b["file"]: b for b in lib["boards"]}
    masters = {m["id"]: m for m in lib["masters"]}
    for r in todo:
        d = r.pop("_dump")
        try:
            dump = json.load(open(d, encoding="utf-8"))
            des = boards[r["file"]]
            ours = X.board_record(masters[r["master"]], dump, clean_name(r["name"]), r["file"])
            if ours is None:
                r["geometry_error"] = "generated board does not match the master's signatures"
                continue
            # the generated file's own text lines: English by name, Tamil by script
            r["geometry"] = compare(ours, des)
        except Exception as e:
            r["geometry_error"] = repr(e)
    rows_path.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
