"""Dump + compare the boards of a run that still lack geometry (collided / failed dumps).   python tools/example_redump.py <brand> known|unseen"""
import json, os, sys
os.environ["SIGNAGE_DUMP_CLIPS"] = "1"
sys.path.insert(0, ".")
from app import example_layout as X
from tools.example_eval import OUT, dump_and_compare
brand, mode = sys.argv[1:3]
run = OUT / brand / mode
rows_path = run / "results.json"
rows = json.loads(rows_path.read_text(encoding="utf-8"))
dump_and_compare(brand, X.library(brand), rows, rows_path, run)
