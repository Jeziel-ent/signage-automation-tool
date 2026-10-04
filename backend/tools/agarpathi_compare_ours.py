"""Dump the generated CDRs of a fidelity run (with PowerClip contents) and compare every element with the designer board.
    python tools/agarpathi_compare_ours.py <run_dir>      -> <run_dir>/geometry.json"""
import json, os, sys, urllib.request
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["SIGNAGE_DUMP_CLIPS"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import corel_supervisor
from tools.agarpathi_elements import CACHE, elements

run = Path(sys.argv[1])
res = json.load(open(run / "results.json", encoding="utf-8"))
recent = json.loads(urllib.request.urlopen("http://localhost:8000/api/v2/recent").read())
by_shop = {r["shop_id"]: r for r in recent}
dumps = run / "ours_dumps"; dumps.mkdir(exist_ok=True)
jobs, meta = [], []
for r in res:
    if r.get("status") != "done":
        continue
    row = by_shop[r["shop_id"]]
    cdr = ROOT / "data" / "jobs_v2" / row["job_id"] / "out" / r["shop_id"] / row["files"]["cdr"]
    cp = dumps / f"{r['sno']}_{r['shop_id']}.json"
    meta.append((r, cp))
    if not cp.exists():
        jobs.append({"dump_only": str(cdr), "dump_cache": str(cp)})
for k in range(0, len(jobs), 5):
    corel_supervisor.run_batch(jobs[k:k + 5], run / "dump_results.json", overall_timeout_s=300,
                               on_progress=lambda i, e: print("dumped", Path(e.get("dump_only", "")).name[:40], e.get("status"), e.get("error"), flush=True))
out = []
for r, cp in meta:
    if not cp.exists():
        continue
    ours = elements(json.load(open(cp, encoding="utf-8")))
    des = next(elements(json.load(open(f, encoding="utf-8"))) for f in CACHE.glob(f"{r['sno']}_*.json")
               if abs(json.load(open(f, encoding='utf-8'))['page_mm']['w'] / json.load(open(f, encoding='utf-8'))['page_mm']['h'] - ours['aspect']) < 0.02)
    row = {"sno": r["sno"], "name": r["name"], "aspect": ours["aspect"], "elements": {}}
    for k in ("roof", "sugandha", "blackstone", "stick", "backdrop", "band", "composite", "name_en", "name_ta"):
        if k in ours and k in des:
            o, d = ours[k], des[k]
            row["elements"][k] = {"dcx": (o["cx"] - d["cx"]) * 100, "dcy": (o["cy"] - d["cy"]) * 100,
                                  "dw": (o["w"] - d["w"]) * 100, "dh": (o["h"] - d["h"]) * 100,
                                  "ours": [round(o[x], 3) for x in ("cx", "cy", "w", "h")], "designer": [round(d[x], 3) for x in ("cx", "cy", "w", "h")]}
        elif k in des:
            row["elements"][k] = {"missing_in_ours": True}
    out.append(row)
(run / "geometry.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
for row in out:
    print(row["sno"], row["name"][:22], round(row["aspect"], 2))
    for k, e in row["elements"].items():
        if "missing_in_ours" in e: print("   ", k, "MISSING"); continue
        flag = "" if max(abs(e["dcx"]), abs(e["dcy"])) <= 2 and max(abs(e["dw"]), abs(e["dh"])) <= 3 else "  <--"
        print(f"    {k:11s} dcx {e['dcx']:+5.1f} dcy {e['dcy']:+5.1f} dw {e['dw']:+5.1f} dh {e['dh']:+5.1f}{flag}")
