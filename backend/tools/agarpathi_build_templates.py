"""Build backend/brand_data/agarpathi/templates.json from the cached designer dumps (see agarpathi_elements.py).
One template per designer board: page size, family and every element's box as a fraction of the page."""
import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.agarpathi_elements import CACHE, elements

out = []
for f in sorted(CACHE.glob("*.json")):
    if "Copy" in f.name:
        continue
    e = elements(json.load(open(f, encoding="utf-8")))
    t = {"sno": int(f.name.split("_")[0]), "file": f.name, "W": round(e["W"], 2), "H": round(e["H"], 2)}
    for k, v in e.items():
        if isinstance(v, dict):
            t[k] = {kk: (round(vv, 5) if isinstance(vv, float) else vv) for kk, vv in v.items() if kk != "text"}
    out.append(t)
dest = ROOT / "brand_data" / "agarpathi"
dest.mkdir(parents=True, exist_ok=True)
(dest / "templates.json").write_text(json.dumps({"brand": "agarpathi", "boards": out}, ensure_ascii=False, indent=1), encoding="utf-8")
print(len(out), "templates ->", dest / "templates.json")
