"""CDR engines.

CorelEngine  - real automation of CorelDRAW through COM (Windows only).
MockEngine   - development stand-in for Linux/macOS: uses a demo scene and
               produces SVG previews so the whole site can be exercised
               without CorelDRAW.

Both expose:  process(master_path, shop, out_dir) -> dict
"""
from __future__ import annotations

import re

import atexit
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from . import corel_util
from .corel_watchdog import Watchdog
from .layout import (_norm_name, MIN_TEXT_PT, TAMIL_FONT, Obj, compute_layout, detect_role, find_contact_ids, find_local_partner_ids,
                     find_shopname_ids, is_tamil, load_brand_rule, brand_rule_for_shop, to_mm)

logger = logging.getLogger(__name__)


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_") or "shop"


# ---------------------------------------------------------------- persistent master session
# Opening the master costs 2.7 s (median, 208 MB DARSHAN master) and closing it ~1.5 s, on every shop. Within one worker
# batch the same master is therefore kept open: each shop's edits run inside ONE undo group (Document.BeginCommandGroup /
# EndCommandGroup), the outputs are written (SaveAs / PublishToPDF / ExportBitmap never touch the master file on disk), the
# group is undone, and the document is compared with a fingerprint taken right after the master was opened. Only an exact
# match keeps it for the next shop; anything else closes it and the next shop reopens from disk - so a shop can never
# inherit the previous shop's text, sizes or tiles. SIGNAGE_KEEP_MASTER_OPEN=0 turns this off.

def keep_master_open() -> bool:
    return os.environ.get("SIGNAGE_KEEP_MASTER_OPEN", "1").strip() != "0"


class _MasterSession:
    doc = None
    pid: int | None = None
    key: tuple | None = None        # (resolved path, mtime_ns, size) of the master file it was opened from
    snapshot: tuple | None = None   # doc_fingerprint() of the pristine master


def _master_key(master_path) -> tuple:
    p = Path(master_path).resolve()
    st = p.stat()
    return (str(p).lower(), st.st_mtime_ns, st.st_size)


class _Coll:
    """A plain list with the COM collection interface (Count / 1-based Item)."""
    def __init__(self, items):
        self._items = items
        self.Count = len(items)

    def Item(self, i):
        return self._items[i - 1]


def doc_fingerprint(doc) -> tuple:
    """Everything a shop's conversion can change, for the whole active page: page size, and per shape (recursively into
    groups and PowerClip contents) its type, box (0.01 mm), text + font + size, uniform fill, and a bitmap's pixel size.
    Two fingerprints are equal only if the document is back exactly as the master was."""
    doc.Unit = CorelEngine.CDR_MILLIMETER
    page = doc.ActivePage
    out = [("page", round(float(page.SizeWidth), 2), round(float(page.SizeHeight), 2))]

    def walk(coll, depth):
        for i in range(1, coll.Count + 1):
            s = coll.Item(i)
            t = int(s.Type)
            row = [depth, t, round(float(s.LeftX), 2), round(float(s.BottomY), 2),
                   round(float(s.SizeWidth), 2), round(float(s.SizeHeight), 2)]
            if t == CorelEngine.SHAPE_TEXT:
                try:
                    st = s.Text.Story
                    row += [st.Text, st.Font, round(float(st.Size), 2)]
                except Exception:
                    row.append(CorelEngine._shape_text(s))
            elif t == CorelEngine.SHAPE_BITMAP:
                try:
                    row += [int(s.Bitmap.SizeWidth), int(s.Bitmap.SizeHeight)]
                except Exception:
                    pass
            elif depth == 0:
                # only top-level shapes are ever recoloured (Placed.recolor_cmyk); reading a fill costs ~7 COM calls, so
                # nested shapes skip it
                row.append(CorelEngine._shape_fill_cmyk(s))
            out.append(tuple(row))
            kids = CorelEngine._child_shapes(s)
            if kids:
                walk(_Coll(kids), depth + 1)

    walk(page.Shapes, 0)
    return tuple(out)


FINGERPRINT_TOL_MM = 0.05   # CorelDRAW re-measures text after an undo: a width read back 1370.06 instead of 1370.07 mm


def same_fingerprint(a: tuple, b: tuple, tol: float = FINGERPRINT_TOL_MM) -> bool:
    """Equal up to `tol` on every number (boxes, page size, point sizes); text, font, fill and bitmap pixels must match
    exactly (they are ints/strings/tuples, compared as they are)."""
    if len(a) != len(b):
        return False
    for ra, rb in zip(a, b):
        if len(ra) != len(rb):
            return False
        for x, y in zip(ra, rb):
            if isinstance(x, float) and isinstance(y, float):
                if abs(x - y) > tol:
                    return False
            elif x != y:
                return False
    return True


def _close_doc(doc) -> None:
    """Close without a save-changes prompt (the master on disk is never saved over)."""
    try:
        doc.Dirty = False
    except Exception:
        pass
    try:
        doc.Close()
    except Exception:
        pass


def close_master_session() -> None:
    if _MasterSession.doc is not None:
        _close_doc(_MasterSession.doc)
    _MasterSession.doc = _MasterSession.pid = _MasterSession.key = _MasterSession.snapshot = None


atexit.register(close_master_session)   # registered after corel_util's, so it runs first: the doc closes before the quit


def shop_language(shop: dict, warnings: list[str] | None = None) -> str:
    """'both' | 'en' | 'ta': which shop-name line(s) the board keeps (shop["language"], default both). Tamil Only without a
    Tamil name would leave the MASTER's own Tamil shop name as the only name on the board, so it becomes English Only, with
    a warning."""
    mode = str(shop.get("language") or "both").strip().lower()
    if mode not in ("en", "ta"):
        return "both"
    if mode == "ta" and not (shop.get("shop_name_local") or "").strip():
        if warnings is not None:
            warnings.append("Tamil Only was chosen but this shop has no Tamil name - kept the English name instead")
        return "en"
    return mode


def name_font(text: str | None, shop: dict) -> str | None:
    """The font to set on a replaced shop-name text: only an EXPLICIT override (`font_ta` for Tamil text, `font_en` for
    anything else - an API caller's choice); otherwise None, i.e. only the text is replaced and the master's own font, size
    and attributes stay. (Fonts are changed in the Signage Editor, not in the queue.) A Tamil replacement whose master font
    cannot draw Tamil at all is still switched by corel_util.ensure_tamil_font_renders, or it would print tofu boxes."""
    if not text:
        return None
    chosen = shop.get("font_ta") if is_tamil(text) else shop.get("font_en")
    return (str(chosen).strip() or None) if chosen else None


def apply_name_fonts(placed, shop: dict) -> None:
    """Replaced shop names keep the master's font unless the shop carries an explicit override (see `name_font`) - this also
    drops layout's own Tamil default (layout.TAMIL_FONT), so the master's Tamil font is kept when it can draw Tamil."""
    for p in placed:
        if p.role == "shopname" and p.text:
            p.font = name_font(p.text, shop)


CR, LF = chr(13), chr(10)      # CorelDRAW artistic text breaks a line with a carriage return


def _output_base(shop: dict) -> str:
    """Output file stem: the caller's `file_base` ("76 - 125 X 48 Inch - Nonlit - SHOP", see file_naming.py) when given,
    else the shop name made filesystem-safe (the old /api/jobs flow and the dev tools)."""
    base = str(shop.get("file_base") or "").strip()
    return base or _safe(shop["name"])


def _report(page, new, placed, timings=None, warnings=None, free_ram_gb=None) -> dict:
    report = {
        "original_page_mm": {"w": page[0], "h": page[1]},
        "new_page_mm": {"w": new[0], "h": new[1]},
        "objects": [p.to_dict() for p in placed],
    }
    src = next((p.layout_source for p in placed if getattr(p, "layout_source", None)), None)
    if src:
        report["layout"] = src
    if timings is not None:
        report["timings_s"] = timings
    if warnings is not None:
        report["warnings"] = warnings
    if free_ram_gb is not None:
        report["free_ram_gb"] = round(free_ram_gb, 2)
    return report


# --------------------------------------------------------------------------
class CorelEngine:
    """Drives CorelDRAW via COM. Requires Windows, CorelDRAW and pywin32."""

    name = "corel"

    # CorelDRAW enum values (cdrUnit / cdrFilter / cdrExportRange / cdrShapeType)
    CDR_MILLIMETER = 3
    CDR_PNG = 802
    CDR_CURRENT_PAGE = 1  # cdrExportRange.cdrCurrentPage (2 is cdrSelection)
    CDR_RGB_IMAGE = 4  # cdrImageType.cdrRGBColorImage
    SHAPE_TEXT = 6
    SHAPE_BITMAP = 5
    SHAPE_GROUP = 7

    PREVIEW_MAX_PX = 1600  # cap the preview PNG's longest side

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("CorelEngine needs Windows + CorelDRAW")
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401

    def process(self, master_path: Path, shop: dict, out_dir: Path, on_step=None) -> dict:
        # COM stays initialised on this thread for the thread's lifetime - NOT CoUninitialize()d per job. The pooled CorelDRAW proxy
        # lives in this apartment; tearing it down after every job disconnected it ("Object is not connected to server", verified
        # live), so Quit() silently failed and every conversion waited out the 15 s force-kill, and a pooled instance could never
        # be reused. See corel_util.ensure_com.
        corel_util.ensure_com()
        return self._process(master_path, shop, out_dir, on_step)

    @staticmethod
    def _shape_text(s) -> str | None:
        try:
            return s.Text.Story.Text
        except Exception:
            return None

    @staticmethod
    def _shape_fill_cmyk(s) -> tuple | None:
        """Best-effort: a shape's uniform fill colour in CMYK (0-100 each),
        rounded to whole numbers - None for any other fill type (or an
        unreadable one). Reading `.RGBRed` etc. on a CMYK-model colour
        raises "Incompatible color model", confirmed live - CMYK is read
        directly instead of trying RGB first, since every sampled shape in
        this master's logos uses CMYK. Used by `_place_panel_sequence`'s
        `card_from` to tell a genuinely white detail (styled for the
        master's dark page background) from the icon's own multi-coloured
        curves, so only the former gets recoloured for its new white card.
        """
        try:
            fill = s.Fill
            if int(fill.Type) != 1:  # cdrUniformFill
                return None
            c = fill.UniformColor
            return (round(c.CMYKCyan), round(c.CMYKMagenta), round(c.CMYKYellow), round(c.CMYKBlack))
        except Exception:
            return None

    # Moved to corel_util.ensure_tamil_font_renders() so app.export_replay's
    # Replayer can share the exact same fix for a `text` op's target
    # (including one nested inside a PowerClip) without importing this whole
    # engine class. Kept as a thin alias here since call sites throughout
    # this file already say `self._ensure_tamil_font_renders(...)`.
    _ensure_tamil_font_renders = staticmethod(corel_util.ensure_tamil_font_renders)

    @staticmethod
    def _set_replacement_text(shape, placed) -> None:
        """Write the replacement text (and font, for Tamil content) via COM,
        then fit it into the space the layout actually gave this shape.

        CorelDRAW does not raise when `Font` names a font that isn't
        installed - Text.Story.Font silently reads back "" afterwards - so
        the write is verified rather than trusted (see layout.TAMIL_FONT).
        """
        if not (placed.text or "").strip():
            # a line of a split shop name the new name has no words left for (layout.split_name_lines)
            try:
                shape.Delete()
            except Exception as e:
                placed.warnings.append(f"could not remove an unused shop-name line: {e}")
            return
        try:
            story = shape.Text.Story
            story.Text = placed.text
            if placed.font:
                story.Font = placed.font
                if story.Font != placed.font:
                    placed.warnings.append(f"font '{placed.font}' not available on this machine; kept original font")
        except Exception as e:
            placed.warnings.append(f"could not set shop-name text: {e}")
            return
        # the master's font is kept - unless it is Tamil text in a font that has no Tamil letters (tofu boxes otherwise)
        corel_util.ensure_tamil_font_renders(shape, placed.text, placed.warnings)
        if placed.text_fit:
            CorelEngine._apply_text_fit(shape, placed.text_fit, placed.warnings)
            return
        CorelEngine._fit_text(shape, placed.w, placed.warnings, allow_wrap=not placed.no_wrap)

    CLIP_FOREGROUND_MAX_AREA = 0.9   # of the page: bigger clipped children are backdrop and keep the frame's stretch
    CLIP_FOREGROUND_MIN_INSIDE = 0.9  # share of the child's box inside the page: art hanging off the page is backdrop
    CLIP_EDGE_TOL = 0.02              # of the master's width: a child this close to (or past) a side edge stays on it

    @classmethod
    def _count_nested(cls, shape) -> int:
        """Shapes nested in a group (all depths, PowerClip contents not counted) - the same number the COM dump's
        top_level() gives, so a master element's signature is the same in the library and at run time."""
        n = 0
        try:
            coll = shape.Shapes
            for i in range(1, coll.Count + 1):
                ch = coll.Item(i)
                n += 1
                if int(ch.Type) == cls.SHAPE_GROUP:
                    n += cls._count_nested(ch)
        except Exception:
            pass
        return n

    @staticmethod
    def _balanced_lines(words: list[str], n: int) -> list[str]:
        """Split words into n lines of about equal length (character count), keeping word order."""
        if n <= 1 or len(words) <= 1:
            return [" ".join(words)]
        total = sum(len(w) for w in words) + len(words) - 1
        lines, cur, cur_len = [], [], 0
        for i, w in enumerate(words):
            cur.append(w)
            cur_len += len(w) + 1
            left = len(words) - i - 1
            if len(lines) < n - 1 and cur_len >= total / n and left >= (n - 1 - len(lines)):
                lines.append(" ".join(cur))
                cur, cur_len = [], 0
        if cur:
            lines.append(" ".join(cur))
        return lines

    @classmethod
    def _apply_text_fit(cls, shape, spec: dict, warnings: list[str]) -> None:
        """Example-library text (app/example_layout.py). Two designer habits, told apart from her boards of this size:
        mode "font" - one font size per style, a longer name wraps onto more lines (never more than she used): the name gets her
          line height and the fewest balanced lines that fit her width cap; if even those do not fit it shrinks uniformly;
        mode "block" - she fits each name to its text block: of 1..her-maximum lines, the one that gives the biggest text inside
          her block height and width cap (a longer break must be clearly better).
        Then it is anchored (left / right / centre) at her x and centred on her y. Growing a short name is as legitimate as
        shrinking a long one."""
        try:
            story = shape.Text.Story
            original = story.Text or ""
            words = original.replace(CR, " ").replace(LF, " ").split()
            if CR in original and is_tamil(original) and re.search(r"[A-Za-z]", original):
                # two names stacked on purpose (English over Tamil, "Both" on a one-line master): keep the line breaks, only scale
                w0, h0 = float(shape.SizeWidth), float(shape.SizeHeight)
                k = min(spec["h"] / h0, spec["w_cap"] / w0) if w0 > 0 and h0 > 0 else 1.0
                shape.SetSize(w0 * k, h0 * k)
                w, h = float(shape.SizeWidth), float(shape.SizeHeight)
                x = cls._inside_page(w, {"left": spec["ax"], "right": spec["ax"] - w, "center": spec["ax"] - w / 2}[spec["anchor"]], spec)
                shape.LeftX = x
                shape.BottomY = spec["cy"] - h / 2
                return
            max_lines = int(spec.get("max_lines") or 1)
            font_mode = spec.get("mode") == "font"
            line_h = float(spec.get("line_h") or spec["h"])
            tries = []
            for n in range(1, min(max(max_lines, 1), max(len(words), 1)) + 1):
                story.Text = CR.join(cls._balanced_lines(words, n)) if n > 1 else " ".join(words)
                w0, h0 = float(shape.SizeWidth), float(shape.SizeHeight)
                if w0 <= 0 or h0 <= 0:
                    continue
                k_w = spec["w_cap"] / w0
                k_h = (line_h * n if font_mode else spec["h"]) / h0
                tries.append((n, min(k_h, k_w), k_h <= k_w, k_h))
            if not tries:
                return
            if font_mode:
                fitting = [t for t in tries if t[2]]
                n, k = (fitting[0][0], fitting[0][3]) if fitting else max(((t[0], t[1]) for t in tries), key=lambda t: t[1])
            else:
                best = None
                for n_, k_, _f, _kh in tries:
                    if best is None or k_ > best[1] * 1.04:
                        best = (n_, k_)
                n, k = best
            story.Text = CR.join(cls._balanced_lines(words, n)) if n > 1 else (" ".join(words) if words else original)
            w0, h0 = float(shape.SizeWidth), float(shape.SizeHeight)
            shape.SetSize(w0 * k, h0 * k)
            w, h = float(shape.SizeWidth), float(shape.SizeHeight)
            x = cls._inside_page(w, {"left": spec["ax"], "right": spec["ax"] - w, "center": spec["ax"] - w / 2}[spec["anchor"]], spec)
            shape.LeftX = x
            shape.BottomY = spec["cy"] - h / 2
            if k < 0.45:
                warnings.append(f"shop name scaled to {k:.0%} of its natural size to fit the designer's block ({n} line(s))")
        except Exception as e:
            warnings.append(f"text-fit failed: {e}")

    @staticmethod
    def _inside_page(w: float, x: float, spec: dict) -> float:
        """Left edge for a name line of width w: kept inside the page (a line anchored at the designer's x must not run off a board
        narrower than hers). Specs without a page width (older libraries) are left as they are."""
        pw = spec.get("page_w")
        if not pw:
            return x
        m = 0.02 * pw
        return max(m, min(x, pw - w - m)) if w <= pw - 2 * m else (pw - w) / 2

    @classmethod
    def _leaf_bitmaps(cls, coll, out: list | None = None) -> list:
        """Every bitmap in a shape collection, through groups (a picture composite is a group of bitmaps)."""
        out = [] if out is None else out
        for i in range(1, coll.Count + 1):
            ch = coll.Item(i)
            t = int(ch.Type)
            if t == cls.SHAPE_BITMAP:
                out.append(ch)
            elif t == cls.SHAPE_GROUP:
                cls._leaf_bitmaps(ch.Shapes, out)
        return out

    @classmethod
    def _place_clip_bitmaps(cls, coll, boxes: dict, warnings: list[str], sx: float = 1.0, sy: float = 1.0) -> int:
        """boxes: {key: (x, y, w, h, aspect)} - the designer's box for each clipped picture. Each bitmap of the clip takes the box
        whose aspect ratio is closest to its own (every box used once); returns how many were placed."""
        import math
        try:
            bms = cls._leaf_bitmaps(coll)
        except Exception as e:
            warnings.append(f"could not read the background clip's pictures: {e}")
            return 0
        used, done = set(), 0
        items = sorted(bms, key=lambda b: -float(b.SizeWidth) * float(b.SizeHeight))
        areas = [float(b.SizeWidth) * float(b.SizeHeight) for b in items]
        # the paper backdrop is the one bitmap far bigger than every picture (>= 3x the next); it only takes a backdrop box
        has_backdrop = len(items) > 1 and areas[0] >= 3 * areas[1]
        for idx, b in enumerate(items):
            try:
                is_backdrop = has_backdrop and idx == 0
                # the page stretch (sx, sy) already squeezed every clipped picture: undo it to see the picture's own proportions
                asp = float(b.SizeWidth) / max(float(b.SizeHeight), 1e-6) * (sy / sx)
                cands = [(abs(math.log(asp / v[4])), k) for k, v in boxes.items() if k not in used and bool(v[5]) == is_backdrop]
                d, k = min(cands) if cands else (9, None)
                if k is None or d > 0.3:
                    continue
                x, y, w, h = boxes[k][:4]
                b.SetSize(w, h)
                b.LeftX = x
                b.BottomY = y
                used.add(k)
                done += 1
            except Exception as e:
                warnings.append(f"could not place a clipped picture: {e}")
        return done

    @classmethod
    def _apply_clip_plan(cls, container, plan: dict, page_w: float, page_h: float, warnings: list[str],
                         sx: float = 1.0, sy: float = 1.0) -> int:
        """Agarpathi template: put the contents of the page-sized background PowerClip where the designer's board of this size
        has them. Children are told apart by geometry on the (already stretched) page: the largest is the paper backdrop
        (covers its target box uniformly - it is a photo), a wide low group is the maroon footer band (stretched to its box -
        plain vector), everything else is the table / product-box composite and moves as ONE rigid unit, scaled uniformly
        into its box (centred)."""
        try:
            coll = container.PowerClip.Shapes
        except Exception:
            return 0
        kids = []
        for i in range(1, coll.Count + 1):
            ch = coll.Item(i)
            try:
                kids.append((ch, float(ch.LeftX), float(ch.BottomY), float(ch.SizeWidth), float(ch.SizeHeight)))
            except Exception:
                continue
        if not kids:
            return 0
        placed_individually = False
        if plan.get("bitmaps"):
            # every clipped picture goes to the designer's box for it, found by its proportions (a picture is scaled, not
            # redrawn, so its aspect ratio is its identity); the vector band keeps the rules below
            done = cls._place_clip_bitmaps(coll, plan["bitmaps"], warnings, sx, sy)
            if done:
                plan = {k: v for k, v in plan.items() if k not in ("backdrop", "composite", "bitmaps")}
                kids = [k for k in kids if int(k[0].Type) != cls.SHAPE_BITMAP and not cls._has_bitmap(k[0])]
                placed_individually = True
                if not kids or "band" not in plan:
                    return done
        bitmaps = [k for k in kids if int(k[0].Type) == cls.SHAPE_BITMAP and k[3] * k[4] >= 0.9 * page_w * page_h]
        if placed_individually:
            backdrop = None                    # the pictures (backdrop included) are already placed
        else:
            backdrop = max(bitmaps, key=lambda k: k[3] * k[4]) if bitmaps else max(kids, key=lambda k: k[3] * k[4])
        rest = [k for k in kids if k is not backdrop]
        bands = [k for k in rest if int(k[0].Type) != cls.SHAPE_BITMAP and k[1] <= 0.1 * page_w
                 and k[1] + k[3] >= 0.9 * page_w and k[2] + k[4] / 2 < 0.45 * page_h]
        band = max(bands, key=lambda k: k[3] * k[4]) if bands else None
        comp = [k for k in rest if k is not band and k[3] < 0.97 * page_w]
        done = 0
        try:
            if "backdrop" in plan and backdrop is not None:
                bx, by, bw, bh = plan["backdrop"]
                _, x, y, w, h = backdrop
                sc = max(bw / w, bh / h)
                backdrop[0].SetSize(w * sc, h * sc)
                backdrop[0].LeftX = bx + bw / 2 - w * sc / 2
                backdrop[0].BottomY = by + bh / 2 - h * sc / 2
                done += 1
            if band is not None and "band" in plan:
                bx, by, bw, bh = plan["band"]
                band[0].SetSize(bw, bh)
                band[0].LeftX = bx
                band[0].BottomY = by
                done += 1
            if comp and "composite" in plan:
                bx, by, bw, bh = plan["composite"]
                x0 = min(k[1] for k in comp); y0 = min(k[2] for k in comp)
                x1 = max(k[1] + k[3] for k in comp); y1 = max(k[2] + k[4] for k in comp)
                cw, ch_ = x1 - x0, y1 - y0
                sc = min(bw / cw, bh / ch_)
                ox = bx + bw / 2 - cw * sc / 2
                oy = by + bh / 2 - ch_ * sc / 2
                for shp, x, y, w, h in comp:
                    shp.SetSize(w * sc, h * sc)
                    shp.LeftX = ox + (x - x0) * sc
                    shp.BottomY = oy + (y - y0) * sc
                    done += 1
        except Exception as e:
            warnings.append(f"could not apply the designer template to the background clip: {e}")
        return done

    @classmethod
    def _undistort_clip_contents(cls, container, sx: float, sy: float, page_w: float, page_h: float,
                                 warnings: list[str]) -> int:
        """A page-covering PowerClip (the `bg` role) is stretched to the new page, and CorelDRAW stretches its contents
        with it - on a board whose shape differs from the master's that squashes the table / product-box composite a
        real master keeps inside its background clip. Each FOREGROUND child of the clip that holds a BITMAP (smaller than
        CLIP_FOREGROUND_MAX_AREA of the page and at least CLIP_FOREGROUND_MIN_INSIDE on it - the same rule as
        orientation_adapter's separable foreground) is re-sized uniformly by the smaller of the two scale factors,
        centred horizontally where the stretch put it and standing on the same bottom edge, so it keeps its
        proportions and never grows past the stretched box (i.e. into the logos above it). Backdrop children (the
        full-bleed texture, art mostly off the page) keep the stretch. Returns how many children were adjusted.

        Horizontally, a child that touched or ran past a page edge on the MASTER stays on that edge (its bleed scaled
        with it): the DARSHAN table runs off the left edge, and re-centring it left a short table with both ends visible
        floating mid-board on a 167x29 in board. Only a child clear of both edges is centred where the stretch put it."""
        if abs(sx / sy - 1.0) < 0.02:
            return 0
        try:
            clip = container.PowerClip
            coll = clip.Shapes if clip is not None else None
        except Exception:
            coll = None
        if coll is None:
            return 0
        k = min(sx, sy)
        src_w = page_w / sx                       # the master's page width
        edge_tol = cls.CLIP_EDGE_TOL * src_w
        done = 0
        for i in range(1, coll.Count + 1):
            ch = coll.Item(i)
            try:
                x, y, w, h = float(ch.LeftX), float(ch.BottomY), float(ch.SizeWidth), float(ch.SizeHeight)
            except Exception:
                continue
            if w <= 0 or h <= 0 or w * h >= cls.CLIP_FOREGROUND_MAX_AREA * page_w * page_h:
                continue
            ix = max(0.0, min(x + w, page_w) - max(x, 0.0))
            iy = max(0.0, min(y + h, page_h) - max(y, 0.0))
            if ix * iy < cls.CLIP_FOREGROUND_MIN_INSIDE * w * h:
                continue
            if not cls._has_bitmap(ch):
                # a plain vector shape (the Hangyo board's white centre panel, a colour band) stretches with the frame
                # like the rest of the background - only photos and photo composites would look distorted
                continue
            nw, nh = w / sx * k, h / sy * k   # original size x the uniform factor
            ox, ow = x / sx, w / sx            # the child's box on the master
            if ox <= edge_tol:                              # on / past the left edge: keep it there
                nx = ox * k
            elif src_w - (ox + ow) <= edge_tol:             # on / past the right edge
                nx = page_w - (src_w - ox) * k
            else:
                nx = x + (w - nw) / 2
            try:
                ch.SetSize(nw, nh)
                ch.LeftX = nx
                ch.BottomY = y
            except Exception as e:
                warnings.append(f"could not keep a clipped object in proportion: {e}")
                continue
            done += 1
        if done:
            warnings.append(f"kept {done} object(s) inside the background PowerClip in proportion "
                            f"(frame stretched {sx:.2f} x {sy:.2f})")
        return done

    @staticmethod
    def _apply_language(mode: str, placed, objs_by_id: dict, shapes_by_id: dict, warnings: list[str]) -> int:
        """English Only / Tamil Only: delete the top-level shop-name lines of the OTHER language (judged by the master's own
        text of each line - Tamil script or not) and move the kept ones so they are centred on the space the whole name
        block used (vertically when the lines were stacked, horizontally when they sat side by side). Returns how many lines were removed. Nothing happens
        when the board has no line of the language to keep (it is never left without a shop name)."""
        if mode not in ("en", "ta"):
            return 0
        names = [p for p in placed if p.role == "shopname" and p.id in objs_by_id and p.id in shapes_by_id]
        drop = [p for p in names if is_tamil(objs_by_id[p.id].text) == (mode == "en")]
        keep = [p for p in names if p not in drop]
        if not drop:
            return 0
        if not keep:
            warnings.append(f"{'English' if mode == 'en' else 'Tamil'} Only was chosen but the board has no "
                            f"{'English' if mode == 'en' else 'Tamil'} shop-name line - both lines kept")
            return 0

        def box(ps):
            bs = []
            for p in ps:
                s = shapes_by_id[p.id]
                bs.append((float(s.LeftX), float(s.BottomY), float(s.LeftX) + float(s.SizeWidth), float(s.BottomY) + float(s.SizeHeight)))
            return min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)

        try:
            k, d = box(keep), box(drop)
            stacked = min(k[2], d[2]) - max(k[0], d[0]) > 0          # the two groups overlap horizontally: one above the other
            # the kept line(s) move to the centre of the space the whole name block used, along the axis the pair was
            # laid out on: vertically for stacked lines, horizontally for side-by-side ones (landscape DARSHAN footer)
            if stacked:
                dx, dy = 0.0, (min(k[1], d[1]) + max(k[3], d[3])) / 2 - (k[1] + k[3]) / 2
            else:
                dx, dy = (min(k[0], d[0]) + max(k[2], d[2])) / 2 - (k[0] + k[2]) / 2, 0.0
            for p in keep:
                s = shapes_by_id[p.id]
                s.LeftX = float(s.LeftX) + dx
                s.BottomY = float(s.BottomY) + dy
        except Exception as e:
            warnings.append(f"could not re-centre the remaining shop name: {e}")
        removed = 0
        for p in drop:
            try:
                shapes_by_id[p.id].Delete()
                removed += 1
            except Exception as e:
                warnings.append(f"could not remove the {'Tamil' if mode == 'en' else 'English'} shop-name line: {e}")
        return removed

    @staticmethod
    def _restore_master(doc, snapshot) -> bool:
        """Undo this shop's command group and check the document is exactly the master again (doc_fingerprint)."""
        doc.Undo(1)
        after = doc_fingerprint(doc)
        same = same_fingerprint(after, snapshot)
        try:
            doc.Dirty = False
        except Exception:
            pass
        if not same:
            diff = [(a, b) for a, b in zip(snapshot, after) if a != b]
            logger.warning("master not identical after undo (%d of %d rows differ, rows %d vs %d, first: %s); closing it - "
                           "the next shop reopens it from disk", len(diff), len(snapshot), len(after), len(snapshot), diff[:3])
        return same

    @classmethod
    def _has_bitmap(cls, shape, depth: int = 0) -> bool:
        """Whether a shape is, or contains (in a group or PowerClip, a few levels deep), an embedded bitmap."""
        try:
            if int(shape.Type) == cls.SHAPE_BITMAP:
                return True
        except Exception:
            return False
        if depth >= 4:
            return False
        return any(cls._has_bitmap(k, depth + 1) for k in cls._child_shapes(shape))

    @staticmethod
    def _child_shapes(s) -> list:
        """Direct children of a group or a PowerClip container ([] for anything else)."""
        for get in (lambda: s.PowerClip.Shapes, lambda: s.Shapes):
            try:
                coll = get()
                if coll is not None:
                    return [coll.Item(i) for i in range(1, coll.Count + 1)]
            except Exception:
                continue
        return []

    STAMP_RE = re.compile(r"\d{1,2}\s*/\s*\d{1,2}")      # "ADINN/06/26": a job stamp, not a shop name

    @classmethod
    def _guess_master_names(cls, shapes) -> tuple[str | None, str | None]:
        """A master whose shop name nobody entered (its file is just "6 X 3.cdr"): the shop name is its biggest English text, and
        the Tamil one its biggest Tamil text - looked up through groups and PowerClips too (a master often keeps the name
        inside the logo group), skipping job stamps like "ADINN/06/26". Returns (english text, tamil text) as printed."""
        best = {"en": None, "ta": None}

        def walk(shape_list):
            for sh in shape_list:
                kids = cls._child_shapes(sh)
                if kids:
                    walk(kids)
                    continue
                try:
                    if int(sh.Type) != cls.SHAPE_TEXT:
                        continue
                    t = cls._shape_text(sh)
                    area = float(sh.SizeWidth) * float(sh.SizeHeight)
                except Exception:
                    continue
                if not t or not t.strip() or cls.STAMP_RE.search(t):
                    continue
                key = "ta" if is_tamil(t) else "en"
                if best[key] is None or area > best[key][0]:
                    best[key] = (area, t)

        walk(shapes)
        return (best["en"][1] if best["en"] else None, best["ta"][1] if best["ta"] else None)

    @classmethod
    def _replace_nested_shopnames(cls, top_shapes, shop: dict, warnings: list[str]) -> int:
        """Rewrite shop-name texts nested inside groups / PowerClips: a text named `shopname...` in CorelDRAW's Object
        Manager, or whose content matches the master's current shop name (either script). The Tamil line stacked
        next to a matched English one is found the same way as for top-level text (layout.find_local_partner_ids).
        Text is replaced in place and fitted to its original width. Returns how many texts were rewritten."""
        name = shop.get("name")
        local = shop.get("shop_name_local")
        if not (name or local):
            return 0
        hints = [_norm_name(h) for h in (shop.get("master_shop_name"), shop.get("master_shop_name_local")) if h and h.strip()]
        texts = []  # (shape, Obj) for every nested text shape

        def walk(shape_list, nested):
            for sh in shape_list:
                kids = cls._child_shapes(sh)
                if kids:
                    walk(kids, True)
                    continue
                try:
                    is_text = int(sh.Type) == cls.SHAPE_TEXT
                except Exception:
                    is_text = False
                if nested and is_text:
                    t = cls._shape_text(sh)
                    try:
                        o = Obj(str(len(texts)), sh.Name or "", "text", float(sh.LeftX), float(sh.BottomY),
                                float(sh.SizeWidth), float(sh.SizeHeight), t)
                    except Exception:
                        continue
                    texts.append((sh, o))

        walk(top_shapes, False)
        objs = [o for _, o in texts]
        ids = {o.id for o in objs if o.name.strip().lower().startswith("shopname")}
        ids |= {o.id for o in objs if o.text and hints and any(
            (h in _norm_name(o.text) or _norm_name(o.text) in h) for h in hints)}
        if (local or shop_language(shop) == "en") and not shop.get("master_shop_name_local"):
            ids |= find_local_partner_ids(objs, ids)
        done = 0
        for sh, o in texts:
            if o.id not in ids:
                continue
            lang = shop_language(shop)
            if lang != "both" and is_tamil(o.text) == (lang == "en"):
                try:
                    sh.Delete()
                    done += 1
                except Exception as e:
                    warnings.append(f"could not remove a nested shop-name line: {e}")
                continue
            if is_tamil(o.text) and local:
                new, font = local, TAMIL_FONT
            elif is_tamil(o.text):
                continue  # no local name given: never print the English name in the Tamil line's place
            else:
                new, font = (name, TAMIL_FONT if is_tamil(name) else None) if name else (local, TAMIL_FONT)
            font = name_font(new, shop)       # only an explicit override - the master's font is kept otherwise
            try:
                story = sh.Text.Story
                story.Text = new
                if font:
                    story.Font = font
                    if story.Font != font:
                        warnings.append(f"font '{font}' not available to CorelDRAW; the nested shop name kept its font")
                corel_util.ensure_tamil_font_renders(sh, new, warnings)
            except Exception as e:
                warnings.append(f"could not set nested shop-name text: {e}")
                continue
            cls._fit_text(sh, o.w, warnings)
            done += 1
        return done

    @staticmethod
    def _fit_text(shape, target_w_mm: float, warnings: list[str], allow_wrap: bool = True) -> None:
        """Shrink, then wrap to a second line, a text shape that's grown
        wider than the space its layout box was given.

        CorelDRAW artistic text (what these masters use for shop
        name/phone/GST - fixed absolute font sizes, not an auto-fit
        paragraph frame) doesn't wrap on its own, so replacing a short
        master name like "SRI KAVI STEELS" with a long one like "SAFI STEEL
        TRADERS PRIVATE LIMITED" can grow the shape wide enough to collide
        with neighbouring fixed text (the phone/GST line) - the collision
        `metrics.py`'s text_overlap check exists to catch after the fact.
        This tries to avoid it at generation time instead: shrink font size
        in 10% steps down to `layout.MIN_TEXT_PT` (a floor derived from real
        data - see that constant), then, if still too wide, wrap at the
        space nearest the middle of the text. Gives up and warns rather
        than looping forever or shrinking past the point of being legible.
        """
        if target_w_mm <= 0:
            return
        try:
            story = shape.Text.Story
            size = float(story.Size)
        except Exception:
            return

        try:
            if float(shape.SizeWidth) <= target_w_mm * 1.05:
                return  # already fits

            while float(shape.SizeWidth) > target_w_mm * 1.05 and size > MIN_TEXT_PT * 1.3:
                size *= 0.9
                story.Size = size

            if allow_wrap and float(shape.SizeWidth) > target_w_mm * 1.05:
                text = story.Text or ""
                spaces = [i for i, c in enumerate(text) if c == " "]
                if spaces:
                    mid = len(text) / 2
                    best = min(spaces, key=lambda i: abs(i - mid))
                    story.Text = text[:best] + "\r" + text[best + 1:]

            if float(shape.SizeWidth) > target_w_mm * 1.05:
                # last resort: scale the text shape itself, uniformly and about its centre, to the box width. Needed when
                # the master's text is small-point type enlarged as a shape (the Hangyo board's big Tamil line), so the
                # font-size loop above never runs - left alone, the new name ran off the white panel.
                w0, h0 = float(shape.SizeWidth), float(shape.SizeHeight)
                cx, cy = float(shape.LeftX) + w0 / 2, float(shape.BottomY) + h0 / 2
                k = target_w_mm / w0
                shape.SetSize(w0 * k, h0 * k)
                shape.LeftX = cx - w0 * k / 2
                shape.BottomY = cy - h0 * k / 2

            if float(shape.SizeWidth) > target_w_mm * 1.05:
                warnings.append("replacement text still wider than its layout box after shrinking/wrapping; check manually")
        except Exception as e:
            warnings.append(f"text-fit failed: {e}")

    def _process(self, master_path: Path, shop: dict, out_dir: Path, on_step=None) -> dict:
        new_w = to_mm(shop["width"], shop["unit"])
        new_h = to_mm(shop["height"], shop["unit"])
        base = _output_base(shop)
        out_dir.mkdir(parents=True, exist_ok=True)
        # SaveAs/PublishToPDF/ExportBitmap need absolute paths just like OpenDocument
        # does (see CLAUDE.md) - a relative path here was reproduced live to make
        # CorelDRAW fall back to showing its interactive "Save Drawing" dialog
        # instead of saving silently, which is exactly the hang this file's
        # timeouts exist to survive.
        out_dir = out_dir.resolve()

        timings: dict[str, float] = {}
        warnings: list[str] = []

        corel_util.cleanup_orphaned_instances()
        free_ram_gb = corel_util.check_memory()
        if free_ram_gb < 2.0:
            warnings.append(f"low free RAM at job start: {free_ram_gb:.2f} GB")

        if on_step is not None:
            try:
                on_step("launch")
            except Exception:
                pass
        t0 = time.time()
        app, we_launched_it, pid = corel_util.acquire_instance()
        timings["launch"] = round(time.time() - t0, 1)

        watchdog = None
        if pid is not None and os.environ.get("SIGNAGE_COREL_WATCHDOG", "1") == "1":
            watchdog = Watchdog(pid, log_fn=logger.info, dismiss_after_s=20.0, shot_dir=out_dir)
            watchdog.start()

        def step(name, fn):
            if on_step is not None:
                try:
                    on_step(name)
                except Exception:
                    pass
            t = time.time()
            try:
                return corel_util.run_with_timeout(fn, pid, name)
            finally:
                timings[name] = round(time.time() - t, 1)

        success = False
        doc = None
        keep_doc = False
        master_reused = False
        snapshot = None
        reuse = keep_master_open() and os.environ.get("SIGNAGE_REUSE_COREL") != "1"
        sess = _MasterSession
        key = _master_key(master_path) if reuse else None
        if sess.doc is not None and (sess.pid != pid or sess.key != key or not reuse):
            if sess.pid == pid:
                _close_doc(sess.doc)          # another master on this instance
            sess.doc = sess.pid = sess.key = sess.snapshot = None
        try:
            if sess.doc is not None:
                doc, snapshot = sess.doc, sess.snapshot
                sess.doc = None               # re-armed only after this shop restores it cleanly
                if on_step is not None:
                    try:
                        on_step("open")
                    except Exception:
                        pass
                timings["open"] = 0.0
                master_reused = True
            else:
                def _open():
                    corel_util.check_file_not_newer(Path(master_path), app)
                    d = app.OpenDocument(str(Path(master_path).resolve()))
                    return d, (doc_fingerprint(d) if reuse else None)

                doc, snapshot = step("open", _open)
            if reuse:
                doc.BeginCommandGroup("Signage shop")

            made_copies: list = []                 # shapes this shop Duplicate()d: such a board is never reused as the next shop's master

            def _resize_and_tile():
                doc.Unit = self.CDR_MILLIMETER
                page = doc.ActivePage
                page_w, page_h = float(page.SizeWidth), float(page.SizeHeight)

                shapes = [page.Shapes.Item(i) for i in range(1, page.Shapes.Count + 1)]
                shapes_by_id: dict[str, object] = {}
                objs = []
                for i, s in enumerate(shapes):
                    kind = {self.SHAPE_TEXT: "text", self.SHAPE_BITMAP: "bitmap",
                            self.SHAPE_GROUP: "group"}.get(int(s.Type), "shape")
                    text = self._shape_text(s) if kind == "text" else None
                    if kind == "text":
                        # Fix BEFORE reading SizeWidth/SizeHeight below - changing
                        # the font can itself change the shape's rendered size,
                        # and every downstream placement calculation should see
                        # the size the shape will actually export at, not the
                        # (possibly tofu-boxed) pre-fix one.
                        self._ensure_tamil_font_renders(s, text, warnings)
                    oid = str(i)
                    shapes_by_id[oid] = s
                    objs.append(Obj(oid, s.Name or f"object_{i+1}", kind,
                                    float(s.LeftX), float(s.BottomY),
                                    float(s.SizeWidth), float(s.SizeHeight), text,
                                    self._shape_fill_cmyk(s), self._count_nested(s) if kind == "group" else 0))

                if not shop.get("master_shop_name") and not shop.get("master_shop_name_local") and                         not any(o.name.strip().lower().startswith("shopname") for o in objs):
                    en, ta = self._guess_master_names(shapes)
                    if en or ta:
                        shop["master_shop_name"] = " ".join(en.split()) if en else None
                        shop["master_shop_name_local"] = " ".join(ta.split()) if ta else None
                        warnings.append("the master's shop name was not set: its biggest text was used as the name to replace "
                                        f"({shop['master_shop_name'] or shop['master_shop_name_local']!r}) - set it for the master to be sure")
                shopname_ids = find_shopname_ids(
                    objs, shop.get("master_shop_name"), shop.get("master_shop_name_local"),
                )
                shopname_ids |= {o.id for o in objs if detect_role(o, page_w, page_h) == "shopname"}
                # The master's Tamil shop-name line is not known by content (only the English name is, from the master's
                # file name) - it is the Tamil text stacked next to the matched English line.
                if not shop.get("master_shop_name_local"):
                    partners = find_local_partner_ids(objs, shopname_ids)
                    # English Only removes the Tamil line, so it is found even without a Tamil name to write into it
                    if shop.get("shop_name_local") or shop_language(shop) == "en":
                        shopname_ids |= partners
                    elif partners:
                        warnings.append("the master's local-language shop name was left unchanged: no local shop name "
                                        "was given for this shop (add a 'Shop Name (Tamil)' column to the import)")
                if not shopname_ids and not shop.get("master_shop_name"):
                    warnings.append("shop name not replaced: the master's current shop name is unknown (set it for the "
                                    "master, or tag the text 'shopname' in CorelDRAW)")
                contact_ids = find_contact_ids(objs) if (shop.get("phone") or shop.get("gst")) else set()
                placed = compute_layout(
                    objs, page_w, page_h, new_w, new_h,
                    safe_margin=float(shop.get("safe_margin", 0)),
                    tile=bool(shop.get("tile", True)),
                    shop_name=shop.get("name"),
                    shop_name_local=shop.get("shop_name_local"),
                    shopname_ids=shopname_ids,
                    brand_rule=brand_rule_for_shop(shop.get("brand")),
                    phone=shop.get("phone"),
                    gst=shop.get("gst"),
                    address_lines=shop.get("address_lines"),
                    contact_ids=contact_ids,
                    template_exclude=shop.get("template_exclude"),   # leave-one-out testing only
                    board_type=shop.get("board_type"),
                    language=shop_language(shop, warnings),
                )
                apply_name_fonts(placed, shop)

                page.SetSize(new_w, new_h)
                reused_base_ids: set[str] = set()
                for p in placed:
                    base_id, _, _tile_idx = p.id.partition("_tile")
                    base_shape = shapes_by_id.get(base_id)
                    if base_shape is None:
                        continue
                    # Reuse the original shape for a base_id's FIRST placement in
                    # this job, duplicate for every subsequent one - tracked by
                    # which base_ids have actually been placed already, not by
                    # whether the id happens to end in "_tile0". That string check
                    # was a latent bug: it's only true for the panel-tiling schemes
                    # where every group's copies are numbered 0..N-1 starting at 0
                    # (_place_tiled_panel, _place_brand_ruled_panel's *repeating*
                    # groups) - a group that appears exactly once but isn't the
                    # first one processed (e.g. _place_brand_ruled_panel's "never
                    # repeats" badge, or ANY group in _place_panel_sequence, which
                    # numbers by sequence position, not per-group occurrence) got
                    # ".Duplicate()"-ed instead of repositioned, leaving the
                    # original shape behind at its old, now-wrong location - a
                    # visible "ghost" duplicate, confirmed live on a real board
                    # (see CLAUDE.md "Wide-board panel sequence").
                    if base_id in reused_base_ids:
                        shape = base_shape.Duplicate()
                        made_copies.append(base_id)
                    else:
                        shape = base_shape
                        reused_base_ids.add(base_id)
                    if shape.Locked:
                        p.warnings.append("shape locked; skipped")
                        continue
                    old_w, old_h = float(shape.SizeWidth), float(shape.SizeHeight)
                    shape.SetSize(p.w, p.h)
                    shape.LeftX = p.x
                    shape.BottomY = p.y
                    if p.role == "bg" and p.clip_plan:
                        self._apply_clip_plan(shape, p.clip_plan, new_w, new_h, p.warnings,
                                              p.w / old_w if old_w > 0 else 1.0, p.h / old_h if old_h > 0 else 1.0)
                    elif p.role == "bg" and old_w > 0 and old_h > 0:
                        self._undistort_clip_contents(shape, p.w / old_w, p.h / old_h, new_w, new_h, p.warnings)
                    if p.bring_to_front:
                        # A duplicated card background (see layout._place_panel_sequence's
                        # `card_from`) stacks directly above the template it was
                        # duplicated from, not above THIS shape's own content -
                        # left alone, the new card visually covers the content
                        # it's meant to frame. Confirmed live: without this, the
                        # borrowed card rendered completely blank.
                        try:
                            shape.OrderToFront()
                        except Exception as e:
                            p.warnings.append(f"could not bring shape to front: {e}")
                    if p.recolor_cmyk is not None:
                        # A borrowed-card content shape (see `bring_to_front`
                        # above) styled white for the master's own dark
                        # background - recoloured to match the template
                        # card's own text colour so it's actually visible on
                        # its new white card. CMYKAssign is expected to
                        # mutate the Color object `Fill.UniformColor`
                        # returns in place - if it doesn't stick, the
                        # warning below is how that gets caught rather than
                        # failing silently.
                        try:
                            c, m, y, k = p.recolor_cmyk
                            shape.Fill.UniformColor.CMYKAssign(c, m, y, k)
                            after = shape.Fill.UniformColor
                            got = (round(after.CMYKCyan), round(after.CMYKMagenta),
                                   round(after.CMYKYellow), round(after.CMYKBlack))
                            if got != (c, m, y, k):
                                p.warnings.append(f"recolor to {p.recolor_cmyk} did not stick (read back {got})")
                        except Exception as e:
                            p.warnings.append(f"could not recolor shape: {e}")
                    if p.text is not None:
                        self._set_replacement_text(shape, p)
                # Shop-name texts INSIDE a group or PowerClip are not layout objects of their own (only top-level shapes
                # are); their parent was placed above, so they are rewritten in place.
                self._apply_language(shop_language(shop, warnings), placed, {o.id: o for o in objs}, shapes_by_id, warnings)
                nested = self._replace_nested_shopnames(shapes, shop, warnings)
                if (shop.get("name") or shop.get("shop_name_local")) and not nested and not any(
                        p.role == "shopname" for p in placed):
                    warnings.append("SHOP NAME NOT REPLACED: none of the master's text matches its shop name "
                                    f"({shop.get('master_shop_name') or 'unknown'!r}) - this board still shows the master's "
                                    "own shop name. Tag the text 'shopname' in CorelDRAW or correct the master's shop name.")
                return page_w, page_h, placed

            page_w, page_h, placed = step("tile_resize", _resize_and_tile)
            # Cap embedded bitmaps at SIGNAGE_MAX_BITMAP_DPI (300) at their PLACED size, before the save: a board smaller
            # than its master otherwise carries the master's full-resolution photos into every output (measured live: a
            # 10x4 in board from a 125x48 in master saved 208.7 MB in 14.1 s; capped, 10.0 MB in 0.8 s). Downsamples only,
            # keeps every bitmap's box - see corel_util.cap_bitmap_resolution.
            bitmap_cap = step("bitmaps", lambda: corel_util.cap_bitmap_resolution(doc, corel_util.max_bitmap_dpi()))
            if reuse:
                doc.EndCommandGroup()          # every change of this shop is now ONE undo step
            if bitmap_cap.get("resampled"):
                logger.info("capped %d bitmap(s) at %d dpi (%.1f -> %.1f Mpx)", bitmap_cap["resampled"],
                            corel_util.max_bitmap_dpi(), bitmap_cap["pixels_before"] / 1e6, bitmap_cap["pixels_after"] / 1e6)

            cdr_path = out_dir / f"{base}.cdr"
            pdf_path = out_dir / f"{base}.pdf"
            png_path = out_dir / f"{base}.png"
            step("saveas", lambda: corel_util.save_cdr(doc, cdr_path))
            cdr_format = corel_util.check_cdr_format(cdr_path, warnings)
            step("pdf", lambda: doc.PublishToPDF(str(pdf_path)))
            # Native-resolution PNG export can be tens of thousands of px on a side for
            # large-format signage; cap the preview's longest side instead.
            longest_in = max(new_w, new_h) / 25.4
            dpi = max(36, min(150, self.PREVIEW_MAX_PX / longest_in))

            def _export():
                flt = doc.ExportBitmap(
                    str(png_path), self.CDR_PNG, self.CDR_CURRENT_PAGE, self.CDR_RGB_IMAGE,
                    0, 0, dpi, dpi, 1, False, False, True, False, 0, None, None,
                )
                flt.Finish()

            step("png", _export)
            success = True
            if reuse and snapshot is not None:
                t = time.time()
                try:
                    keep_doc = corel_util.run_with_timeout(lambda: self._restore_master(doc, snapshot), pid, "restore")
                except Exception as e:
                    logger.warning("could not restore the master for reuse (%s); it will be reopened", e)
                timings["restore"] = round(time.time() - t, 1)
                if made_copies and keep_doc:
                    # an undone Duplicate() passes the fingerprint check yet has produced wrongly drawn boards (an icon at many times its
                    # size) for the shops after it in one batch; a board with copies reopens its master from disk instead
                    keep_doc = False
        finally:
            # Best-effort: a cleanup failure (e.g. the COM server already died)
            # must not clobber a result we already successfully computed.
            if keep_doc and not corel_util.will_quit_on_release(pid, success):
                sess.doc, sess.pid, sess.key, sess.snapshot = doc, pid, key, snapshot
            elif doc is not None:
                _close_doc(doc)
            if watchdog is not None:
                for d in watchdog.stop():
                    warnings.append(f"dialog {d['title']!r} open >=20s, dismissed via {d['dismissed_via']}")
            corel_util.release_instance(pid, success)

        report = _report((page_w, page_h), (new_w, new_h), placed, timings, warnings, free_ram_gb)
        report["cdr_format"] = cdr_format
        report["bitmap_cap"] = {"max_dpi": corel_util.max_bitmap_dpi(), **bitmap_cap}
        report["corel"] = dict(corel_util.connected)  # which CorelDRAW (ProgID + version) produced this board
        # the master was already open from the previous shop (restored by undo and verified) / kept open for the next one
        report["master_session"] = {"reused": master_reused, "kept_open": bool(keep_doc)}
        (out_dir / f"{base}_report.json").write_text(json.dumps(report, indent=2))
        return {"files": {"cdr": cdr_path.name, "pdf": pdf_path.name,
                          "preview": png_path.name, "report": f"{base}_report.json"},
                "report": report}


# --------------------------------------------------------------------------
class MockEngine:
    """No CorelDRAW needed. Uses a demo scene instead of parsing the .cdr."""

    name = "mock"

    DEMO_PAGE = (3000.0, 1000.0)
    DEMO = [
        Obj("0", "bg_wall", "shape", 0, 0, 3000, 1000),
        Obj("1", "frame_border", "shape", 50, 50, 2900, 900),
        Obj("2", "logo_main", "group", 150, 300, 700, 400),
        Obj("3", "Shop name", "text", 1000, 400, 1500, 200),
        Obj("4", "fixed_phone", "text", 2400, 80, 500, 60),
    ]

    def process(self, master_path: Path, shop: dict, out_dir: Path) -> dict:
        new_w = to_mm(shop["width"], shop["unit"])
        new_h = to_mm(shop["height"], shop["unit"])
        base = _output_base(shop)
        out_dir.mkdir(parents=True, exist_ok=True)
        pw, ph = self.DEMO_PAGE
        placed = compute_layout(self.DEMO, pw, ph, new_w, new_h,
                                safe_margin=float(shop.get("safe_margin", 0)))
        report = _report((pw, ph), (new_w, new_h), placed)

        shutil.copyfile(master_path, out_dir / f"{base}.cdr")  # placeholder copy
        (out_dir / f"{base}_report.json").write_text(json.dumps(report, indent=2))
        (out_dir / f"{base}.svg").write_text(_svg(new_w, new_h, placed))
        return {"files": {"cdr": f"{base}.cdr", "preview": f"{base}.svg",
                          "report": f"{base}_report.json"},
                "report": report,
                "note": "MockEngine: demo scene, not your real .cdr. Run on Windows with CorelDRAW for real output."}


def _svg(w, h, placed) -> str:
    colors = {"bg": "#e8eef7", "frame": "none", "text": "#1d4ed8", "logo": "#f59e0b", "fixed": "#10b981"}
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.1f} {h:.1f}" width="100%">']
    for p in placed:
        y = h - p.y - p.h  # flip y for SVG
        fill = colors.get(p.role, "#999")
        sw = max(w, h) / 400
        parts.append(f'<rect x="{p.x:.1f}" y="{y:.1f}" width="{p.w:.1f}" height="{p.h:.1f}" '
                     f'fill="{fill}" fill-opacity="0.85" stroke="#334155" stroke-width="{sw:.1f}"/>')
        fs = max(min(p.h * 0.3, p.w / max(len(p.name), 1) * 1.6), 1)
        parts.append(f'<text x="{p.x + p.w/2:.1f}" y="{y + p.h/2:.1f}" font-size="{fs:.1f}" '
                     f'text-anchor="middle" dominant-baseline="middle" fill="#0f172a">{p.name}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def get_engine(kind: str = "auto"):
    if kind == "mock":
        return MockEngine()
    if kind == "corel" or (kind == "auto" and sys.platform == "win32"):
        return CorelEngine()
    return MockEngine()
