"""Background watchdog: logs any top-level window owned by a given PID
(CorelDRAW's process), plus its child control text, and screenshots the
screen when a new one appears. Pure win32gui/win32process - never touches
the COM `app`/`doc` objects, so it's safe to run from a background thread
alongside a blocking COM call in the main thread.

Two uses:
  - dev diagnosis (backend/tools/diagnose_save.py, test_saveas_variants.py):
    just logs/screenshots, never dismisses anything.
  - production (CorelEngine, behind SIGNAGE_COREL_WATCHDOG=1): pass
    `dismiss_after_s` so a dialog that's been up that long gets its title
    and full text logged, a screenshot saved, and is closed via its own
    default button (found by the BS_DEFPUSHBUTTON style, not guessed by
    position/text) so the job can continue instead of hanging forever.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from pathlib import Path

import win32con
import win32gui
import win32process

DEFAULT_SHOT_DIR = Path(__file__).resolve().parents[1] / "dataset_analysis" / "diagnose"
# Never treated as a dialog, whatever the CorelDRAW version: its main frame ("CorelDRAW21" in 2019, "CorelDRAW27"-style in newer
# builds - the class name carries the version, so matching one literal name broke on every other version) and helper windows it
# creates. Verified live on 27.0.0.121 launched hidden: its only visible top-level window is an untitled "Internet Explorer_Hidden".
# The old check (class != "CorelDRAW21") let that helper through, so on every conversion longer than 20 s the watchdog "dismissed"
# it with WM_CLOSE - 23 real reports carried "dialog '' open >=20s, dismissed via WM_CLOSE (no default button found)".
MAIN_FRAME_RE = re.compile(r"^CorelDRAW\d+$")
HELPER_CLASSES = {"Internet Explorer_Hidden", "MSCTFIME UI", "IME", "tooltips_class32", "GDI+ Hook Window Class"}


def is_dialog(info: dict) -> bool:
    """Whether a top-level window of CorelDRAW's process could be a blocking dialog: not the main frame, not a known helper, and
    showing something a person could act on (a title or child controls). An untitled window with no controls is not a dialog."""
    cls = info.get("class") or ""
    if MAIN_FRAME_RE.match(cls) or cls in HELPER_CLASSES:
        return False
    return bool((info.get("title") or "").strip() or info.get("children"))


BS_DEFPUSHBUTTON = 0x0001
BS_TYPEMASK = 0x000F


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _child_windows(hwnd: int) -> list[int]:
    children = []
    try:
        win32gui.EnumChildWindows(hwnd, lambda c, _: children.append(c) or True, None)
    except Exception:
        pass
    return children


def _child_texts(hwnd: int) -> list[str]:
    texts = []
    for child in _child_windows(hwnd):
        try:
            t = win32gui.GetWindowText(child)
            cls = win32gui.GetClassName(child)
            if t or cls:
                texts.append(f"[{cls}] {t!r}")
        except Exception:
            pass
    return texts


def _find_default_button(hwnd: int) -> int | None:
    """The child button with the BS_DEFPUSHBUTTON style - the one Enter
    would trigger - rather than guessing by button text (which varies
    across CorelDRAW's dialogs and localizations).
    """
    import win32api

    for child in _child_windows(hwnd):
        try:
            if win32gui.GetClassName(child) != "Button":
                continue
            style = win32api.GetWindowLong(child, win32con.GWL_STYLE)
            if (style & BS_TYPEMASK) == BS_DEFPUSHBUTTON:
                return child
        except Exception:
            continue
    return None


def _windows_for_pid(pid: int) -> dict[int, dict]:
    found = {}

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        try:
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return True
        if wpid != pid:
            return True
        title = win32gui.GetWindowText(hwnd)
        cls = win32gui.GetClassName(hwnd)
        if not title and not cls:
            return True
        found[hwnd] = {"title": title, "class": cls, "children": _child_texts(hwnd)}
        return True

    win32gui.EnumWindows(_cb, None)
    return found


class Watchdog:
    """Call start(pid), then read .log (list of str) / .events / .dismissed
    any time - all appended to live from the background thread. Call stop()
    when done (returns the list of dismissed-dialog events).

    Pass `log_fn` to also have each line written immediately to the caller's
    own (flushed-to-disk) log, so a dialog is visible on disk right away
    even if the main thread is still blocked on the COM call that caused it.

    Pass `dismiss_after_s` to enable auto-dismiss: a window still open that
    long after first being seen gets logged (title + every child control's
    text - covers the dialog's message and button labels), screenshotted,
    and closed via its own default button. Windows belonging to the main
    CorelDRAW application frame (any version, MAIN_FRAME_RE) and its helper windows are never dismissed -
    only secondary dialogs.
    """


    def __init__(self, pid: int, interval_s: float = 2.0, log_fn=None,
                 dismiss_after_s: float | None = None, shot_dir: Path | None = None):
        self.pid = pid
        self.interval_s = interval_s
        self.log: list[str] = []
        self.events: list[dict] = []
        self.dismissed: list[dict] = []
        self._log_fn = log_fn
        self._dismiss_after_s = dismiss_after_s
        self._shot_dir = shot_dir or DEFAULT_SHOT_DIR
        self._first_seen: dict[int, float] = {}
        self._dismissed_hwnds: set[int] = set()
        self._seen: set[int] = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._shot_dir.mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> list[dict]:
        self._stop.set()
        self._thread.join(timeout=5)
        return self.dismissed

    def _emit(self, line: str) -> None:
        self.log.append(line)
        if self._log_fn:
            try:
                self._log_fn(line)
            except Exception:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                windows = _windows_for_pid(self.pid)
                now = time.time()
                for hwnd, info in windows.items():
                    if hwnd not in self._seen:
                        self._seen.add(hwnd)
                        self._first_seen[hwnd] = now
                        self._emit(f"[{_ts()}] NEW WINDOW hwnd={hwnd} class={info['class']!r} title={info['title']!r}")
                        for c in info["children"]:
                            self._emit(f"           child: {c}")
                        self.events.append({"hwnd": hwnd, **info})
                        # dev diagnosis only: in production (dismiss mode) every job would otherwise save a screenshot
                        # into its shop's output folder (it did - one per conversion, of CorelDRAW's hidden helper window)
                        if self._dismiss_after_s is None and is_dialog(info):
                            self._screenshot(hwnd, "new")

                    if (self._dismiss_after_s is not None
                            and hwnd not in self._dismissed_hwnds
                            and is_dialog(info)
                            and now - self._first_seen[hwnd] >= self._dismiss_after_s):
                        self._dismiss(hwnd, info)
            except Exception as e:
                self._emit(f"[{_ts()}] watchdog error: {e}")
            self._stop.wait(self.interval_s)

    def _dismiss(self, hwnd: int, info: dict) -> None:
        self._dismissed_hwnds.add(hwnd)
        self._emit(f"[{_ts()}] DIALOG STUCK >= {self._dismiss_after_s:.0f}s: "
                   f"title={info['title']!r} children={info['children']}")
        self._screenshot(hwnd, "stuck")
        event = {"hwnd": hwnd, "title": info["title"], "class": info["class"], "children": info["children"]}
        button = _find_default_button(hwnd)
        try:
            if button is not None:
                win32gui.SendMessage(button, win32con.BM_CLICK, 0, 0)
                event["dismissed_via"] = "default_button"
                self._emit(f"[{_ts()}] clicked default button on hwnd={hwnd}")
            else:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                event["dismissed_via"] = "WM_CLOSE (no default button found)"
                self._emit(f"[{_ts()}] no default button found on hwnd={hwnd}; sent WM_CLOSE")
        except Exception as e:
            event["dismissed_via"] = f"failed: {e}"
            self._emit(f"[{_ts()}] could not dismiss hwnd={hwnd}: {e}")
        self.dismissed.append(event)

    def _screenshot(self, hwnd: int, tag: str) -> None:
        try:
            from PIL import ImageGrab

            path = self._shot_dir / f"dialog_{tag}_{hwnd}_{int(time.time())}.png"
            # only the dialog itself, never the whole screen (which records whatever else the user has open)
            ImageGrab.grab(bbox=win32gui.GetWindowRect(hwnd), all_screens=True).save(path)
            self._emit(f"[{_ts()}] screenshot -> {path}")
        except Exception as e:
            self._emit(f"[{_ts()}] screenshot failed: {e}")
