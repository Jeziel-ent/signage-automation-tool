"""Re-derive the geometry verdicts of an evaluation run from its saved dumps (no CorelDRAW).
    python tools/example_recompute.py <brand> known|unseen"""
import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import example_layout as X
from tools.example_eval import OUT, compare, dump_name, clean_name

brand, mode = sys.argv[1:3]
lib = X.library(brand)
boards = {b["file"]: b for b in lib["boards"]}
masters = {m["id"]: m for m in lib["masters"]}
run = OUT / brand / mode
rows = json.loads((run / "results.json").read_text(encoding="utf-8"))
n = 0
for r in rows:
    d = run / "ours_dumps" / dump_name(r)
    if r.get("status") != "done" or not d.exists():
        continue
    ours = X.board_record(masters[r["master"]], json.load(open(d, encoding="utf-8")), clean_name(r["name"]), r["file"])
    if ours is None:
        r["geometry_error"] = "generated board does not match the master's signatures"
        r.pop("geometry", None)
        continue
    r["geometry"] = compare(ours, boards[r["file"]]); r.pop("geometry_error", None); n += 1
(run / "results.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
print("recomputed", n, "of", len(rows))
