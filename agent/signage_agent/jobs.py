"""Doing the website's CorelDRAW work on this PC: claim convert jobs, fetch the masters, run them, upload the files, report the result.

One batch at a time (CorelDRAW is a single-instance desktop app, exactly as on the server). While a batch runs a small pump thread tells the
site which step the active job is on (read from the worker's heartbeat file - the same file the website's own progress bar uses) and keeps
every claimed job's lease alive, so a long master open never lets the site think the agent died.
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import zipfile
from pathlib import Path

from . import client, engine_bridge

log = logging.getLogger("agent.jobs")
KEEPALIVE_S = 20
PUMP_S = 1.5
CACHE_KEEP = 15            # masters kept for the next conversion (a master is 100-350 MB)
UPLOAD_TRIES = 3


class JobRunner:
    def __init__(self, agent, run_batch=None, run_mock=None, mock: bool | None = None, poll_s: float = 4.0):
        self.agent = agent
        self._run_batch = run_batch or engine_bridge.run_batch
        self._run_mock = run_mock or engine_bridge.run_mock
        self.mock = engine_bridge.mock_mode() if mock is None else mock
        self.poll_s = poll_s
        self._stop = threading.Event()

    # ---- when to ask for work
    def can_work(self) -> bool:
        return self.agent.config.paired and (self.mock or bool(self.agent.corel_installs()))

    def loop(self) -> None:
        while not self._stop.wait(self.poll_s):
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 - the loop must outlive any one bad round
                log.exception("job round failed")
                self.agent.state = "idle"

    def stop(self) -> None:
        self._stop.set()

    # ---- one round
    def poll_once(self) -> int:
        cfg = self.agent.config
        if not self.can_work() or self.agent.state != "idle":
            return 0
        try:
            jobs = client.claim_jobs(cfg.server_url, cfg.token)
        except client.ApiError as e:
            if e.status != 401:                      # a 401 is dealt with by the heartbeat (the PC was disconnected)
                self.agent.last_error = e.detail
            return 0
        if not jobs:
            return 0
        self.agent.state = "busy"
        try:
            self._run(jobs)
        finally:
            self.agent.state = "idle"
        return len(jobs)

    # ---- running what was claimed
    def _run(self, jobs: list[dict]) -> None:
        if jobs[0].get("kind", "convert") == "convert":
            return self._run_convert(jobs)
        for job in jobs:                                           # a scene build or an export is always claimed alone
            self._run_single(job)

    def _run_convert(self, jobs: list[dict]) -> None:
        cfg = self.agent.config
        work = cfg.home / "work" / jobs[0]["id"]
        work.mkdir(parents=True, exist_ok=True)
        pump = _Pump(cfg.server_url, cfg.token, [j["id"] for j in jobs])
        pump.start()
        try:
            prepared: list[tuple[dict, dict]] = []                     # (job, runnable)
            for job in jobs:
                try:
                    master = self._fetch_master(job)
                except Exception as e:  # noqa: BLE001
                    self._fail(job, f"could not download the master: {e}")
                    continue
                prepared.append((job, {"master_path": str(master), "shop": job["shop"], "out_dir": str(work / job["id"])}))
            if not prepared:
                return
            pump.ids = [j["id"] for j, _ in prepared]
            entries = self._convert(prepared, work, pump)
            for (job, run), entry in zip(prepared, entries):
                self._deliver(job, run, entry)
        finally:
            pump.stop()
            shutil.rmtree(work, ignore_errors=True)

    def _fetch_master(self, job: dict) -> Path:
        cfg = self.agent.config
        m = job["master"]
        cache = cfg.home / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        dest = cache / f"{m['key']}-{Path(m['filename']).name}"
        if dest.is_file() and dest.stat().st_size == m["size"]:
            dest.touch()                                               # recently used
            return dest
        client.download_master(cfg.server_url, cfg.token, job["id"], dest, m["size"])
        self._prune_cache(cache)
        return dest

    @staticmethod
    def _prune_cache(cache: Path) -> None:
        files = sorted((p for p in cache.iterdir() if p.is_file() and not p.name.endswith(".part")), key=lambda p: p.stat().st_mtime)
        for p in files[:-CACHE_KEEP]:
            p.unlink(missing_ok=True)

    def _convert(self, prepared: list[tuple[dict, dict]], work: Path, pump: "_Pump") -> list[dict]:
        if self.mock:
            out = []
            for i, (job, run) in enumerate(prepared):
                pump.step_for(i, "converting")
                try:
                    out.append({"status": "done", "result": self._run_mock(run)})
                except Exception as e:  # noqa: BLE001
                    out.append({"status": "error", "error": str(e)})
            return out
        runs = [r for _, r in prepared]
        results_path = work / "batch.json"
        pump.heartbeat = engine_bridge.heartbeat_path(results_path)
        try:
            results = self._run_batch(runs, results_path)
        except Exception as e:  # noqa: BLE001 - e.g. RefusedToStart (not enough free RAM): every job fails with the reason
            return [{"status": "error", "error": str(e)} for _ in runs]
        # a batch member that failed after the first is retried alone in a fresh worker + CorelDRAW (it may have failed only because the
        # reused instance did) - the same rule the website's own conversions follow
        for i, entry in enumerate(results):
            if i > 0 and entry.get("status") != "done":
                log.warning("job %s failed in a batch (%s); retrying it alone", prepared[i][0]["id"], entry.get("error"))
                single = work / f"retry{i}.json"
                pump.heartbeat, pump.offset = engine_bridge.heartbeat_path(single), i
                try:
                    results[i] = self._run_batch([runs[i]], single)[0]
                except Exception as e:  # noqa: BLE001
                    results[i] = {"status": "error", "error": str(e)}
        return results

    # ---- a scene build or an export (the editor)
    def _run_single(self, job: dict) -> None:
        cfg = self.agent.config
        work = cfg.home / "work" / job["id"]
        work.mkdir(parents=True, exist_ok=True)
        pump = _Pump(cfg.server_url, cfg.token, [job["id"]])
        pump.start()
        try:
            inputs = self._fetch_inputs(job, work / "in")
            if job["kind"] == "scene_build":
                self._scene_build(job, inputs, work, pump)
            elif job["kind"] == "export":
                self._export(job, inputs, work, pump)
            else:
                self._fail(job, f"this agent cannot do {job['kind']!r} jobs - update the agent")
        except Exception as e:  # noqa: BLE001 - whatever went wrong is the job's failure, with the reason
            log.exception("job %s (%s) failed", job["id"], job["kind"])
            self._fail(job, str(e) or type(e).__name__)
        finally:
            pump.stop()
            shutil.rmtree(work, ignore_errors=True)

    def _fetch_inputs(self, job: dict, folder: Path) -> dict[str, Path]:
        """Download every input of the job; `asset:<name>` files (the designer's product images) go to folder/assets under their own names."""
        cfg = self.agent.config
        folder.mkdir(parents=True, exist_ok=True)
        got: dict[str, Path] = {}
        for name, meta in (job.get("inputs") or {}).items():
            if name.startswith("asset:"):
                dest = folder / "assets" / Path(meta["filename"]).name
                dest.parent.mkdir(exist_ok=True)
            else:
                dest = folder / Path(meta["filename"]).name
            client.download_input(cfg.server_url, cfg.token, job["id"], name, dest, meta["size"])
            got[name] = dest
        return got

    def _scene_build(self, job: dict, inputs: dict[str, Path], work: Path, pump: "_Pump") -> None:
        scene_dir = work / "scene"
        spec = job.get("spec") or {}
        if self.mock:
            pump.step_for(0, "images 1/1")
            engine_bridge.mock_scene(scene_dir, float(spec.get("width_mm") or 3000), float(spec.get("height_mm") or 1000))
        else:
            results_path = work / "scene.json"
            pump.heartbeat = engine_bridge.heartbeat_path(results_path)
            entry = engine_bridge.run_spec({"scene_export": str(inputs["cdr"]), "scene_out": str(scene_dir)}, results_path)
            if entry.get("status") != "done":
                raise RuntimeError(entry.get("error", "scene export failed"))
        archive = work / "scene.zip"
        _zip_scene(scene_dir, archive)
        self._send_files(job, {"scene": archive}, {})

    def _export(self, job: dict, inputs: dict[str, Path], work: Path, pump: "_Pump") -> None:
        spec = dict(job.get("spec") or {})
        out_dir = work / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        results_path = work / "export.json"
        pump.heartbeat = engine_bridge.heartbeat_path(results_path)
        if self.mock:
            report = engine_bridge.mock_export(inputs["scene"], spec, out_dir, pump.heartbeat)
        else:
            run = {"cdr": str(inputs["cdr"]), "scene": str(inputs["scene"]), "ops": spec.get("ops") or [], "formats": spec["formats"],
                   "options": spec["options"], "out_dir": str(out_dir), "base_name": spec["base_name"],
                   "assets_dir": str(work / "in" / "assets"), "font_subs": spec.get("font_subs") or {}}
            entry = engine_bridge.run_spec({"export_replay": run}, results_path)
            if entry.get("status") != "done":
                raise RuntimeError(entry.get("error", "export failed"))
            report = entry["report"]
        self._send_files(job, {kind: out_dir / name for kind, name in (report.get("files") or {}).items()}, report)

    def _send_files(self, job: dict, files: dict[str, Path], report: dict) -> None:
        """Upload the named files of a finished scene build / export, then report it done."""
        cfg = self.agent.config
        sent: dict[str, str] = {}
        try:
            client.report_progress(cfg.server_url, cfg.token, job["id"], "uploading")
            for kind, path in files.items():
                if Path(path).is_file():
                    self._upload(job["id"], Path(path))
                    sent[kind] = Path(path).name
            client.post_result(cfg.server_url, cfg.token, job["id"], "done", sent, report)
        except client.ApiError as e:
            if e.status == 409:
                log.warning("job %s was taken back by the site before the results arrived", job["id"])
                return
            raise RuntimeError(f"could not upload the results: {e.detail}") from None

    # ---- sending the outcome
    def _fail(self, job: dict, reason: str) -> None:
        cfg = self.agent.config
        try:
            client.post_result(cfg.server_url, cfg.token, job["id"], "error", error=reason)
        except client.ApiError as e:
            log.warning("could not report the failure of job %s: %s", job["id"], e)

    def _deliver(self, job: dict, run: dict, entry: dict) -> None:
        cfg = self.agent.config
        if entry.get("status") != "done":
            return self._fail(job, str(entry.get("error") or "the conversion failed"))
        result = entry["result"]
        files: dict[str, str] = {}
        try:
            client.report_progress(cfg.server_url, cfg.token, job["id"], "uploading")
            for kind, name in (result.get("files") or {}).items():
                p = Path(str(name))
                if not p.is_absolute():                   # an engine result names its files relative to the job's output folder
                    p = Path(run["out_dir"]) / p
                if not p.is_file():
                    continue
                self._upload(job["id"], p)
                files[kind] = p.name
            client.post_result(cfg.server_url, cfg.token, job["id"], "done", files, result.get("report") or {})
        except client.ApiError as e:
            if e.status == 409:                      # the lease ran out and the job went back to the queue: another round redoes it
                log.warning("job %s was taken back by the site before the results arrived", job["id"])
                return
            self._fail(job, f"could not upload the results: {e.detail}")

    def _upload(self, job_id: str, path: Path) -> None:
        cfg = self.agent.config
        for attempt in range(1, UPLOAD_TRIES + 1):
            try:
                client.upload_file(cfg.server_url, cfg.token, job_id, path.name, path)
                return
            except client.ApiError as e:
                if e.status not in (0, 502, 503, 504) or attempt == UPLOAD_TRIES:
                    raise
                time.sleep(2 * attempt)


def _zip_scene(scene_dir: Path, archive: Path) -> None:
    """The scene folder (scene.json, img/*, page.png) as one zip with scene.json LAST, so the site shows the scene only once it is whole."""
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(scene_dir.rglob("*")):
            if f.is_file() and f.name != "scene.json":
                z.write(f, f.relative_to(scene_dir).as_posix())
        z.write(scene_dir / "scene.json", "scene.json")


class _Pump(threading.Thread):
    """Reports the active job's step to the site and keeps every claimed job's lease alive."""

    def __init__(self, server: str, token: str, ids: list[str]):
        super().__init__(name="job-pump", daemon=True)
        self.server, self.token, self.ids = server, token, ids
        self.heartbeat: Path | None = None
        self.offset = 0
        self._done = threading.Event()
        self._last_step: tuple[int, str] | None = None
        self._last_keep = time.time()

    def stop(self) -> None:
        self._done.set()

    def step_for(self, index: int, step: str) -> None:
        self._send(index, step)

    def _send(self, index: int, step: str) -> None:
        if 0 <= index < len(self.ids):
            try:
                client.report_progress(self.server, self.token, self.ids[index], step)
            except client.ApiError:
                pass                                 # a missed beat is not an error; the next one extends the lease

    def run(self) -> None:
        while not self._done.wait(PUMP_S):
            step, idx = None, self.offset
            if self.heartbeat is not None and self.heartbeat.exists():
                try:
                    hb = json.loads(self.heartbeat.read_text(encoding="utf-8"))
                    step, idx = hb.get("step"), int(hb.get("job_index", 0)) + self.offset      # a retry batch holds one job: offset = its place
                except (OSError, ValueError):
                    pass
            now = time.time()
            if step and (idx, step) != self._last_step:
                self._last_step = (idx, step)
                self._send(idx, step)
            if now - self._last_keep >= KEEPALIVE_S:             # every claimed job, so a long step never lets a lease run out
                for i in range(len(self.ids)):
                    self._send(i, "")
                self._last_keep = now
