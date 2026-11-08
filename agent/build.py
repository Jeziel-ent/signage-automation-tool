"""Build SignageAgent.exe (PyInstaller, one file, no console window) and agent/dist/latest.json.

    cd agent
    ..\\.venv\\Scripts\\python.exe -m pip install pyinstaller      # once
    ..\\.venv\\Scripts\\python.exe build.py [--min-supported 0.1.0] [--sign]

`--sign` code-signs the exe (Windows SmartScreen then stops warning designers) with a certificate you own:
    SIGNAGE_SIGN_PFX=C:\\certs\\adinn.pfx   SIGNAGE_SIGN_PASSWORD=...   [SIGNAGE_SIGN_TIMESTAMP=http://timestamp.digicert.com]
    [SIGNTOOL=path of signtool.exe]   (found automatically when the Windows SDK is installed)
The checksum in latest.json is taken AFTER signing, so agents verify exactly the signed file.

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
DEFAULT_TIMESTAMP = "http://timestamp.digicert.com"


def find_signtool(env: dict | None = None) -> str | None:
    """signtool.exe: $SIGNTOOL, else the newest one in the Windows SDK folders, else None."""
    import glob
    import os

    env = os.environ if env is None else env
    if env.get("SIGNTOOL") and Path(env["SIGNTOOL"]).is_file():
        return env["SIGNTOOL"]
    roots = [env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), env.get("ProgramFiles", r"C:\Program Files")]
    hits = sorted(h for r in roots for h in glob.glob(str(Path(r) / "Windows Kits" / "10" / "bin" / "*" / "x64" / "signtool.exe")))
    return hits[-1] if hits else None


def sign_command(signtool: str, exe: Path, pfx: str, password: str, timestamp: str = DEFAULT_TIMESTAMP) -> list[str]:
    """The signtool command line (SHA-256 file digest and timestamp digest, an RFC 3161 timestamp so the signature outlives the certificate)."""
    return [signtool, "sign", "/f", pfx, "/p", password, "/fd", "sha256", "/tr", timestamp, "/td", "sha256", "/d", "Adinn Signage Agent", str(exe)]


def sign(exe: Path, env: dict | None = None) -> None:
    import os

    env = os.environ if env is None else env
    pfx, password = env.get("SIGNAGE_SIGN_PFX", ""), env.get("SIGNAGE_SIGN_PASSWORD", "")
    if not pfx or not Path(pfx).is_file():
        raise SystemExit("--sign needs SIGNAGE_SIGN_PFX (the path of your code-signing certificate, a .pfx file)")
    tool = find_signtool(env)
    if not tool:
        raise SystemExit("--sign needs signtool.exe (install the Windows SDK, or set SIGNTOOL to its path)")
    subprocess.run(sign_command(tool, exe, pfx, password, env.get("SIGNAGE_SIGN_TIMESTAMP") or DEFAULT_TIMESTAMP), check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-supported", default="", help="oldest agent version still accepted (default: this version)")
    ap.add_argument("--sign", action="store_true", help="code-sign the exe (see the top of this file)")
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
                "tools.render_real_preview", "win32com.client", "win32api", "pythoncom", "pywintypes", "PIL.Image", "PIL.ImageDraw",
                "pystray", "pystray._win32"):
        cmd += ["--hidden-import", mod]
    # data the engine reads next to its code: brand rules, confidence bounds and each brand's example library
    cmd += ["--add-data", f"{backend / 'app' / 'brand_rules'}{sep}app/brand_rules",
            "--add-data", f"{backend / 'app' / 'confidence_bounds.json'}{sep}app"]
    for lib in sorted((backend / "brand_data").glob("*/library.json")):
        cmd += ["--add-data", f"{lib}{sep}brand_data/{lib.parent.name}"]
    cmd += ["--exclude-module", "numpy", "--exclude-module", "scipy", "--exclude-module", "tkinter", str(HERE / "run_agent.py")]
    subprocess.run(cmd, check=True, cwd=HERE)
    exe = dist / "SignageAgent.exe"
    if args.sign:
        sign(exe)
    sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    (dist / "latest.json").write_text(json.dumps({"version": AGENT_VERSION, "sha256": sha, "size": exe.stat().st_size,
                                                  "min_supported": args.min_supported or AGENT_VERSION}, indent=2), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    print(f"built {exe} ({exe.stat().st_size // 1024} KB) version {AGENT_VERSION} sha256 {sha[:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
