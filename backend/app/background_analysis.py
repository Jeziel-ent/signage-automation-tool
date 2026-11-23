"""Phase 3 of master spatial intelligence: what the master LOOKS like behind its objects, from a rendered page image.

Three different things are kept apart (never merged):
1. declared fill     - what CorelDRAW says an object's fill is (`master_analysis.read_fill`, stored on the model object);
2. visible colour    - what a rendered pixel region actually shows (this module): a dominant colour of a SAMPLE, not of a shape;
3. relationship      - an estimate that a text object sits on a coloured area (`method` + `confidence` say how it was reached).

The sample is the object's own box (the dominant colour of a text box is the surface behind the glyphs) and a ring just outside it. It
says what is visible there; it does not claim to be the colour of one whole background shape. Pillow + NumPy only (no OpenCV).

Image convention: row 0 is the TOP of the page; model coordinates are millimetres with the origin bottom-left, so y is flipped here.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image


class _LazyNumpy:
    """numpy is NOT in requirements-server.txt (the website image): this module is imported at server start-up through master_store, but the analysis itself
    runs on a Windows PC / the agent, where numpy exists. Import it on first use, never at import time."""

    def __getattr__(self, name):
        import numpy
        return getattr(numpy, name)


np = _LazyNumpy()

QUANT_BITS = 4              # colour quantisation for the dominant-colour mode: 16 levels per channel
RING_FRAC = 0.03            # ring width as a share of the page's short side (in pixels), at least 2 px
MIN_PIXELS = 12             # fewer pixels than this in a sample = no answer
FLAT_STD = 6.0              # luminance standard deviation below this = flat
TEXTURE_STD = 22.0          # ... above this (or high edge density) = textured / patterned
EMPTY_GRID = 48             # empty-space search grid (cells along the long side)
EMPTY_MIN_SHARE = 0.01      # an empty rectangle smaller than this share of the page is not reported


def _lum(rgb) -> float:
    r, g, b = [(c / 255.0) for c in rgb]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731 - WCAG relative luminance
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast_ratio(rgb_a, rgb_b) -> float:
    """WCAG contrast ratio of two sRGB colours (1.0 = identical, 21 = black on white)."""
    la, lb = _lum(rgb_a), _lum(rgb_b)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)


def _dominant(pixels: np.ndarray, top: int = 3) -> list[dict]:
    """The most common colours of an (N,3) uint8 array: quantised bins, the mean of each bin's own pixels, with its share."""
    if len(pixels) == 0:
        return []
    shift = 8 - QUANT_BITS
    q = (pixels >> shift).astype(np.int32)
    key = (q[:, 0] << (2 * QUANT_BITS)) | (q[:, 1] << QUANT_BITS) | q[:, 2]
    uniq, inv, counts = np.unique(key, return_inverse=True, return_counts=True)
    order = np.argsort(-counts)[:top]
    out = []
    for k in order:
        sel = pixels[inv == k]
        out.append({"rgb": [int(v) for v in sel.mean(axis=0).round()], "share": round(float(counts[k]) / len(pixels), 4)})
    return out


def _texture(gray: np.ndarray) -> dict:
    """flat / gradient / textured from the spread of luminance and the edge density of a sample (a heuristic, reported as such)."""
    if gray.size < MIN_PIXELS:
        return {"kind": "unknown"}
    std = float(gray.std())
    gx = np.abs(np.diff(gray.astype(np.float32), axis=1)) if gray.shape[1] > 1 else np.zeros(1)
    gy = np.abs(np.diff(gray.astype(np.float32), axis=0)) if gray.shape[0] > 1 else np.zeros(1)
    edge = float(((gx > 24).mean() + (gy > 24).mean()) / 2)
    if std < FLAT_STD and edge < 0.02:
        kind = "flat"
    elif edge >= 0.12 or std >= TEXTURE_STD and edge >= 0.05:
        kind = "textured"
    else:
        kind = "gradient" if std >= FLAT_STD else "flat"
    return {"kind": kind, "luminance_std": round(std, 2), "edge_density": round(edge, 4)}


def _px_box(box_mm: dict, pw: float, ph: float, W: int, H: int) -> tuple[int, int, int, int]:
    x0 = int(np.floor(box_mm["x"] / pw * W))
    x1 = int(np.ceil((box_mm["x"] + box_mm["width"]) / pw * W))
    y0 = int(np.floor((1 - (box_mm["y"] + box_mm["height"]) / ph) * H))
    y1 = int(np.ceil((1 - box_mm["y"] / ph) * H))
    return max(0, x0), max(0, y0), min(W, x1), min(H, y1)


def _ring(arr: np.ndarray, x0: int, y0: int, x1: int, y1: int, w: int) -> np.ndarray:
    H, W = arr.shape[:2]
    ox0, oy0, ox1, oy1 = max(0, x0 - w), max(0, y0 - w), min(W, x1 + w), min(H, y1 + w)
    mask = np.ones((oy1 - oy0, ox1 - ox0), bool)
    mask[y0 - oy0:y1 - oy0, x0 - ox0:x1 - ox0] = False
    return arr[oy0:oy1, ox0:ox1][mask]


def declared_rgb(obj: dict) -> list | None:
    """The text colour CorelDRAW declares (RGB if given, else a plain CMYK->RGB estimate - an estimate, not a colour-managed value)."""
    c = ((obj.get("appearance") or {}).get("fill") or {}).get("color") or {}
    if c.get("rgb"):
        return list(c["rgb"])
    if c.get("cmyk"):
        cc, m, y, k = [v / 100.0 for v in c["cmyk"]]
        return [round(255 * (1 - cc) * (1 - k)), round(255 * (1 - m) * (1 - k)), round(255 * (1 - y) * (1 - k))]
    return None


def analyze_object(arr: np.ndarray, obj: dict, pw: float, ph: float) -> dict | None:
    H, W = arr.shape[:2]
    x0, y0, x1, y1 = _px_box(obj["box_mm"], pw, ph, W, H)
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    inside = arr[y0:y1, x0:x1].reshape(-1, 3)
    ring_w = max(2, int(RING_FRAC * min(W, H)))
    ring = _ring(arr, x0, y0, x1, y1, ring_w)
    out: dict = {"declared": {"fill": ((obj.get("appearance") or {}).get("fill") or {}).get("type")}}
    dom_in = _dominant(inside) if len(inside) >= MIN_PIXELS else []
    dom_ring = _dominant(ring) if len(ring) >= MIN_PIXELS else []
    if dom_in:
        out["visible_inside"] = dom_in[0] | {"method": "dominant colour of the object's own box"}
    if dom_ring:
        out["visible_around"] = dom_ring[0] | {"method": "dominant colour of a ring outside the box"}
    # texture of the SURFACE: for text the box holds glyphs (a bimodal histogram is not a gradient), so the ring just outside is measured
    sample = ring if obj.get("type") == "text" else inside
    if len(sample) >= MIN_PIXELS and obj.get("type") != "text":
        out["texture"] = _texture(np.asarray(Image.fromarray(arr[y0:y1, x0:x1]).convert("L")))
    elif len(sample) >= MIN_PIXELS:
        out["texture"] = _texture(sample.mean(axis=1).reshape(-1, 1)) | {"measured_on": "ring around the text"}
    else:
        out["texture"] = {"kind": "unknown"}
    if obj.get("type") == "text" and dom_in:
        bg = dom_in[0]["rgb"]
        txt = declared_rgb(obj)
        source = "declared fill"
        if txt is None and len(dom_in) > 1:
            txt, source = dom_in[1]["rgb"], "second dominant colour in the box (estimate)"
        out["surface"] = {"rgb": bg, "share_of_box": dom_in[0]["share"], "method": "dominant colour of the text box",
                          "confidence": round(min(0.9, 0.35 + dom_in[0]["share"]), 2)}
        if txt is not None:
            out["text_contrast"] = {"ratio": contrast_ratio(txt, bg), "text_rgb": txt, "text_source": source}
        agree = bool(dom_ring) and bool(np.abs(np.array(dom_ring[0]["rgb"]) - np.array(bg)).max() < 40)
        out["surface"]["ring_agrees"] = agree          # the same colour just outside the box = the box sits on one coloured area
        if agree:
            out["surface"]["confidence"] = round(min(0.95, out["surface"]["confidence"] + 0.15), 2)
    return out


def empty_regions(arr: np.ndarray, boxes_mm: list[dict], pw: float, ph: float, background_rgb: list | None = None) -> list[dict]:
    """Large rectangles with no foreground object (by box) whose pixels are low-variance: room to place text. Largest first."""
    H, W = arr.shape[:2]
    n = EMPTY_GRID
    cols, rows = (n, max(1, round(n * H / W))) if W >= H else (max(1, round(n * W / H)), n)
    free = np.ones((rows, cols), bool)
    for b in boxes_mm:
        x0 = int(np.floor(b["x"] / pw * cols)); x1 = int(np.ceil((b["x"] + b["width"]) / pw * cols))
        y0 = int(np.floor((1 - (b["y"] + b["height"]) / ph) * rows)); y1 = int(np.ceil((1 - b["y"] / ph) * rows))
        free[max(0, y0):min(rows, y1), max(0, x0):min(cols, x1)] = False
    gray = np.asarray(Image.fromarray(arr).convert("L").resize((cols, rows)), dtype=np.float32)
    small = np.asarray(Image.fromarray(arr).resize((cols * 4, rows * 4)).convert("L"), dtype=np.float32)
    var = small.reshape(rows, 4, cols, 4).std(axis=(1, 3))
    free &= var < TEXTURE_STD
    # maximal rectangles by the histogram method
    best: list[tuple[int, int, int, int, int]] = []
    heights = np.zeros(cols, int)
    for r in range(rows):
        heights = np.where(free[r], heights + 1, 0)
        stack: list[tuple[int, int]] = []
        for c in range(cols + 1):
            h = heights[c] if c < cols else 0
            start = c
            while stack and stack[-1][1] >= h:
                s, sh = stack.pop()
                if sh > 0:
                    best.append(((c - s) * sh, s, r - sh + 1, c - s, sh))
                start = s
            stack.append((start, h))
    best.sort(reverse=True)
    out: list[dict] = []
    taken = np.zeros_like(free)
    for _area, c0, r0, cw, rh in best:
        if taken[r0:r0 + rh, c0:c0 + cw].any():
            continue
        share = cw * rh / (cols * rows)
        if share < EMPTY_MIN_SHARE:
            break
        taken[r0:r0 + rh, c0:c0 + cw] = True
        out.append({"x": round(c0 / cols, 4), "y": round(1 - (r0 + rh) / rows, 4), "width": round(cw / cols, 4), "height": round(rh / rows, 4),
                    "area_share": round(share, 4), "method": "object-free, low-variance grid cells"})
        if len(out) >= 6:
            break
    return out


def analyze_background(png_path: Path, model_dict: dict) -> dict:
    """Render-based analysis for a model (a `MasterModel.to_dict()`): per top-level object entries + page-level colours + empty regions."""
    pw, ph = float(model_dict["page"]["width_mm"]), float(model_dict["page"]["height_mm"])
    img = Image.open(png_path).convert("RGB")
    arr = np.asarray(img)
    H, W = arr.shape[:2]
    objects: dict[str, dict] = {}
    for o in model_dict["objects"]:
        if o.get("parent") is not None or not o.get("visible", True):
            continue
        r = analyze_object(arr, o, pw, ph)
        if r:
            objects[o["id"]] = r
    fg = [o["box_mm"] for o in model_dict["objects"] if o.get("parent") is None and o.get("visible", True) and o.get("role") != "bg"]
    page_dom = _dominant(arr.reshape(-1, 3)[:: max(1, (W * H) // 200000)], top=4)
    return {"method": "render", "image": {"width_px": W, "height_px": H}, "page_dominant": page_dom,
            "empty_regions": empty_regions(arr, fg, pw, ph), "objects": objects,
            "notes": ["visible colours are dominant colours of sampled regions, not whole-shape fills",
                      "text_contrast uses the declared text colour (CMYK->RGB is an estimate) when available"]}
