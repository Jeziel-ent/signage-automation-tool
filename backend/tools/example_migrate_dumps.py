"""Rename ours_dumps from the old '<safe[:60]>.json' to the unique names of example_eval.dump_name."""
import json, sys
sys.path.insert(0, ".")
from tools.example_eval import OUT, dump_name
brand, mode = sys.argv[1:3]
run = OUT / brand / mode
rows = json.loads((run / "results.json").read_text(encoding="utf-8"))
n = 0
for r in rows:
    old, new = run / "ours_dumps" / f"{r['safe'][:60]}.json", run / "ours_dumps" / dump_name(r)
    if old.exists() and not new.exists() and not any(x is not r and x["safe"][:60] == r["safe"][:60] and x["file"] != r["file"] for x in rows):
        old.rename(new); n += 1
print("renamed", n)
