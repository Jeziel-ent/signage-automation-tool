"""Rewrite frontend/lcov.info so every `SF:` path is relative to the REPOSITORY root with forward slashes
(node writes paths relative to frontend/ with the OS separator; sonar-project.properties points at frontend/lcov.info and
SonarQube resolves them from the project root). Standard library only."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LCOV = ROOT / "frontend" / "lcov.info"

if not LCOV.is_file():
    sys.exit(f"{LCOV} not found - run ci/frontend_coverage.mjs first")

out, files = [], 0
for line in LCOV.read_text(encoding="utf-8").splitlines():
    if line.startswith("SF:"):
        p = line[3:].replace(chr(92), "/")
        if not re.match(r"^[A-Za-z]:", p) and not p.startswith("frontend/"):
            p = "frontend/" + p
        line = "SF:" + p
        files += 1
    out.append(line)
LCOV.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
print(f"lcov: {files} files rewritten relative to the repository root")
