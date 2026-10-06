"""Make sure the local SonarQube is up for the analysis stage, starting it when it is not running.

  python ci/ensure_sonarqube.py start   start SonarQube in the background if it is down, then return at once (it takes 2-3 minutes to boot;
                                        the Jenkinsfile runs this first so the boot overlaps with the tests)
  python ci/ensure_sonarqube.py wait    wait until SonarQube answers UP; exit 0 when it does, exit 3 when it never does

Environment: SONAR_HOST_URL (default http://localhost:9000), SONARQUBE_HOME (default D:\\sonarqube), SONAR_WAIT_SECONDS (default 420).
SonarQube is started detached (and marked "dontKillMe") so it keeps running after the Jenkins build ends. Standard library only."""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

URL = os.environ.get("SONAR_HOST_URL", "http://localhost:9000").rstrip("/")
HOME = Path(os.environ.get("SONARQUBE_HOME", r"D:\sonarqube"))
BAT = HOME / "bin" / "windows-x86-64" / "StartSonar.bat"
WAIT_S = int(os.environ.get("SONAR_WAIT_SECONDS", "420"))


def status():
    """SonarQube's own status (UP, STARTING, ...) or None when nothing answers."""
    try:
        with urllib.request.urlopen(f"{URL}/api/system/status", timeout=5) as r:
            return json.load(r).get("status")
    except Exception:
        return None


def start():
    s = status()
    if s == "UP":
        print("SonarQube is already running")
        return 0
    if s is not None:
        print(f"SonarQube is {s} - already starting")
        return 0
    if not BAT.is_file():
        print(f"SonarQube is down and {BAT} does not exist - set SONARQUBE_HOME. The analysis stage will be skipped.")
        return 0
    flags = 0
    if os.name == "nt":                                  # detach: no console, own process group, survives the build
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    env = dict(os.environ, BUILD_ID="dontKillMe", JENKINS_NODE_COOKIE="dontKillMe")   # Jenkins would otherwise kill it when the build ends
    subprocess.Popen(["cmd", "/c", str(BAT)], cwd=str(BAT.parent), env=env, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"SonarQube was down - started {BAT} in the background (takes 2-3 minutes)")
    return 0


def wait():
    deadline = time.time() + WAIT_S
    last = None
    while True:
        s = status()
        if s == "UP":
            print("SonarQube is UP")
            return 0
        if s != last:
            print(f"SonarQube status: {s or 'not answering'}")
            last = s
        if time.time() > deadline:
            print(f"SonarQube did not come up within {WAIT_S}s")
            return 3
        time.sleep(5)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "start"
    if mode not in ("start", "wait"):
        sys.exit("usage: ensure_sonarqube.py start|wait")
    sys.exit(start() if mode == "start" else wait())
