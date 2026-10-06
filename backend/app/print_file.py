"""The free-form "Create Print File" sheet (sidebar page): the print details sheet the printing team receives, built from
uploaded CDR / image files and typed details instead of converted shops.

A4 portrait pages, 1600 reference units wide (2480 x 3508 px at 300 dpi); content that does not fit moves to the next page, laid out with the same
header, fonts, thumbnails and captions as `print_sheet.py` (which it reuses and does not change):

    red angled "PRINT DETAILS" block + title band, DATE / PROJECT NO, optional right-aligned lines (region, branch ...);
    one or more SECTIONS, each a full-width rule and a red angled bar with the section name (a material / board type),
    QTY : n Nos and Sq.feet : n, then a 3-column grid of cards (preview + caption `01 - 10 X 4 Inch - TYPE - NAME`).

QTY and Sq.feet of a section are computed from its items (an item's `qty` multiplies both - e.g. one design printed 55 times) unless
the section carries an override. A PDF holds every page; JPEG is one image, or a zip of page images. Pillow only, no CorelDRAW.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from . import print_sheet as ps

COLS = 3
GAP_U = 40                   # between cards (reference units)
BAR_H_U = 102                # the red bar's height
SECTION_GAP_U = 70           # above a section's rule
BOX_ASPECT = 0.72            # card box height / width
CAP_PT = 8.0                 # caption size; wraps to ps.CAPTION_LINES lines
MAX_ITEMS = 300


@dataclass
class Item:
    name: str
    width: float
    height: float
    unit: str = "in"
    board_type: str = ""
    no: int | str = ""
    qty: int = 1
    image: Path | None = None


@dataclass
class Section:
    name: str
    items: list[Item] = field(default_factory=list)
    qty_override: int | None = None
    sqft_override: float | None = None


@dataclass
class FileMeta:
    title: str = ""
    project_no: str = ""
    date: str = ""                       # already formatted (DD.MM.YYYY)
    lines: list[str] = field(default_factory=list)   # right-aligned header lines (region, branch ...), at most 2 are drawn


def section_totals(sec: Section) -> tuple[int, float]:
    """(QTY, Sq.feet) of a section: item quantities times item areas, unless overridden."""
    qty = sum(max(1, i.qty) for i in sec.items)
    sqft = sum(max(1, i.qty) * ps.sq_feet(i.width, i.unit, i.height, i.unit) for i in sec.items)
    return (sec.qty_override if sec.qty_override is not None else qty,
            sec.sqft_override if sec.sqft_override is not None else sqft)


def _shop(i: Item, n: int) -> ps.SheetShop:
    return ps.SheetShop(no=i.no or n, name=i.name, width=i.width, width_unit=i.unit, height=i.height, height_unit=i.unit,
                        image=i.image)


def _grid() -> dict:
    card_u = (ps.REF_W - 2 * ps.SIDE - (COLS - 1) * GAP_U) / COLS
    card_pt = card_u * ps.K
    box_pt = card_pt * BOX_ASPECT
    return {"card_u": card_u, "card_w": card_pt, "box_h": box_pt, "cap_pt": CAP_PT,
            "row_u": (box_pt + ps.CAP_GAP_PT + CAP_PT * 1.3 * ps.CAPTION_LINES + 16) / ps.K}


PAGE_H_U = ps.PAGE_PT[1] / ps.K          # an A4 page is 1600 x 2263 reference units
HEADER_U = 460.0                          # the header (every page repeats it)
FOOT_U = 90.0                             # kept free at the bottom for "Page X of Y"


def _draw_header(d, meta: FileMeta) -> None:
    ps._poly_u(d, [(0, 0), (625, 0), (527, 125), (0, 125)], ps.RED)
    ps._poly_u(d, [(650, 0), (ps.REF_W, 0), (ps.REF_W, 125), (553, 125)], ps.GREY_BAND)
    ps._text_cap(d, ps._u(ps.SIDE), ps._u(38), "PRINT DETAILS",
                 ps._fit("head", "PRINT DETAILS", 66 * ps.K, (527 - ps.SIDE - 12) * ps.K), ps.WHITE)
    title = (meta.title or "").upper()
    if title:
        tf = ps._fit("head", title, 66 * ps.K, (ps.REF_W - 670 - 40) * ps.K)
        cap_h = tf.getbbox("H")[3] - tf.getbbox("H")[1]
        ps._text_cap(d, ps._u(670), ps._u(62.5) - cap_h / 2, title, tf, ps.DARK)
    ps._label_value(d, ps.SIDE, 188, "DATE : ", meta.date or "-", 72 * ps.K)
    ps._label_value(d, ps.SIDE, 308, "PROJECT NO : ", meta.project_no or "-", 72 * ps.K)
    for cap, line in zip((188, 308), [ln.strip().upper() for ln in meta.lines if ln.strip()][:2]):
        ps._text_cap(d, ps._u(ps.REF_W - ps.SIDE), ps._u(cap), line, ps._fit("head", line, 72 * ps.K, 620 * ps.K), ps.DARK, "right")


def _bar(d, y_u: float, width: int, name: str, totals: tuple[int, float] | None) -> None:
    """A section's rule and red angled bar; `totals` None on a continuation page."""
    d.rectangle([0, ps._u(y_u - 2), width, ps._u(y_u + 2)], fill=ps.DARK)
    ps._poly_u(d, [(0, y_u), (820, y_u), (690, y_u + BAR_H_U), (0, y_u + BAR_H_U)], ps.RED)
    ps._text_cap(d, ps._u(ps.SIDE), ps._u(y_u + 25), name, ps._fit("head", name, 66 * ps.K, (690 - ps.SIDE - 20) * ps.K), ps.WHITE)
    if totals:
        ps._label_value(d, 860, y_u + 35, "QTY : ", f"{totals[0]} Nos", 62 * ps.K)
        ps._label_value(d, 1230, y_u + 35, "Sq.feet : ", ps.fmt_sqft(totals[1]), 62 * ps.K)


class _Pager:
    """Flows sections onto A4 pages: owns the current page, the y cursor and the layout limits."""

    def __init__(self, meta: FileMeta):
        self.meta = meta
        self.width, self.height = round(ps._u(ps.REF_W)), round(ps._u(PAGE_H_U))
        self.grid = _grid()
        self.limit = PAGE_H_U - FOOT_U
        self.pages: list[Image.Image] = []
        self.img = self.d = None
        self.y = HEADER_U
        self.new_page()

    def new_page(self) -> None:
        self.img = Image.new("RGB", (self.width, self.height), ps.WHITE)
        self.d = ImageDraw.Draw(self.img)
        _draw_header(self.d, self.meta)
        self.pages.append(self.img)
        self.y = HEADER_U

    def _card(self, item: Item, n: int, col: int, y_u: float) -> None:
        g = self.grid
        x_pt, y_pt = (ps.SIDE + col * (g["card_u"] + GAP_U)) * ps.K, y_u * ps.K
        shop = _shop(item, n + 1)
        ps._draw_thumb(self.img, self.d, shop, x_pt, y_pt, g)
        self.d = ImageDraw.Draw(self.img)
        ps._draw_caption(self.d, x_pt, y_pt + g["box_h"] + ps.CAP_GAP_PT, g["card_w"], ps.caption(shop, item.board_type), g, ps.CAPTION)

    def _rows(self, sec: Section, n: int, y_u: float) -> tuple[int, float]:
        """Draw whole rows of cards from item `n` while they fit; returns the next item and the y below the last row."""
        while n < len(sec.items) and y_u + self.grid["row_u"] <= self.limit:
            for col in range(min(COLS, len(sec.items) - n)):
                self._card(sec.items[n + col], n + col, col, y_u)
            n += COLS
            y_u += self.grid["row_u"]
        return min(n, len(sec.items)), y_u

    def section(self, sec: Section) -> None:
        name = (sec.name or "BOARD").upper()
        n, first = 0, True
        while n < len(sec.items):
            need = SECTION_GAP_U + BAR_H_U + 40 + self.grid["row_u"]
            if self.y + need > self.limit and self.y > HEADER_U:
                self.new_page()
            y = self.y + (SECTION_GAP_U if self.y > HEADER_U or first else 0)
            _bar(self.d, y, self.width, name if first else f"{name} (CONT.)", section_totals(sec) if first else None)
            first = False
            n, self.y = self._rows(sec, n, y + BAR_H_U + 40)
            if n < len(sec.items):
                self.new_page()

    def number_pages(self) -> None:
        if len(self.pages) < 2:
            return
        pf = ps._font("caption", 7.5)
        for i, img in enumerate(self.pages, 1):
            ps._text_cap(ImageDraw.Draw(img), (ps.PAGE_PT[0] - ps.SIDE * ps.K) * ps._P, (ps.PAGE_PT[1] - 20) * ps._P,
                         f"Page {i} of {len(self.pages)}", pf, ps.MUTED, "right")


def render_pages(meta: FileMeta, sections: list[Section]) -> list[Image.Image]:
    """A4 portrait pages (2480 x 3508 px). Sections flow down the page; a row that does not fit moves to the next page, which
    repeats the header, and a section split over pages repeats its bar as "NAME (CONT.)"."""
    if not any(s.items for s in sections):
        raise ValueError("add at least one item")
    pager = _Pager(meta)
    for sec in sections:
        if sec.items:
            pager.section(sec)
    pager.number_pages()
    return pager.pages


def render(meta: FileMeta, sections: list[Section]) -> Image.Image:
    """The first page (see render_pages)."""
    return render_pages(meta, sections)[0]


def write(meta: FileMeta, sections: list[Section], fmt: str, out_path: Path) -> tuple[Path, str]:
    """Render and save: a PDF (every page), a JPEG (one page) or a zip of JPEG pages. Returns (path, media type)."""
    pages = render_pages(meta, sections)
    out_path = Path(out_path)
    if fmt == "pdf":
        p = out_path.with_suffix(".pdf")
        pages[0].save(p, "PDF", resolution=ps.DPI, save_all=True, append_images=pages[1:], title="Print Details")
        return p, "application/pdf"
    if fmt != "jpeg":
        raise ValueError("format must be 'pdf' or 'jpeg'")
    if len(pages) == 1:
        p = out_path.with_suffix(".jpg")
        pages[0].save(p, "JPEG", quality=95, dpi=(ps.DPI, ps.DPI), subsampling=0)
        return p, "image/jpeg"
    p = out_path.with_suffix(".zip")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_STORED) as z:
        for i, page in enumerate(pages, 1):
            buf = io.BytesIO()
            page.save(buf, "JPEG", quality=95, dpi=(ps.DPI, ps.DPI), subsampling=0)
            z.writestr(f"Print_File_page{i}.jpg", buf.getvalue())
    return p, "application/zip"
