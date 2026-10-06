"""Quality gate stage: wait for the SonarQube analysis that the scanner just uploaded, write a report, and fail (exit 1) when the
quality gate is not OK. Reads .scannerwork/report-task.txt (written by the scanner) and SONAR_TOKEN from the environment.
Writes ci-reports/quality-gate-report.md, ci-reports/quality-gate-report.json and ci-reports/summary.txt (one line, shown as the
build description). Standard library only - no SonarQube plugin or webhook is needed."""
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / ".scannerwork" / "report-task.txt"
OUT = ROOT / "ci-reports"
TIMEOUT_S = 300
METRICS = ["bugs", "vulnerabilities", "code_smells", "security_hotspots", "coverage", "duplicated_lines_density", "ncloc"]
LABELS = {"bugs": "Bugs", "vulnerabilities": "Vulnerabilities", "code_smells": "Code smells", "security_hotspots": "Security hotspots",
          "coverage": "Coverage (%)", "duplicated_lines_density": "Duplication (%)", "ncloc": "Lines of code"}

token = os.environ.get("SONAR_TOKEN")
if not token:
    sys.exit("SONAR_TOKEN is not set (bind the 'sonar-token' credential)")
if not REPORT.is_file():
    sys.exit(f"{REPORT} not found - did the SonarQube analysis stage run?")

task = dict(line.split("=", 1) for line in REPORT.read_text(encoding="utf-8").splitlines() if "=" in line)
auth = "Basic " + base64.b64encode((token + ":").encode()).decode()


def get(url):
    req = urllib.request.Request(url, headers={"Authorization": auth})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


deadline = time.time() + TIMEOUT_S
while True:
    ce = get(task["ceTaskUrl"])["task"]
    if ce["status"] in ("SUCCESS", "FAILED", "CANCELED"):
        break
    if time.time() > deadline:
        sys.exit(f"SonarQube did not finish processing within {TIMEOUT_S}s (status {ce['status']})")
    time.sleep(3)
if ce["status"] != "SUCCESS":
    sys.exit(f"SonarQube analysis task ended as {ce['status']}")

server, key = task["serverUrl"], task["projectKey"]
gate = get(f"{server}/api/qualitygates/project_status?analysisId={ce['analysisId']}")["projectStatus"]
measures = {m["metric"]: m.get("value") for m in
            get(f"{server}/api/measures/component?component={urllib.parse.quote(key)}&metricKeys={','.join(METRICS)}")["component"]["measures"]}
dashboard = task.get("dashboardUrl") or f"{server}/dashboard?id={key}"
status = gate["status"]

# ---- report ----------------------------------------------------------------------------------------------------------------------------
OUT.mkdir(exist_ok=True)
now = datetime.now().strftime("%Y-%m-%d %H:%M")
build = os.environ.get("BUILD_NUMBER", "local")
OPS = {"LT": "<", "GT": ">", "EQ": "=", "NE": "!="}
lines = [f"# Quality gate report - {status}", "", f"Project `{key}`, build {build}, {now}", "", f"Dashboard: {dashboard}", "",
         "## Gate conditions", "", "| Condition | Result | Actual | Fails when |", "|---|---|---|---|"]
for c in gate.get("conditions", []):
    lines.append(f"| {c['metricKey']} | {c['status']} | {c.get('actualValue', '-')} | {OPS.get(c.get('comparator'), c.get('comparator', ''))} {c.get('errorThreshold', '')} |")
lines += ["", "## Project measures", "", "| Measure | Value |", "|---|---|"]
lines += [f"| {LABELS[m]} | {measures.get(m, '-')} |" for m in METRICS]
(OUT / "quality-gate-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
(OUT / "quality-gate-report.json").write_text(
    json.dumps({"status": status, "build": build, "time": now, "dashboard": dashboard, "conditions": gate.get("conditions", []), "measures": measures}, indent=2),
    encoding="utf-8")
failed = [c["metricKey"] for c in gate.get("conditions", []) if c["status"] != "OK"]
summary = f"Quality gate {status}" + (f" ({', '.join(failed)})" if failed else "") + f" - coverage {measures.get('coverage', '?')}%, bugs {measures.get('bugs', '?')}, smells {measures.get('code_smells', '?')}"
(OUT / "summary.txt").write_text(summary + "\n", encoding="utf-8")

print("\n".join(lines))
print(f"\n{summary}")
sys.exit(0 if status == "OK" else 1)
