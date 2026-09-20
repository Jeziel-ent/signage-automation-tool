// Raster size preview for the export popup - a mirror of export_replay.resolve_raster
// (the server re-checks every limit; this only lets the popup show the size and block early).
export const MAX_SIDE_PX = 20000;
export const MAX_MEGAPIXELS = 200;
export const MIN_DPI = 1;
export const MAX_DPI = 1200;

/** {w_px, h_px, dpi, megapixels, error} - `error` is a user-facing string when a limit is exceeded. */
export function resolveRaster(pageWmm, pageHmm, raster) {
  const longestIn = Math.max(pageWmm, pageHmm) / 25.4;
  let dpi;
  if (raster.mode === "max_px") {
    if (!(raster.max_px >= 16)) return { error: "Longest side must be at least 16 px" };
    dpi = raster.max_px / longestIn;
  } else {
    dpi = Number(raster.dpi);
    if (!(dpi > 0)) return { error: "Enter a resolution in dpi" };
  }
  dpi = Math.max(MIN_DPI, Math.min(MAX_DPI, dpi));
  const w_px = Math.max(1, Math.round((pageWmm / 25.4) * dpi));
  const h_px = Math.max(1, Math.round((pageHmm / 25.4) * dpi));
  const megapixels = (w_px * h_px) / 1e6;
  const out = { w_px, h_px, dpi: Math.round(dpi * 100) / 100, megapixels: Math.round(megapixels * 100) / 100 };
  if (Math.max(w_px, h_px) > MAX_SIDE_PX) out.error = `${w_px} × ${h_px} px is too large (limit ${MAX_SIDE_PX} px on the longest side) - lower the resolution`;
  else if (megapixels > MAX_MEGAPIXELS) out.error = `${Math.round(megapixels)} megapixels is too large (limit ${MAX_MEGAPIXELS}) - lower the resolution`;
  return out;
}

/** Edited text objects whose font is not installed here (CorelDRAW would substitute it). */
export function missingFonts(scene, fonts) {
  if (!fonts || !fonts.available) return [];
  const known = new Set(fonts.fonts.map((f) => f.toLowerCase()));
  const seen = new Map();
  const walk = (children) => {
    for (const n of children) {
      if (n.stale && n.text && n.text.font && !known.has(n.text.font.toLowerCase())) seen.set(n.text.font, (seen.get(n.text.font) || 0) + 1);
      if (n.children) walk(n.children);
    }
  };
  scene.layers.forEach((l) => walk(l.children));
  return [...seen.entries()].map(([font, count]) => ({ font, count }));
}

export const STEP_LABELS = {
  launch: "Starting CorelDRAW",
  open: "Opening the converted file",
  replay: "Applying your edits",
  verify: "Checking the result against your edits",
  cdr: "Saving the CDR",
  pdf: "Publishing the PDF",
  png: "Exporting the PNG",
  jpeg: "Exporting the JPEG",
};

export const fmtBytes = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);
