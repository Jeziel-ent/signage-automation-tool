"""Run the example evaluations one after another (a single process tree: kill this one process to stop everything).
    python tools/eval_chain.py hangyo:known hangyo:unseen agarpathi:known agarpathi:unseen"""
import os, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT.parent / "signage_dataset"
SETS = {"hangyo": ("hangyo", DATA / "Hangyo"), "agarpathi": ("agarpathi", DATA / "Agarpathi"), "dalmia": ("dalmia_clips", DATA / "dalmia")}
env = dict(os.environ, SIGNAGE_KEEP_MASTER_OPEN="0")
for step in sys.argv[1:]:
    brand, mode, *rest = step.split(":")           # "agarpathi:known:20,24" = only those S.Nos
    cache, folder = SETS[brand]
    with open(ROOT / "dataset_analysis" / f"eval_{brand}_{mode}.log", "w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-u", "tools/example_eval.py", brand, cache, str(folder), mode, *(["--only", rest[0]] if rest else [])], cwd=ROOT, env=env,
                       stdout=log, stderr=subprocess.STDOUT)
