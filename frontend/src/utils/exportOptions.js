// Settings model of the row-level "Export Signage Files" modal, and how it maps onto the export API's options
// (backend/app/export_replay.normalize_options). Only options CorelDRAW actually honours are offered.
import { resolveRaster } from "../editor/exportMath.js";

export const EXPORT_FORMATS = [
  { key: "pdf", label: "PDF", hint: "Print-ready document" },
  { key: "jpeg", label: "JPG", hint: "Flat image" },
  { key: "cdr", label: "CDR", hint: "CorelDRAW native file" },
  { key: "png", label: "PNG", hint: "Image, optional transparency" },
];

export const PDF_DPI_CHOICES = [150, 300, 600];
export const DPI_CHOICES = [72, 150, 300];
export const PADDING_CHOICES = [0, 10, 25, 50]; // mm on every side
// cdrFileVersion values: 21 = CorelDRAW 2019 (what the designers run), 0 = whatever the server's CorelDRAW writes (27), 17 = X7
export const CDR_VERSIONS = [
  [21, "CorelDRAW 2019 (v21) - Recommended"],
  [0, "CorelDRAW 2025 (v27) - Native"],
  [17, "CorelDRAW X7 (v17)"],
];

/** Pixel size of a raster export at `dpi`, with `paddingMm` added on every side (mirrors export_replay.raster_sizes). */
export function rasterPreview(pageWmm, pageHmm, dpi, paddingMm = 0) {
  return resolveRaster(pageWmm + 2 * paddingMm, pageHmm + 2 * paddingMm, { mode: "dpi", dpi });
}

/** Default image dpi: the largest choice up to `cap` whose image stays within the size limits (else the smallest choice). */
export function bestDpi(pageWmm, pageHmm, choices = DPI_CHOICES, cap = 150) {
  const fits = choices.filter((d) => d <= cap && !rasterPreview(pageWmm, pageHmm, d).error);
  return fits.length ? Math.max(...fits) : Math.min(...choices);
}

export function defaultSettings(pageWmm, pageHmm) {
  return {
    pdf: { color_mode: "cmyk", bitmap_dpi: 300, crop_marks: false, bleed: false, curves: false },
    jpeg: { color: "rgb", dpi: bestDpi(pageWmm, pageHmm) },
    cdr: { version: 21, text: "editable" },
    png: { transparent: true, dpi: bestDpi(pageWmm, pageHmm), padding_mm: 0 },
  };
}

/** The `options` body of POST /api/editor/{job}/{shop}/export. */
export function buildExportOptions(s) {
  return {
    pdf: {
      color_mode: s.pdf.color_mode,
      bitmap_dpi: s.pdf.bitmap_dpi,
      text: s.pdf.curves ? "curves" : "embed",
      crop_marks: s.pdf.crop_marks,
      bleed: s.pdf.bleed,
    },
    cdr: { version: s.cdr.version, text: s.cdr.text },
    raster: { antialias: true },
    png: { mode: "dpi", dpi: s.png.dpi, png_background: s.png.transparent ? "transparent" : "white", padding_mm: s.png.padding_mm },
    jpeg: { mode: "dpi", dpi: s.jpeg.dpi, color: s.jpeg.color },
  };
}
