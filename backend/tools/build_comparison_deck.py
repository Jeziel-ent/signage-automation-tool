"""Designer-vs-Automated comparison deck from tools/example_eval.py results (any brand).
    python tools/build_comparison_deck.py <out.pptx> <brand>[:<Title>] [<brand>[:<Title>] ...]
Each brand needs dataset_analysis/example_eval/<brand>/known/results.json (+ unseen/ for the unseen-size simulation).
A brand with no results (e.g. its files cannot be opened by the installed CorelDRAW) gets an explanatory section instead."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import example_layout as X  # noqa: E402
from tools.example_eval import CLOSE, EXACT, verdict  # noqa: E402

EVAL = ROOT / "dataset_analysis" / "example_eval"
RED, INK, GREY = RGBColor(0xE3, 0x1E, 0x24), RGBColor(0x1F, 0x1F, 0x24), RGBColor(0x6B, 0x6B, 0x76)
GREEN, AMBER, WHITE, SOFT = RGBColor(0x1E, 0x8E, 0x4E), RGBColor(0xE0, 0x8A, 0x00), RGBColor(0xFF, 0xFF, 0xFF), RGBColor(0xF6, 0xF6, 0xF8)
SW, SH = Inches(13.333), Inches(7.5)
COL = {"EXACT": GREEN, "CLOSE": AMBER, "OFF": RED, "ART": GREY, "FAILED": RED, "n/a": GREY}
LABEL = {"EXACT": "EXACT MATCH", "CLOSE": "CLOSE", "OFF": "OFF", "ART": "NEEDS ART NOT IN MASTER", "FAILED": "FAILED", "n/a": "-"}


def load(brand, mode):
    p = EVAL / brand / mode / "results.json"
    return {r["file"]: r for r in json.loads(p.read_text(encoding="utf-8"))} if p.exists() else {}


def classify(row, art_missing: bool) -> str:
    if row is None:
        return "n/a"
    if row.get("status") != "done" or not row.get("geometry"):
        return "FAILED"
    if art_missing:                      # the designer's board uses pictures or copies the master does not have
        return "ART"
    if verdict(row["geometry"], EXACT)[0]:
        return "EXACT"
    if verdict(row["geometry"], CLOSE)[0]:
        return "CLOSE"
    return "OFF"


# ---------------------------------------------------------------------------------------------- drawing helpers
def tb(slide, x, y, w, h, text, size=14, bold=False, color=INK, font="Calibri", align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    for i, ln in enumerate(text if isinstance(text, list) else [text]):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(4)
        r = p.add_run()
        head = ln.startswith("## ")
        r.text = ln[3:] if head else ln
        r.font.size, r.font.bold, r.font.name = Pt(size + (1 if head else 0)), bold or head, font
        r.font.color.rgb = RED if head else color
    return box


def pill(slide, x, y, w, h, text, fill, size=13):
    s = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    s.fill.solid()
    s.fill.fore_color.rgb = fill
    s.line.fill.background()
    tf = s.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = text
    r.font.size, r.font.bold, r.font.name = Pt(size), True, "Calibri"
    r.font.color.rgb = WHITE


def card(slide, x, y, w, h, fill=SOFT):
    c = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    c.fill.solid()
    c.fill.fore_color.rgb = fill
    c.line.fill.background()
    return c


def jpeg(path, max_side=1500):
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=82)
    b.seek(0)
    return b, im.size


def place(slide, path, x, y, w, h):
    """Picture scaled to fit (x, y, w, h) without cropping, centred in it."""
    try:
        b, (iw, ih) = jpeg(path)
    except Exception:
        tb(slide, x, y, w, h, "(image not available)", 12, False, GREY, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        return
    k = min(w / iw, h / ih)
    pw, ph = int(iw * k), int(ih * k)
    slide.shapes.add_picture(b, x + (w - pw) // 2, y + (h - ph) // 2, pw, ph)


def bg(slide, color=WHITE):
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = color


def title(slide, text, sub=None):
    bg(slide)
    tb(slide, Inches(0.6), Inches(0.4), Inches(12.1), Inches(0.8), text, 32, True, INK, "Cambria")
    if sub:
        tb(slide, Inches(0.6), Inches(1.15), Inches(12.1), Inches(0.5), sub, 15, False, GREY)


# ---------------------------------------------------------------------------------------------- brand section
def brand_section(prs, brand: str, label: str):
    blank = prs.slide_layouts[6]
    known, unseen = load(brand, "known"), load(brand, "unseen")
    lib = X.library(brand)
    if not known or not lib:
        return blocked_section(prs, brand, label)
    refs_p = EVAL / brand / "refs.json"           # optional higher-resolution designer pictures (file -> image)
    better = json.loads(refs_p.read_text(encoding="utf-8")) if refs_p.exists() else {}
    for rr in list(known.values()) + list(unseen.values()):
        if rr["file"] in better:
            rr["ref"] = better[rr["file"]]
    masters = {m["id"]: m for m in lib["masters"]}
    boards = {b["file"]: b for b in lib["boards"]}
    rows = sorted(known.values(), key=lambda r: ((r["sno"] or 0), r["w"]))
    art = {r["file"]: (not X.compatible(boards[r["file"]], masters[boards[r["file"]]["master"]])) or boards[r["file"]].get("extra", 0) >= 3
           for r in rows}
    kc = {r["file"]: classify(r, art[r["file"]]) for r in rows}
    uc = {r["file"]: classify(unseen.get(r["file"]), art[r["file"]]) for r in rows}
    n = len(rows)
    cnt = lambda d, k: sum(1 for v in d.values() if v == k)
    reproducible = n - cnt(kc, "ART")
    vis = [r["visual"]["combined"] for r in rows if r.get("visual")]

    # divider
    s = prs.slides.add_slide(blank)
    bg(s, INK)
    tb(s, Inches(0.8), Inches(2.6), Inches(11.5), Inches(1), label.upper(), 40, True, WHITE, "Cambria")
    tb(s, Inches(0.8), Inches(3.7), Inches(11.5), Inches(0.8), f"{n} designer boards vs the automated engine", 22, False, RGBColor(0xF4, 0xB4, 0xB6))

    # at a glance
    s = prs.slides.add_slide(blank)
    title(s, f"{label} - at a glance")
    stats = [(f"{cnt(kc, 'EXACT')}/{n}", "EXACT at sizes the designer made", "every picture, badge and shop-name line within 2 % of the page of her CDR", GREEN),
             (f"{cnt(kc, 'EXACT') + cnt(kc, 'CLOSE')}/{n}", "EXACT or CLOSE (within 5 %)", f"{cnt(kc, 'OFF')} off, {cnt(kc, 'FAILED')} failed", AMBER),
             (f"{cnt(uc, 'EXACT')}/{n}", "EXACT for a size never made before", "same test with every board of that size hidden from the engine", GREEN),
             (f"{cnt(kc, 'ART')}", "boards need art the master lacks", "other pictures or repeated copies of the artwork - not reproducible from this master", GREY)]
    for i, (big, lab, sub, col) in enumerate(stats):
        x = Inches(0.6) + i * Inches(3.1)
        card(s, x, Inches(1.6), Inches(2.9), Inches(2.5))
        tb(s, x + Inches(0.2), Inches(1.75), Inches(2.5), Inches(0.9), big, 44, True, col, "Cambria")
        tb(s, x + Inches(0.2), Inches(2.75), Inches(2.5), Inches(0.5), lab, 15, True, INK)
        tb(s, x + Inches(0.2), Inches(3.3), Inches(2.5), Inches(0.8), sub, 11, False, GREY)
    lines = ["## How it is measured",
             "Every generated board is opened in CorelDRAW and each picture, badge, logo, table composite and shop-name line is compared with the same element in the designer's own CDR (EXACT = within 2 % of the page for position, 3 % for size; CLOSE = 5 % / 6 %). Shop-name lines are scored on position with a 4x wider tolerance (she centres each name by eye; her own boards of one size differ by 5-10 %) and on height only when it is off by more than 15 % (it follows how many lines the name wraps into).",
             f"Known sizes = what production does today. Unseen sizes = the same boards re-generated with every designer board of that size hidden, i.e. how a size nobody has made would come out. Reproducible boards (not counting 'needs art'): {reproducible}."]
    if vis:
        lines.append(f"Average image similarity to the designer's preview (SSIM / edge blend): {sum(vis) / len(vis):.2f}")
    tb(s, Inches(0.6), Inches(4.5), Inches(12.1), Inches(2.6), lines, 13)

    # breakdown table
    s = prs.slides.add_slide(blank)
    title(s, f"{label} - result breakdown")
    heads = ["", "EXACT", "CLOSE", "OFF", "Needs art not in master", "Failed"]
    tbl = s.shapes.add_table(4, 6, Inches(0.6), Inches(1.7), Inches(12.1), Inches(2.4)).table
    fam = {r["file"]: ("Landscape" if r["w"] >= 1.25 * r["h"] else "Portrait / square") for r in rows}
    def line(label_, d, sel=None):
        files = [f for f in d if sel is None or fam[f] == sel]
        return [label_, *[str(sum(1 for f in files if d[f] == k)) for k in ("EXACT", "CLOSE", "OFF", "ART", "FAILED")]]
    data = [heads, line("Known sizes - all", kc), line("Unseen sizes - all", uc), line("Known - landscape only", kc, "Landscape")]
    for r_, rowvals in enumerate(data):
        for c_, v in enumerate(rowvals):
            cell = tbl.cell(r_, c_)
            cell.text = v
            for p in cell.text_frame.paragraphs:
                for run in p.runs:
                    run.font.size, run.font.name, run.font.bold = Pt(15), "Calibri", (r_ == 0 or c_ == 0)
    worst = []
    for r in rows:
        ok, bad = verdict(r["geometry"], CLOSE) if r.get("geometry") else (False, [("-", "-")])
        if not ok and kc[r["file"]] != "ART":
            worst.append(f"S.No {r['sno']} {r['name'][:26]} ({r['w']:g}x{r['h']:g} {r['unit']}): " + ", ".join(f"{k} {v}" for k, v in bad[:3]))
    tb(s, Inches(0.6), Inches(4.5), Inches(12.1), Inches(2.6), ["## Boards that are still off at known sizes" if worst else "Every board that can be built from this master is at least CLOSE at known sizes", *worst[:7]], 13)

    # per-board slides
    for r in rows:
        s = prs.slides.add_slide(blank)
        bg(s)
        pill(s, Inches(0.5), Inches(0.35), Inches(1.15), Inches(0.5), f"S.No {r['sno']}", RED, 15)
        tb(s, Inches(1.85), Inches(0.3), Inches(7.6), Inches(0.6), r["name"], 26 if len(r["name"]) <= 26 else (20 if len(r["name"]) <= 36 else 16), True, INK, "Cambria", anchor=MSO_ANCHOR.MIDDLE)
        tb(s, Inches(9.6), Inches(0.35), Inches(3.2), Inches(0.5), f"{r['w']:g} X {r['h']:g} {r['unit']}", 15, False, GREY, align=PP_ALIGN.RIGHT, anchor=MSO_ANCHOR.MIDDLE)
        pill(s, Inches(0.5), Inches(0.98), Inches(2.5), Inches(0.36), "Known size: " + LABEL[kc[r["file"]]], COL[kc[r["file"]]], 11)
        pill(s, Inches(3.15), Inches(0.98), Inches(2.9), Inches(0.36), "Unseen size: " + LABEL[uc[r["file"]]], COL[uc[r["file"]]], 11)
        wide = r["w"] >= 1.25 * r["h"]
        top, bottom = Inches(1.55), Inches(7.2)
        if wide:
            h2 = (bottom - top - Inches(0.7)) // 2
            tb(s, Inches(0.5), top, Inches(3), Inches(0.3), "DESIGNER", 11, True, GREY)
            place(s, r.get("ref"), Inches(0.5), top + Inches(0.3), Inches(12.3), h2 - Inches(0.3))
            tb(s, Inches(0.5), top + h2 + Inches(0.15), Inches(3), Inches(0.3), "AUTOMATED", 11, True, RED)
            place(s, r.get("png"), Inches(0.5), top + h2 + Inches(0.45), Inches(12.3), h2 - Inches(0.3))
        else:
            wcol = Inches(6.0)
            tb(s, Inches(0.5), top, Inches(3), Inches(0.3), "DESIGNER", 11, True, GREY)
            place(s, r.get("ref"), Inches(0.5), top + Inches(0.3), wcol, bottom - top - Inches(0.4))
            tb(s, Inches(6.9), top, Inches(3), Inches(0.3), "AUTOMATED", 11, True, RED)
            place(s, r.get("png"), Inches(6.9), top + Inches(0.3), wcol, bottom - top - Inches(0.4))
        notes = [f"Known size: {LABEL[kc[r['file']]]}; unseen size: {LABEL[uc[r['file']]]}"]
        if r.get("geometry"):
            for k, v in verdict(r["geometry"], EXACT)[1][:6]:
                notes.append(f"{k}: {v}")
        if r.get("warnings"):
            notes.append("Warnings: " + "; ".join(r["warnings"])[:300])
        s.notes_slide.notes_text_frame.text = "\n".join(notes)


def blocked_section(prs, brand: str, label: str):
    """A brand whose files this machine's CorelDRAW cannot open: say so, with the evidence."""
    blank = prs.slide_layouts[6]
    s = prs.slides.add_slide(blank)
    bg(s, INK)
    tb(s, Inches(0.8), Inches(2.6), Inches(11.5), Inches(1), label.upper(), 40, True, WHITE, "Cambria")
    tb(s, Inches(0.8), Inches(3.7), Inches(11.5), Inches(0.8), "not compared yet - see next slide", 22, False, RGBColor(0xF4, 0xB4, 0xB6))
    s = prs.slides.add_slide(blank)
    title(s, f"{label} - blocked by the CorelDRAW version")
    info = json.loads((EVAL / brand / "blocked.json").read_text(encoding="utf-8")) if (EVAL / brand / "blocked.json").exists() else {}
    tb(s, Inches(0.6), Inches(1.7), Inches(12.1), Inches(5.3), [
        "## What happened",
        info.get("what", "The designer files could not be opened by the CorelDRAW installed on this machine."),
        "## Evidence",
        *info.get("evidence", []),
        "## What is needed",
        *info.get("needed", ["Open the masters in CorelDRAW 2024 or newer and 'Save As' version 21 (CorelDRAW 2019 format), or run the evaluation on a machine with CorelDRAW 2024+."]),
        "## Ready once unblocked",
        " the generic example engine, its library builder (tools/build_example_library.py) and the evaluation (tools/example_eval.py) need no brand-specific work."], 14)


def findings(prs):
    blank = prs.slide_layouts[6]
    s = prs.slides.add_slide(blank)
    title(s, "What changed in this round, and what is still open")
    left = ["## Done in this round",
            "Confidence tag on every finished board: Exact size (designer board of that size copied), Check (nearest-size copy), Draft (far or rules fallback) - on the queue and Recently generated.",
            "Several masters per brand, one per design style: the engine routes to the master the nearest designer board was made from (the flat-box 9x3 ft style no longer needs unavailable art).",
            "Shop-name lines: wrapped into the designer's text block (1 to her maximum lines), her font size where her boards agree, a second-script line added when the master has one name line.",
            "Repeated copies: a wide board that shows the logo / badge twice is now reproduced by duplicating the master element at her positions.",
            "## Still open",
            "Sizes nobody has made are still a nearest-board guess.",
            "Hangyo's 10x3 ft master has no shop-name text, so its boards need a sample name added to the master.",
            "Tamil spelling is automatic transliteration; the designer types her own."]
    right = ["## Suggested next",
             "1. 'Approve and add to library': a corrected board becomes a new example, so every size the designers touch is learned.",
             "2. One-click library build from a folder of designer CDRs inside the app.",
             "3. Warn on upload when a master is saved by a newer CorelDRAW than installed (the engine already refuses with a clear message).",
             "4. Take the Tamil name from the shop sheet when present and flag transliterated names for review.",
             "5. Add a sample shop-name text to every master (Hangyo's 10x3 ft one has none).",
             "6. Re-save Agni and the other Dalmia files (CorelDRAW 2024) to test two more brands.",
             "7. Master reuse between boards is off in the evaluation: duplicated shapes make the undo check unreliable - a failed board is retried alone in the app."]
    tb(s, Inches(0.6), Inches(1.4), Inches(6.0), Inches(5.6), left, 13)
    tb(s, Inches(6.9), Inches(1.4), Inches(5.9), Inches(5.6), right, 13)


def main(out, specs):
    prs = Presentation()
    prs.slide_width, prs.slide_height = SW, SH
    blank = prs.slide_layouts[6]
    s = prs.slides.add_slide(blank)
    bg(s, INK)
    tb(s, Inches(0.8), Inches(2.3), Inches(11.5), Inches(1), "SIGNAGE AUTOMATION", 40, True, WHITE, "Cambria")
    tb(s, Inches(0.8), Inches(3.0), Inches(11.5), Inches(0.7), "Designer vs Automated", 28, True, WHITE, "Cambria")
    tb(s, Inches(0.8), Inches(3.9), Inches(11.5), Inches(0.7), "Brand-agnostic example engine, real CorelDRAW output, measured against the designers' own files", 20, False, RGBColor(0xF4, 0xB4, 0xB6))
    tb(s, Inches(0.8), Inches(5.0), Inches(11.5), Inches(1.2), [" - ".join(l for _, l in specs)], 16, False, RGBColor(0xC9, 0xC9, 0xD0))
    for brand, label in specs:
        brand_section(prs, brand, label)
    findings(prs)
    prs.save(out)
    print("saved", out, len(prs.slides), "slides")


if __name__ == "__main__":
    specs = []
    for a in sys.argv[2:]:
        b, _, l = a.partition(":")
        specs.append((b, l or b.capitalize()))
    main(sys.argv[1], specs)
