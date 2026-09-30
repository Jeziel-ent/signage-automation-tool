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
from .layout import (MIN_TEXT_PT, TAMIL_FONT, Obj, compute_layout, detect_role, find_contact_ids, find_local_partner_ids,
                     find_shopname_ids, is_tamil, load_brand_rule, to_mm)

logger = logging.getLogger(__name__)


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_") or "shop"


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
        # the master's font is kept - unless it is Tamil text in a font that has no Tamil letters (tofu boxes otherwise)
        corel_util.ensure_tamil_font_renders(shape, placed.text, placed.warnings)
        CorelEngine._fit_text(shape, placed.w, placed.warnings)

    CLIP_FOREGROUND_MAX_AREA = 0.9   # of the page: bigger clipped children are backdrop and keep the frame's stretch
    CLIP_FOREGROUND_MIN_INSIDE = 0.9  # share of the child's box inside the page: art hanging off the page is backdrop

    @classmethod
    def _undistort_clip_contents(cls, container, sx: float, sy: float, page_w: float, page_h: float,
                                 warnings: list[str]) -> int:
        """A page-covering PowerClip (the `bg` role) is stretched to the new page, and CorelDRAW stretches its contents
        with it - on a board whose shape differs from the master's that squashes the table / product-box composite a
        real master keeps inside its background clip. Each FOREGROUND child of the clip (smaller than
        CLIP_FOREGROUND_MAX_AREA of the page and at least CLIP_FOREGROUND_MIN_INSIDE on it - the same rule as
        orientation_adapter's separable foreground) is re-sized uniformly by the smaller of the two scale factors,
        centred horizontally where the stretch put it and standing on the same bottom edge, so it keeps its
        proportions and never grows past the stretched box (i.e. into the logos above it). Backdrop children (the
        full-bleed texture, art mostly off the page) keep the stretch. Returns how many children were adjusted."""
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
            nw, nh = w / sx * k, h / sy * k   # original size x the uniform factor
            try:
                ch.SetSize(nw, nh)
                ch.LeftX = x + (w - nw) / 2
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
        hints = [h.strip().lower() for h in (shop.get("master_shop_name"), shop.get("master_shop_name_local")) if h and h.strip()]
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
            (h in o.text.strip().lower() or o.text.strip().lower() in h) for h in hints)}
        if local and not shop.get("master_shop_name_local"):
            ids |= find_local_partner_ids(objs, ids)
        done = 0
        for sh, o in texts:
            if o.id not in ids:
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
                shopname_ids |= {o.id for o in objs if detect_role(o, page_w, page_h) == "shopname"}
                # The master's Tamil shop-name line is not known by content (only the English name is, from the master's
                # file name) - it is the Tamil text stacked next to the matched English line.
                if not shop.get("master_shop_name_local"):
                    partners = find_local_partner_ids(objs, shopname_ids)
                    if shop.get("shop_name_local"):
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
                    brand_rule=load_brand_rule(shop.get("brand")),
                    phone=shop.get("phone"),
                    gst=shop.get("gst"),
                    address_lines=shop.get("address_lines"),
                    contact_ids=contact_ids,
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
                    if p.role == "bg" and old_w > 0 and old_h > 0:
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
                self._replace_nested_shopnames(shapes, shop, warnings)
                return page_w, page_h, placed

            page_w, page_h, placed = step("tile_resize", _resize_and_tile)
            # Cap embedded bitmaps at SIGNAGE_MAX_BITMAP_DPI (300) at their PLACED size, before the save: a board smaller
            # than its master otherwise carries the master's full-resolution photos into every output (measured live: a
            # 10x4 in board from a 125x48 in master saved 208.7 MB in 14.1 s; capped, 10.0 MB in 0.8 s). Downsamples only,
            # keeps every bitmap's box - see corel_util.cap_bitmap_resolution.
            bitmap_cap = step("bitmaps", lambda: corel_util.cap_bitmap_resolution(doc, corel_util.max_bitmap_dpi()))
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
        report["bitmap_cap"] = {"max_dpi": corel_util.max_bitmap_dpi(), **bitmap_cap}
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
