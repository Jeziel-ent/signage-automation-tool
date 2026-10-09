r"""Start the local Jenkins when it is not running, so a `git push` always gets built (the job polls GitHub every 2 minutes, but only while Jenkins is up).

  python ci/ensure_jenkins.py     returns at once; Jenkins needs about a minute to boot, then its next SCM poll finds the pushed commit

Used by .githooks/pre-push (git config core.hooksPath .githooks). It never fails: a problem here must not block a push.
Environment: JENKINS_URL (default http://localhost:7070), JENKINS_WAR (default D:\jenkins\jenkins.war), JENKINS_HOME (default D:\jenkins_home),
JAVA_HOME / java on PATH. Standard library only."""
import glob
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

URL = os.environ.get("JENKINS_URL", "http://localhost:7070").rstrip("/")
WAR = Path(os.environ.get("JENKINS_WAR", r"D:\jenkins\jenkins.war"))
HOME = os.environ.get("JENKINS_HOME", r"D:\jenkins_home")


def is_up() -> bool:
    """Jenkins answers (200 on the login page; 403 / 503 also mean the server is there, just not ready or not anonymous)."""
    try:
        urllib.request.urlopen(f"{URL}/login", timeout=4).close()
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def find_java() -> str | None:
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin" / "java.exe").is_file():
        return str(Path(home) / "bin" / "java.exe")
    found = shutil.which("java")
    if found:
        return found
    hits = sorted(glob.glob(r"C:\Program Files\Eclipse Adoptium\jdk-2*\bin\java.exe"), reverse=True)
    return hits[0] if hits else None


def main() -> int:
    if is_up():
        print("Jenkins is already running")
        return 0
    java = find_java()
    if not java or not WAR.is_file():
        print(f"Jenkins is down and cannot be started here (java: {java}, war: {WAR}) - set JAVA_HOME / JENKINS_WAR")
        return 0
    port = URL.rsplit(":", 1)[-1]
    flags = 0
    if os.name == "nt":                                  # detached: no console, survives the git process
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    log = open(WAR.parent / "jenkins.out.log", "ab")
    subprocess.Popen([java, "-jar", str(WAR), f"--httpPort={port}"], cwd=str(WAR.parent), env=dict(os.environ, JENKINS_HOME=HOME),
                     creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    print("Jenkins was down - started it in the background; it will build this push within a few minutes")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                               # never block a push
        print(f"ensure_jenkins: {e}")
        sys.exit(0)
