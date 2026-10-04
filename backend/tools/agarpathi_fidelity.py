"""Agarpathi fidelity run: designer JPEG (from the comparison deck) vs a fresh automated conversion.

Drives the running API (so the UI shows every shop): adds each shop to the Agarpathi brand's masters, converts it
with the real CorelEngine, downloads the preview PNG and scores it against the designer image.
    python tools/agarpathi_fidelity.py [--only S.No,S.No] [--out DIR]
"""
from __future__ import annotations
import argparse, json, re, subprocess, sys, time, zipfile, urllib.request, urllib.parse
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from tools.metrics import visual_similarity, load_config
from app.batch_import import parse_shop_lines

API = "http://localhost:8000"
DECK = ROOT / "Designer JPEGs and Automated Generated.pptx"
REF = ROOT / "backend" / "dataset_analysis" / "agarpathi_fidelity"
DATASET = ROOT / "signage_dataset" / "Agarpathi"


def http(method, path, body=None):
    req = urllib.request.Request(API + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


def tamil(names):
    js = "import('./frontend/src/utils/tamilTranslit.js').then(m=>console.log(JSON.stringify(JSON.parse(process.argv[1]).map(m.toTamil))))"
    out = subprocess.run(["node", "-e", js, json.dumps(names)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return json.loads(out.stdout)


def designer_refs():
    """{(sno, w, h): designer jpg path} from the comparison deck (slide text -> image-N-1.jpg)."""
    REF.mkdir(parents=True, exist_ok=True)
    z = zipfile.ZipFile(DECK)
    refs = {}
    for n in z.namelist():
        m = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", n)
        if not m or int(m.group(1)) < 4:
            continue
        i = int(m.group(1))
        t = re.findall(r"<a:t>([^<]*)", z.read(n).decode())
        sno = int(t[0].split()[-1]); size = re.match(r"(\d+) X (\d+) (Inch|Feet)", t[2])
        p = REF / f"designer_{i}.jpg"
        if not p.exists():
            p.write_bytes(z.read(f"ppt/media/image-{i}-1.jpg"))
        refs[(sno, int(size[1]), int(size[2]), size[3])] = (p, t[1].replace("&amp;", "&"))
    return refs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only"); ap.add_argument("--out", default=str(REF / "run"))
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    only = {int(x) for x in a.only.split(",")} if a.only else None
    masters = http("GET", "/api/masters?brand=Agarpathi")
    land, port = masters["landscape"][0], masters["portrait"][0]
    print("masters:", land["id"], port["id"])
    refs = designer_refs()
    todo = []
    for (sno, w, h, unit), (path, name) in sorted(refs.items()):
        if only and sno not in only:
            continue
        todo.append((sno, w, h, unit, path, name))
    tamils = tamil([t[5] for t in todo])
    cfg = load_config(); results = []
    rf = out / "results.json"
    for (sno, w, h, unit, ref_path, name), ta in zip(todo, tamils):
        ratio = w / h
        m = land if ratio >= 1.25 else port
        shop = http("POST", f"/api/v2/jobs/{m['id']}/shops", {
            "name": name, "width": w, "height": h, "unit": "ft" if unit == "Feet" else "in",
            "shop_name_local": ta, "board_type": "Nonlit", "master_id": m["id"],
            "landscape_master_id": land["id"], "portrait_master_id": port["id"]})
        t0 = time.time()
        http("POST", f"/api/v2/shops/{shop['id']}/convert", {})
        status = {}
        while time.time() - t0 < 400:
            time.sleep(2)
            status = http("GET", f"/api/v2/shops/{shop['id']}/status")
            if status.get("status") in ("done", "error"):
                break
        rec = {"sno": sno, "name": name, "size": f"{w}x{h} {unit}", "shop_id": shop["id"], "status": status.get("status"),
               "seconds": round(time.time() - t0, 1), "error": status.get("error")}
        if status.get("status") == "done":
            png = out / f"auto_{sno}_{w}x{h}.png"
            urllib.request.urlretrieve(f"{API}/api/v2/shops/{shop['id']}/files/preview.png", png) if False else None
            files = http("GET", f"/api/v2/shops/{shop['id']}/downloads") if False else None
            # preview: use the generic file route with the report's preview name
            rep = status.get("report") or {}
            rec_row = next(r for r in http("GET", "/api/v2/recent") if r["shop_id"] == shop["id"])
            pv = rec_row["files"]["preview"]
            urllib.request.urlretrieve(f"{API}/api/v2/shops/{shop['id']}/files/{urllib.parse.quote(Path(pv).name)}", png)
            vs = visual_similarity(png, ref_path, cfg)
            rec.update({"png": str(png), "ref": str(ref_path), "visual": vs, "warnings": rep.get("warnings", [])})
        print(sno, name[:30], rec["status"], rec["seconds"], rec.get("visual", {}).get("combined"))
        results.append(rec)
        rf.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
