"""Entry point: `SignageAgent.exe` (double-click = install + run) or `python -m signage_agent --no-install --home DIR` (development)."""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path

from . import installer, local_server
from .agent import Agent
from .config import Config, install_dir
from .version import AGENT_VERSION


def _logging(home: Path, console: bool) -> None:
    home.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.handlers.RotatingFileHandler(home / "agent.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)


def _corel_worker(argv: list[str]) -> int:
    """`SignageAgent.exe --corel-worker <module> <jobs.json> <results.json>`: the exe acting as the CorelDRAW worker process that
    corel_supervisor starts (a frozen exe has no `python -m`). Runs in its own process so a CorelDRAW hang can be killed with it."""
    import importlib

    from . import engine_bridge

    module, jobs, results = argv[0], argv[1], argv[2]
    engine_bridge.prepare(install_dir() / "data")
    sys.argv = [module, jobs, results]
    importlib.import_module(module).main()
    return 0


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "--corel-worker" and len(raw) >= 4:
        return _corel_worker(raw[1:])
    ap = argparse.ArgumentParser(prog="SignageAgent")
    ap.add_argument("--home", help="data folder (development / tests); default %%LOCALAPPDATA%%\AdinnSignageAgent")
    ap.add_argument("--no-install", action="store_true", help="run from here without installing (development)")
    ap.add_argument("--installed", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--after-update", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--uninstall", action="store_true", help="remove the start-with-Windows entry")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)

    if args.version:
        print(AGENT_VERSION)
        return 0
    if args.uninstall:
        installer.uninstall()
        return 0
    if args.home:
        os.environ["SIGNAGE_AGENT_HOME"] = args.home
    frozen = getattr(sys, "frozen", False)
    if frozen and not args.no_install and not installer.is_installed_copy():
        return 0 if installer.install_and_launch() else 1

    home = Path(args.home) if args.home else install_dir()
    _logging(home, console=not frozen)
    # a second start (the user double-clicked again, or Windows ran it twice) quietly does nothing; right after an update
    # the old process may need a moment to let go of the lock
    deadline = time.time() + (15 if (args.after_update or args.installed) else 0)
    while not installer.single_instance():
        if time.time() >= deadline:
            logging.getLogger("agent").info("already running - exiting")
            return 0
        time.sleep(0.5)

    agent = Agent(Config(home))
    if local_server.start(agent) is None:
        logging.getLogger("agent").error("could not open a local status port; the website will not detect this agent")
    try:
        agent.run()
    except KeyboardInterrupt:
        agent.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
