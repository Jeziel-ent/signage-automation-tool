"""Build SignageAgent.exe (PyInstaller, one file, no console window) and agent/dist/latest.json.

    cd agent
    ..\\.venv\\Scripts\\python.exe -m pip install pyinstaller      # once
    ..\\.venv\\Scripts\\python.exe build.py [--min-supported 0.1.0]

The website serves agent/dist (SIGNAGE_AGENT_DIST) and offers it as a zip together with a config.json naming the site. Raise the version in
signage_agent/version.py for every release: agents update themselves to whatever latest.json says. `min_supported` is the oldest
version the server still lets work; an older agent is told to update at its next heartbeat.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-supported", default="", help="oldest agent version still accepted (default: this version)")
    args = ap.parse_args()
    sys.path.insert(0, str(HERE))
    from signage_agent.version import AGENT_VERSION

    dist, work = HERE / "dist", HERE / "build"
    backend = HERE.parent / "backend"
    sep = ";" if sys.platform == "win32" else ":"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--noconsole", "--name", "SignageAgent",
           "--distpath", str(dist), "--workpath", str(work), "--specpath", str(work), "--paths", str(HERE), "--paths", str(backend)]
    # the website's CorelDRAW engine (backend/app) runs inside the exe: imported lazily / by name, so PyInstaller must be told
    for mod in ("app.corel_supervisor", "app.corel_worker", "app.engines", "app.export_replay", "app.scene_export", "app.corel_util",
                "app.layout", "app.example_layout", "app.confidence", "app.corrections", "app.file_naming", "app.name_check",
                "app.orientation_adapter", "app.content_check", "app.print_file", "app.scene_ops", "tools.dump_objects",
                "tools.render_real_preview", "win32com.client", "win32api", "pythoncom", "pywintypes", "PIL.Image"):
        cmd += ["--hidden-import", mod]
    # data the engine reads next to its code: brand rules, confidence bounds and each brand's example library
    cmd += ["--add-data", f"{backend / 'app' / 'brand_rules'}{sep}app/brand_rules",
            "--add-data", f"{backend / 'app' / 'confidence_bounds.json'}{sep}app"]
    for lib in sorted((backend / "brand_data").glob("*/library.json")):
        cmd += ["--add-data", f"{lib}{sep}brand_data/{lib.parent.name}"]
    cmd += ["--exclude-module", "numpy", "--exclude-module", "scipy", "--exclude-module", "tkinter", str(HERE / "run_agent.py")]
    subprocess.run(cmd, check=True, cwd=HERE)
    exe = dist / "SignageAgent.exe"
    sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    (dist / "latest.json").write_text(json.dumps({"version": AGENT_VERSION, "sha256": sha, "size": exe.stat().st_size,
                                                  "min_supported": args.min_supported or AGENT_VERSION}, indent=2), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    print(f"built {exe} ({exe.stat().st_size // 1024} KB) version {AGENT_VERSION} sha256 {sha[:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
