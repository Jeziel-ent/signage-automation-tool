"""The "Print Details" summary sheet for a batch of converted shops (Automation page -> "Create Print File").

Rendered with Pillow, no CorelDRAW, as **A4 portrait** (210 x 297 mm = 595.28 x 841.89 pt) in the visual style of the reference
sheet `assets/DT-2026-00065__...print-details-2...jpg` (2482 x 1773 px, landscape): its elements are laid out in the
reference's own units (the reference scaled to 2000 wide) and mapped onto the portrait page with `K` points per unit, so each
one can be compared with the reference directly:

    a red angled "PRINT DETAILS" block, the title on a grey band;
    DATE : <red>, PROJECT NO : <red>, the region / location right-aligned on the project line; a full-width rule;
    a red angled bar with the board type, QTY : <red> Nos, Sq.feet : <red>;
    a grid whose FIRST card is always the yellow "CDR & PDF" folder (drawn here, crisp at any size), then one card per shop:
    the preview bottom-aligned in its box with a soft shadow and the caption
    `01 - 10 X 4 Inch - ACP BOARD - FAYAZ HARDWREAS (COLACHAL)` centred below it, at most 2 lines.

Grid: 2 columns x 3 rows while the folder and every shop fit on one page, else 3 x 3 (box ~120 pt high); further pages repeat
the header and carry "Page X of Y". A PDF holds every page, JPEG pages come back as a zip. Honest limits: Pillow here has no
complex-script shaping (no libraqm), so Tamil text is drawn with Nirmala UI but its conjuncts are not shaped; the PDF is a
raster page (300 dpi), not vector text.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .layout import to_mm

# ---- page geometry ----------------------------------------------------------------------------------------------------
PAGE_PT = (595.28, 841.89)            # A4 portrait
DPI = 300
_P = DPI / 72.0                       # points -> canvas pixels
PAGE_W, PAGE_H = round(PAGE_PT[0] * _P), round(PAGE_PT[1] * _P)   # 2480 x 3508
K = 0.372                             # points per reference unit (the reference is 2000 units wide)
REF_W = PAGE_PT[0] / K                # the portrait page's width in reference units (1600)
SIDE = 88                             # reference left/right text inset (units)
GRID_TOP_U = 568                      # below the board-type bar (it ends at 532)
BOTTOM_PT = 26.0                      # bottom margin; the page number sits in it
GAP_PT = 14.0                         # between grid cells
CAP_GAP_PT = 7.0                      # between a box and its caption
CAPTION_LINES = 2

# ---- colours (sampled from the reference) ------------------------------------------------------------------------------
RED = (227, 30, 36)
DARK = (35, 31, 32)
GREY_BAND = (230, 231, 232)
CAPTION = (60, 60, 60)
MUTED = (140, 140, 140)
WHITE = (255, 255, 255)

MM_PER_FOOT = 304.8
UNIT_WORD = {"in": "Inch", "ft": "Feet", "cm": "cm", "mm": "mm"}
_TAMIL = re.compile(r"[\u0B80-\u0BFF]")
FONT_DIR = Path("C:/Windows/Fonts")


@dataclass
class SheetMeta:
    title: str = ""
    project_no: str = ""
    date: str = ""          # already formatted for display (DD.MM.YYYY)
    location: str = ""      # the region / location code shown right-aligned (e.g. "MDU", "CHENNAI")
    board_type: str = ""


@dataclass
class SheetShop:
    no: int
    name: str
    width: float
    width_unit: str
    height: float
    height_unit: str
    image: Path | None = None   # a PNG/JPEG preview of the board, or None
    has_cdr: bool = False       # kept for callers; not drawn - the "CDR & PDF" folder card stands for the files
    has_pdf: bool = False


# --------------------------------------------------------------------------- numbers and text

def sq_feet(width: float, width_unit: str, height: float, height_unit: str) -> float:
    return (to_mm(width, width_unit) / MM_PER_FOOT) * (to_mm(height, height_unit) / MM_PER_FOOT)


def totals(shops: list[SheetShop]) -> tuple[int, float]:
    """(quantity, total square feet) - one board per shop."""
    return len(shops), sum(sq_feet(s.width, s.width_unit, s.height, s.height_unit) for s in shops)


def fmt_sqft(total: float) -> str:
    """Whole square feet like the reference (185.25 -> "185"); one decimal below 10 so a small board is not "0"."""
    if total >= 10:
        return str(int(round(total)))
    return _num(round(total, 1))


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def format_date(value: str) -> str:
    """The date picker's YYYY-MM-DD becomes DD.MM.YYYY (the reference's style); anything else is kept as typed."""
    m = re.fullmatch(r"\s*(\d{4})-(\d{2})-(\d{2})\s*", value or "")
    return f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else (value or "").strip()


def caption(shop: SheetShop, board_type: str) -> str:
    """`01 - 10 X 4 Inch - ACP BOARD - FAYAZ HARDWREAS (COLACHAL)` (the reference's caption format)."""
    wu, hu = UNIT_WORD.get(shop.width_unit, shop.width_unit), UNIT_WORD.get(shop.height_unit, shop.height_unit)
    size = f"{_num(shop.width)} X {_num(shop.height)} {wu}" if wu == hu else f"{_num(shop.width)} {wu} X {_num(shop.height)} {hu}"
    parts = [f"{shop.no:02d}", size]
    if board_type.strip():
        parts.append(board_type.strip())
    parts.append(shop.name.strip())
    return " - ".join(parts)


# --------------------------------------------------------------------------- fonts

_font_cache: dict = {}


def _font(kind: str, pt: float, text: str = "") -> ImageFont.FreeTypeFont:
    """kind "head": Bahnschrift Bold SemiCondensed (the closest installed match to the reference's heading face);
    "caption": Segoe UI. Tamil text switches to Nirmala UI. Falls back to DejaVu / Pillow's own font off Windows."""
    size = max(6, int(round(pt * _P)))
    tamil = bool(_TAMIL.search(text))
    key = (kind, size, tamil)
    if key in _font_cache:
        return _font_cache[key]
    f = None
    try:
        if tamil:
            f = ImageFont.truetype(str(FONT_DIR / "Nirmala.ttc"), size, index=1 if kind == "head" else 0)
        elif kind == "head":
            f = ImageFont.truetype(str(FONT_DIR / "bahnschrift.ttf"), size)
            f.set_variation_by_name("Bold SemiCondensed")
        else:
            f = ImageFont.truetype(str(FONT_DIR / "segoeui.ttf"), size)
    except Exception:
        f = None
    if f is None:
        try:
            f = ImageFont.truetype("DejaVuSans-Bold.ttf" if kind == "head" else "DejaVuSans.ttf", size)
        except Exception:
            f = ImageFont.load_default(size)
    _font_cache[key] = f
    return f


def _fit(kind: str, text: str, pt: float, max_w_pt: float) -> ImageFont.FreeTypeFont:
    """The font at `pt`, shrunk until `text` fits `max_w_pt` (a long title must stay in its band)."""
    f = _font(kind, pt, text)
    while pt > 6 and f.getlength(text) > max_w_pt * _P:
        pt *= 0.95
        f = _font(kind, pt, text)
    return f


def _wrap(text: str, font, max_w_px: float, max_lines: int) -> list[str]:
    """Word-wrap to `max_lines`; an overflowing last line ends with an ellipsis."""
    words, lines, cur = text.split(), [], ""
    for i, word in enumerate(words):
        trial = f"{cur} {word}".strip()
        if font.getlength(trial) <= max_w_px or not cur:
            cur = trial
            continue
        lines.append(cur)
        cur = word
        if len(lines) == max_lines:
            rest = " ".join([cur] + words[i + 1:])
            last = lines[-1] + " " + rest
            while font.getlength(last + "...") > max_w_px and " " in last:
                last = last[:last.rstrip().rfind(" ")]
            lines[-1] = last.rstrip() + "..."
            return lines
    if cur:
        lines.append(cur)
    return lines


# --------------------------------------------------------------------------- drawing helpers

def _u(v: float) -> float:
    """Reference units -> canvas pixels."""
    return v * K * _P


def _poly_u(d, pts, fill) -> None:
    d.polygon([(_u(x), _u(y)) for x, y in pts], fill=fill)


def _text_cap(d, x_px: float, cap_top_px: float, text: str, font, fill, align: str = "left") -> float:
    """Draw `text` with its capitals starting at `cap_top_px`; returns its right edge (px)."""
    w = font.getlength(text)
    x = x_px - (w if align == "right" else w / 2 if align == "center" else 0)
    d.text((x, cap_top_px - font.getbbox("H")[1]), text, font=font, fill=fill)
    return x + w


def _label_value(d, x_u: float, cap_u: float, label: str, value: str, pt: float, align: str = "left") -> None:
    """`LABEL : ` dark, the value red - the reference's key/value style."""
    f = _font("head", pt, label + value)
    x = _u(x_u)
    if align == "right":
        x -= f.getlength(label) + f.getlength(value)
    x = _text_cap(d, x, _u(cap_u), label, f, DARK)
    _text_cap(d, x, _u(cap_u), value, f, RED)


def _load_thumb(path: Path | None) -> Image.Image | None:
    if path is None:
        return None
    try:
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as im:
            im.load()
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                flat = Image.new("RGB", im.size, WHITE)
                flat.paste(im, mask=im.split()[-1])
                return flat
            return im.convert("RGB")
    except Exception:
        return None


# --------------------------------------------------------------------------- header (reference geometry)

def _draw_header(d, meta: SheetMeta) -> None:
    # top band: the red angled "PRINT DETAILS" block, a white gap, the grey title band
    _poly_u(d, [(0, 0), (625, 0), (527, 125), (0, 125)], RED)
    _poly_u(d, [(650, 0), (REF_W, 0), (REF_W, 125), (553, 125)], GREY_BAND)
    _text_cap(d, _u(SIDE), _u(38), "PRINT DETAILS", _fit("head", "PRINT DETAILS", 66 * K, (527 - SIDE - 12) * K), WHITE)
    title = (meta.title or "").upper()
    if title:
        tf = _fit("head", title, 66 * K, (REF_W - 670 - 40) * K)
        cap_h = tf.getbbox("H")[3] - tf.getbbox("H")[1]
        _text_cap(d, _u(670), _u(62.5) - cap_h / 2, title, tf, DARK)
    # DATE, PROJECT NO (values red), the region right-aligned on the project line
    _label_value(d, SIDE, 188, "DATE : ", meta.date or "-", 72 * K)
    _label_value(d, SIDE, 308, "PROJECT NO : ", meta.project_no or "-", 72 * K)
    if meta.location:
        loc = meta.location.upper()
        _text_cap(d, _u(REF_W - SIDE), _u(308), loc, _fit("head", loc, 72 * K, 520 * K), DARK, "right")
    # full-width rule, then the red angled board-type bar with QTY and Sq.feet
    d.rectangle([0, _u(428), PAGE_W, _u(432)], fill=DARK)
    _poly_u(d, [(0, 430), (820, 430), (690, 532), (0, 532)], RED)
    board = (meta.board_type or "BOARD").upper()
    _text_cap(d, _u(SIDE), _u(455), board, _fit("head", board, 66 * K, (690 - SIDE - 20) * K), WHITE)


def _draw_totals(d, qty: int, sqft: float) -> None:
    _label_value(d, 860, 465, "QTY : ", f"{qty} Nos", 62 * K)
    _label_value(d, 1230, 465, "Sq.feet : ", fmt_sqft(sqft), 62 * K)


# --------------------------------------------------------------------------- the "CDR & PDF" folder

# The folder as drawn on the reference (its own coordinates, reference units), mapped into any box.
_FOLDER_BOX = (117, 553, 480, 838)


def _draw_folder(img: Image.Image, x: float, y: float, w: float, h: float) -> None:
    """The yellow folder with a document and a red "CDR" emblem, fitted into the px box (x, y, w, h), bottom-aligned."""
    fx0, fy0, fx1, fy1 = _FOLDER_BOX
    s = min(w / (fx1 - fx0), h / (fy1 - fy0))
    ox = x + (w - (fx1 - fx0) * s) / 2 - fx0 * s
    oy = y + h - (fy1 - fy0) * s - fy0 * s
    p = lambda X, Y: (ox + X * s, oy + Y * s)          # noqa: E731
    box = lambda x0, y0, x1, y1: [*p(x0, y0), *p(x1, y1)]  # noqa: E731
    d = ImageDraw.Draw(img)
    r = 18 * s
    d.rounded_rectangle(box(117, 553, 278, 620), r, fill=(245, 184, 36))            # tab
    d.rounded_rectangle(box(117, 592, 480, 838), r, fill=(238, 177, 38))            # back
    d.rounded_rectangle(box(129, 605, 466, 790), 8 * s, fill=(247, 221, 160))       # inside
    x0, y0, x1, y1, fold = 240, 627, 376, 770, 36                                   # document, folded corner
    d.polygon([p(x0, y0), p(x1 - fold, y0), p(x1, y0 + fold), p(x1, y1), p(x0, y1)], fill=WHITE)
    d.polygon([p(x1 - fold, y0), p(x1 - fold, y0 + fold), p(x1, y0 + fold)], fill=(214, 214, 214))
    d.line([p(x0, y0), p(x1 - fold, y0), p(x1, y0 + fold), p(x1, y1)], fill=(190, 190, 190), width=max(1, int(1.2 * s)))
    d.rounded_rectangle(box(208, 640, 318, 745), 10 * s, fill=(232, 48, 58))         # red emblem, above the front flap
    ef = ImageFont.truetype(str(FONT_DIR / "bahnschrift.ttf"), max(6, int(40 * s))) if (FONT_DIR / "bahnschrift.ttf").exists() \
        else _font("head", 40 * s / _P)
    try:
        ef.set_variation_by_name("Bold SemiCondensed")
    except Exception:
        pass
    cx, cy = p(263, 656)
    d.text((cx - ef.getlength("CDR") / 2, cy - ef.getbbox("H")[1]), "CDR", font=ef, fill=WHITE)
    fw, fh = max(1, int(363 * s)), max(1, int(133 * s))                                # front flap, vertical gradient
    flap = Image.new("RGB", (fw, fh))
    fd = ImageDraw.Draw(flap)
    top, bot = (253, 229, 138), (249, 196, 52)
    for yy in range(fh):
        t = yy / max(1, fh - 1)
        fd.line([(0, yy), (fw, yy)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bot)))
    mask = Image.new("L", flap.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, fw - 1, fh - 1], r, fill=255)
    fx, fy = p(117, 705)
    img.paste(flap, (int(fx), int(fy)), mask)
    d.line([p(117, 834), p(480, 834)], fill=(232, 168, 30), width=max(2, int(3 * s)))


# --------------------------------------------------------------------------- grid

def _grid(cols: int) -> dict:
    x0, x1 = SIDE * K, PAGE_PT[0] - SIDE * K
    card_w = (x1 - x0 - GAP_PT * (cols - 1)) / cols
    box_h = 150.0 if cols == 2 else 120.0
    cap_pt = 8.5 if cols == 2 else 7.5
    row_h = box_h + CAP_GAP_PT + CAPTION_LINES * cap_pt * 1.3 + GAP_PT
    top, bottom = GRID_TOP_U * K, PAGE_PT[1] - BOTTOM_PT
    rows = max(1, int((bottom - top + GAP_PT) // row_h))
    return {"cols": cols, "rows": rows, "card_w": card_w, "box_h": box_h, "cap_pt": cap_pt, "row_h": row_h,
            "x0": x0, "top": top, "per_page": cols * rows}


def plan_grid(n_items: int) -> dict:
    """`n_items` grid cells INCLUDING the folder card: 2 columns while they fit on one page, else 3 per row."""
    two = _grid(2)
    return two if n_items <= two["per_page"] else _grid(3)


def _draw_caption(d, x_pt: float, y_pt: float, w_pt: float, text: str, g: dict, fill) -> None:
    f = _font("caption", g["cap_pt"], text)
    for i, line in enumerate(_wrap(text, f, (w_pt - 4) * _P, CAPTION_LINES)):
        cy = (y_pt + i * g["cap_pt"] * 1.3) * _P
        _text_cap(d, (x_pt + w_pt / 2) * _P, cy, line, f, fill, "center")


def _draw_thumb(img: Image.Image, d, shop: SheetShop, x: float, y: float, g: dict) -> None:
    """The board's preview, contain-fitted, bottom-aligned in its box with a soft shadow (as on the reference)."""
    bw, bh = g["card_w"] * _P, g["box_h"] * _P
    bx, by = x * _P, y * _P
    thumb = _load_thumb(shop.image)
    if thumb is None:
        ph = min(bh, bw * 0.35)
        d.rectangle([bx, by + bh - ph, bx + bw, by + bh], fill=(241, 241, 241), outline=(205, 205, 205), width=3)
        nf = _font("caption", g["cap_pt"])
        _text_cap(d, bx + bw / 2, by + bh - ph / 2 - nf.getbbox("H")[3] / 2, "No preview available", nf, MUTED, "center")
        return
    s = min(bw / thumb.width, bh / thumb.height)
    tw, th = max(1, int(thumb.width * s)), max(1, int(thumb.height * s))
    thumb = thumb.resize((tw, th), Image.LANCZOS)
    tx, ty = int(bx + (bw - tw) / 2), int(by + bh - th)
    pad = 14
    shadow = Image.new("L", (tw + 2 * pad, th + 2 * pad), 0)
    ImageDraw.Draw(shadow).rectangle([pad, pad, pad + tw, pad + th], fill=105)
    img.paste((150, 150, 150), (tx - pad + 5, ty - pad + 6), shadow.filter(ImageFilter.GaussianBlur(7)))
    img.paste(thumb, (tx, ty))


def render_pages(meta: SheetMeta, shops: list[SheetShop]) -> list[Image.Image]:
    if not shops:
        raise ValueError("select at least one shop")
    qty, sqft = totals(shops)
    items = [None] + list(shops)                 # None = the "CDR & PDF" folder card, always first
    g = plan_grid(len(items))
    chunks = [items[i:i + g["per_page"]] for i in range(0, len(items), g["per_page"])]
    pages = []
    for n, chunk in enumerate(chunks, 1):
        img = Image.new("RGB", (PAGE_W, PAGE_H), WHITE)
        d = ImageDraw.Draw(img)
        _draw_header(d, meta)
        _draw_totals(d, qty, sqft)
        for i, item in enumerate(chunk):
            r, c = divmod(i, g["cols"])
            x = g["x0"] + c * (g["card_w"] + GAP_PT)
            y = g["top"] + r * g["row_h"]
            if item is None:
                _draw_folder(img, x * _P, y * _P, g["card_w"] * _P, g["box_h"] * _P)
                d = ImageDraw.Draw(img)
                _draw_caption(d, x, y + g["box_h"] + CAP_GAP_PT, g["card_w"], "CDR & PDF", g, CAPTION)
            else:
                _draw_thumb(img, d, item, x, y, g)
                _draw_caption(d, x, y + g["box_h"] + CAP_GAP_PT, g["card_w"], caption(item, meta.board_type), g, CAPTION)
        if len(chunks) > 1:
            pf = _font("caption", 7.5)
            _text_cap(d, (PAGE_PT[0] - SIDE * K) * _P, (PAGE_PT[1] - BOTTOM_PT / 2 - 3) * _P, f"Page {n} of {len(chunks)}",
                      pf, MUTED, "right")
        d.rectangle([0, 0, PAGE_W - 1, PAGE_H - 1], outline=DARK, width=max(2, int(0.75 * _P)))   # the reference's thin frame
        pages.append(img)
    return pages


def write_sheet(meta: SheetMeta, shops: list[SheetShop], fmt: str, out_path: Path) -> tuple[Path, str]:
    """Render and save. Returns (path, media type): a PDF (every page, A4 portrait), a JPEG (one page) or a zip of JPEG pages."""
    pages = render_pages(meta, shops)
    out_path = Path(out_path)
    if fmt == "pdf":
        p = out_path.with_suffix(".pdf")
        pages[0].save(p, "PDF", resolution=DPI, save_all=True, append_images=pages[1:], title="Print Details")
        return p, "application/pdf"
    if fmt != "jpeg":
        raise ValueError("format must be 'pdf' or 'jpeg'")
    if len(pages) == 1:
        p = out_path.with_suffix(".jpg")
        pages[0].save(p, "JPEG", quality=95, dpi=(DPI, DPI), subsampling=0)
        return p, "image/jpeg"
    p = out_path.with_suffix(".zip")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_STORED) as z:
        for i, page in enumerate(pages, 1):
            buf = io.BytesIO()
            page.save(buf, "JPEG", quality=95, dpi=(DPI, DPI), subsampling=0)
            z.writestr(f"Print_Details_page{i}.jpg", buf.getvalue())
    return p, "application/zip"
