"""CDR engines.

CorelEngine  - real automation of CorelDRAW through COM (Windows only).
MockEngine   - development stand-in for Linux/macOS: uses a demo scene and
               produces SVG previews so the whole site can be exercised
               without CorelDRAW.

Both expose:  process(master_path, shop, out_dir) -> dict
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from . import corel_util
from .corel_watchdog import Watchdog
from .layout import MIN_TEXT_PT, Obj, compute_layout, find_contact_ids, find_shopname_ids, load_brand_rule, to_mm

logger = logging.getLogger(__name__)


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_") or "shop"


def _report(page, new, placed, timings=None, warnings=None, free_ram_gb=None) -> dict:
    report = {
        "original_page_mm": {"w": page[0], "h": page[1]},
        "new_page_mm": {"w": new[0], "h": new[1]},
        "objects": [p.to_dict() for p in placed],
    }
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
        CorelEngine._fit_text(shape, placed.w, placed.warnings)

    @staticmethod
    def _fit_text(shape, target_w_mm: float, warnings: list[str]) -> None:
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

            if float(shape.SizeWidth) > target_w_mm * 1.05:
                text = story.Text or ""
                spaces = [i for i, c in enumerate(text) if c == " "]
                if spaces:
                    mid = len(text) / 2
                    best = min(spaces, key=lambda i: abs(i - mid))
                    story.Text = text[:best] + "\r" + text[best + 1:]

            if float(shape.SizeWidth) > target_w_mm * 1.05:
                warnings.append("replacement text still wider than its layout box after shrinking/wrapping; check manually")
        except Exception as e:
            warnings.append(f"text-fit failed: {e}")

    def _process(self, master_path: Path, shop: dict, out_dir: Path, on_step=None) -> dict:
        new_w = to_mm(shop["width"], shop["unit"])
        new_h = to_mm(shop["height"], shop["unit"])
        base = _safe(shop["name"])
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
        try:
            doc = step("open", lambda: app.OpenDocument(str(Path(master_path).resolve())))

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
                                    self._shape_fill_cmyk(s)))

                shopname_ids = find_shopname_ids(
                    objs, shop.get("master_shop_name"), shop.get("master_shop_name_local"),
                )
                contact_ids = find_contact_ids(objs) if (shop.get("phone") or shop.get("gst")) else set()
                placed = compute_layout(
                    objs, page_w, page_h, new_w, new_h,
                    safe_margin=float(shop.get("safe_margin", 0)),
                    tile=bool(shop.get("tile", True)),
                    shop_name=shop.get("name"),
                    shop_name_local=shop.get("shop_name_local"),
                    shopname_ids=shopname_ids,
                    brand_rule=load_brand_rule(shop.get("brand")),
                    phone=shop.get("phone"),
                    gst=shop.get("gst"),
                    address_lines=shop.get("address_lines"),
                    contact_ids=contact_ids,
                )

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
                    else:
                        shape = base_shape
                        reused_base_ids.add(base_id)
                    if shape.Locked:
                        p.warnings.append("shape locked; skipped")
                        continue
                    shape.SetSize(p.w, p.h)
                    shape.LeftX = p.x
                    shape.BottomY = p.y
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
                return page_w, page_h, placed

            page_w, page_h, placed = step("tile_resize", _resize_and_tile)

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
        finally:
            # Best-effort: a cleanup failure (e.g. the COM server already died)
            # must not clobber a result we already successfully computed.
            if doc is not None:
                try:
                    doc.Close()
                except Exception:
                    pass
            if watchdog is not None:
                for d in watchdog.stop():
                    warnings.append(f"dialog {d['title']!r} open >=20s, dismissed via {d['dismissed_via']}")
            corel_util.release_instance(pid, success)

        report = _report((page_w, page_h), (new_w, new_h), placed, timings, warnings, free_ram_gb)
        report["cdr_format"] = cdr_format
        report["corel"] = dict(corel_util.connected)  # which CorelDRAW (ProgID + version) produced this board
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
        base = _safe(shop["name"])
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
