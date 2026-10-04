"""Offline leave-one-out of the example-library layout (app/example_layout.py), any brand.
    python tools/example_loo.py <brand> <dump_cache_dir> [seen|unseen] [--detail]
seen   : the board itself is hidden (boards of the same size remain - how a repeat size comes out)
unseen : every board of the same size is hidden (a size the designer never made)
A board PASSES when each element it has is within 2 % (centre) / 3 % (size) of the page of the designer's."""
import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import example_layout as X
from app.layout import Obj

POS, SIZE = 2.0, 3.0


def master_objs(dump):
    W, H = dump["page_mm"]["w"], dump["page_mm"]["h"]
    objs = []
    for i, s in enumerate(X.top_level(dump)):
        objs.append(Obj(str(i), s["name"] or f"o{i}", s["kind"], s["x"], s["y"], s["w"], s["h"], s.get("text") if s["kind"] == "text" else None, None, s["n_desc"]))
    return objs, W, H


def main():
    brand, cache = sys.argv[1], sys.argv[2]
    mode = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else "seen"
    lib = X.library(brand)
    dumps = {}
    for f in (ROOT / "dataset_analysis" / "real_dumps_cache" / cache).glob("*.json"):
        if not f.name.startswith("_"):
            d = json.load(open(f, encoding="utf-8")); dumps[d["file"]] = d
    mobjs = {m["id"]: master_objs(dumps[m["file"]]) for m in lib["masters"]}
    ok = n = 0
    rows = []
    for b in lib["boards"]:
        objs, W, H = mobjs[b["master"]]
        excl = [x["file"] for x in lib["boards"] if (x["file"] == b["file"] or (mode == "unseen" and X._same_size(x, b["W"], b["H"])))]
        pl = X.plan(brand, objs, W, H, b["W"], b["H"], exclude=excl)
        if pl is None:
            rows.append((b["file"][:40], "NO PLAN", [])); n += 1; continue
        errs = []
        by_key = {k: oid for oid, (x, y, w, h, k) in pl["boxes"].items()}
        for k, be in b["els"].items():
            vs = [v for v in pl["boxes"].values() if v[4] == k]
            if not vs:
                errs.append((k, "missing")); continue
            x = min(v[0] for v in vs); y = min(v[1] for v in vs)
            w = max(v[0] + v[2] for v in vs) - x; h = max(v[1] + v[3] for v in vs) - y
            if be["loose"]:
                dpos = max(abs((x + w / 2) / b["W"] - be["cx"]), abs((y + h / 2) / b["H"] - be["cy"])) * 100
                dsz = 0
            else:
                dpos = max(abs((x + w / 2) / b["W"] - be["cx"]), abs((y + h / 2) / b["H"] - be["cy"])) * 100
                dsz = max(abs(w / b["W"] - be["w"]), abs(h / b["H"] - be["h"])) * 100
            if dpos > POS or dsz > SIZE:
                errs.append((k, round(dpos, 1), round(dsz, 1)))
        pics = pl["clip"].get("bitmaps") or {}
        for k, t in (b.get("cbm") or {}).items():
            if t.get("bd") or k not in pics:
                continue
            x, y, w, h = pics[k][:4]
            dpos = max(abs((x + w / 2) / b["W"] - t["cx"]), abs((y + h / 2) / b["H"] - t["cy"])) * 100
            dsz = max(abs(w / b["W"] - t["w"]), abs(h / b["H"] - t["h"])) * 100
            if dpos > POS or dsz > SIZE:
                errs.append(("pic_" + k, round(dpos, 1), round(dsz, 1)))
        n += 1
        ok += not errs
        rows.append((b["file"][:44], "ok" if not errs else "bad", errs, pl["template"]["file"][:20]))
    print(f"{brand} [{mode}]: {ok}/{n} boards pass")
    if "--detail" in sys.argv:
        for r in rows:
            if r[1] != "ok": print(" ", r)


if __name__ == "__main__":
    main()
