"""Build the e-mail report for a finished build: ci-reports/email-report.html (and a plain-text twin).

  python ci/make_email_report.py <SUCCESS|UNSTABLE|FAILURE|ABORTED>

Reads what the pipeline produced (all optional - a build that failed early simply has less to show): ci-reports/quality-gate-report.json,
backend/pytest-report.xml, frontend/frontend-junit.xml, and Jenkins' BUILD_URL / BUILD_NUMBER / JOB_NAME environment. No recipient or
secret is ever written here. Standard library only."""
import html
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "ci-reports"
result = (sys.argv[1] if len(sys.argv) > 1 else "UNKNOWN").upper()
COLOR = {"SUCCESS": "#1a7f37", "UNSTABLE": "#9a6700", "FAILURE": "#cf222e", "ABORTED": "#57606a"}.get(result, "#57606a")
ICON = {"SUCCESS": "PASSED", "UNSTABLE": "UNSTABLE", "FAILURE": "FAILED", "ABORTED": "ABORTED"}.get(result, result)


def junit_counts(path):
    """(tests, failures, skipped) from a JUnit xml file, or None when it is missing/unreadable."""
    try:
        # count the test cases themselves: pytest writes summary attributes, node's reporter does not
        cases = list(ET.parse(path).getroot().iter("testcase"))
        failed = sum(1 for c in cases if c.find("failure") is not None or c.find("error") is not None)
        skipped = sum(1 for c in cases if c.find("skipped") is not None)
        return len(cases), failed, skipped
    except Exception:
        return None


def git(*args):
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


gate = load(OUT / "quality-gate-report.json")
be = junit_counts(ROOT / "backend" / "pytest-report.xml")
fe = junit_counts(ROOT / "frontend" / "frontend-junit.xml")
job, num, url = os.environ.get("JOB_NAME", "signage-automation-tool"), os.environ.get("BUILD_NUMBER", "?"), os.environ.get("BUILD_URL", "")
commit = git("log", "-1", "--format=%h|%an|%s")
sha, author, subject = (commit.split("|", 2) + ["", "", ""])[:3]
now = datetime.now().strftime("%Y-%m-%d %H:%M")


def test_line(name, c):
    if c is None:
        return f"{name}: not run (the build stopped before this stage)"
    t, f, s = c
    return f"{name}: {t - f - s} passed, {f} failed, {s} skipped (of {t})"


rows = [("Backend tests", test_line("", be)[2:]), ("Frontend tests", test_line("", fe)[2:])]
text = [f"{job} #{num} - {ICON}", f"{now}", f"Commit: {sha} {subject} ({author})" if sha else "", "", rows[0][0] + ": " + rows[0][1], rows[1][0] + ": " + rows[1][1]]
parts = []
parts.append(f'<div style="font-family:Segoe UI,Arial,sans-serif;max-width:680px">')
parts.append(f'<div style="background:{COLOR};color:#fff;padding:14px 18px;border-radius:6px 6px 0 0"><div style="font-size:12px;opacity:.85">{html.escape(job)} &middot; build #{html.escape(num)} &middot; {now}</div><div style="font-size:22px;font-weight:600">Pipeline {ICON}</div></div>')
parts.append('<div style="border:1px solid #d0d7de;border-top:0;padding:16px 18px;border-radius:0 0 6px 6px">')
if sha:
    parts.append(f'<p style="margin:0 0 12px"><b>Commit</b> <code>{html.escape(sha)}</code> {html.escape(subject)} <span style="color:#57606a">by {html.escape(author)}</span></p>')
parts.append('<table cellpadding="6" style="border-collapse:collapse;font-size:14px;width:100%">')
for k, v in rows:
    parts.append(f'<tr><td style="border-bottom:1px solid #eaeef2;width:150px"><b>{k}</b></td><td style="border-bottom:1px solid #eaeef2">{html.escape(v)}</td></tr>')

if gate:
    g = gate["status"]
    gc = "#1a7f37" if g == "OK" else "#cf222e"
    parts.append(f'<tr><td style="border-bottom:1px solid #eaeef2"><b>Quality gate</b></td><td style="border-bottom:1px solid #eaeef2;color:{gc}"><b>{html.escape(g)}</b></td></tr>')
    text.append(f"Quality gate: {g}")
    for c in gate.get("conditions", []):
        ops = {"LT": "<", "GT": ">"}.get(c.get("comparator"), c.get("comparator", ""))
        line = f"{c['metricKey']}: {c['status']} (actual {c.get('actualValue', '-')}, fails when {ops} {c.get('errorThreshold', '')})"
        parts.append(f'<tr><td style="border-bottom:1px solid #eaeef2;padding-left:24px;color:#57606a">condition</td><td style="border-bottom:1px solid #eaeef2">{html.escape(line)}</td></tr>')
        text.append("  " + line)
    m = gate.get("measures", {})
    labels = [("Bugs", "bugs"), ("Vulnerabilities", "vulnerabilities"), ("Code smells", "code_smells"), ("Coverage %", "coverage"), ("Duplication %", "duplicated_lines_density")]
    parts.append('<tr><td><b>Project</b></td><td>' + " &middot; ".join(f"{html.escape(a)}: <b>{html.escape(str(m.get(b, '-')))}</b>" for a, b in labels) + "</td></tr>")
    text.append("Project: " + ", ".join(f"{a} {m.get(b, '-')}" for a, b in labels))
else:
    parts.append('<tr><td><b>Quality gate</b></td><td style="color:#57606a">no report - analysis was skipped or the build stopped before it</td></tr>')
    text.append("Quality gate: no report (analysis skipped or an earlier stage failed)")
parts.append("</table>")
if url:
    parts.append(f'<p style="margin:16px 0 0"><a href="{html.escape(url)}">Open this build in Jenkins</a> &middot; <a href="{html.escape(url)}console">console output</a>')
    if gate and gate.get("dashboard"):
        parts.append(f' &middot; <a href="{html.escape(gate["dashboard"])}">SonarQube dashboard</a>')
    parts.append("</p>")
    text.append(f"Build: {url}")
parts.append('<p style="color:#8c959f;font-size:12px;margin:14px 0 0">Links open on the computer that runs Jenkins and SonarQube.</p></div></div>')
OUT.mkdir(exist_ok=True)
(OUT / "email-report.html").write_text("".join(parts), encoding="utf-8")
(OUT / "email-report.txt").write_text("\n".join(t for t in text if t != "") + "\n", encoding="utf-8")
print(f"wrote {OUT / 'email-report.html'} ({ICON})")
