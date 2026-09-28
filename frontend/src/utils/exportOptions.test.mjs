import { test } from "node:test";
import assert from "node:assert/strict";
import { bestDpi, buildExportOptions, defaultSettings, rasterPreview } from "./exportOptions.js";

const IN = 25.4;

test("default image dpi is the largest choice up to 150 that stays within the size limits", () => {
  assert.equal(bestDpi(30 * IN, 40 * IN), 150); // 30x40 in: 150 dpi = 4500x6000 px
  assert.equal(bestDpi(240 * IN, 36 * IN), 72); // 240 in at 150 dpi = 36000 px, over the 20000 px limit
  assert.equal(bestDpi(400 * IN, 10 * IN), 72); // nothing fits: the smallest choice (the modal shows the error)
});

test("PNG padding widens the image on every side", () => {
  const plain = rasterPreview(1000, 500, 72);
  const padded = rasterPreview(1000, 500, 72, 25);
  assert.equal(padded.w_px, Math.round((1050 / IN) * 72));
  assert.equal(padded.h_px, Math.round((550 / IN) * 72));
  assert.ok(padded.w_px > plain.w_px);
  assert.match(rasterPreview(240 * IN, 36 * IN, 300).error, /too large/);
});

test("settings map onto the export API's options", () => {
  const s = defaultSettings(30 * IN, 40 * IN);
  s.pdf = { color_mode: "rgb", bitmap_dpi: 600, crop_marks: true, bleed: true, curves: true };
  s.jpeg = { color: "cmyk", dpi: 72 };
  s.cdr = { version: 17, text: "curves" };
  s.png = { transparent: false, dpi: 300, padding_mm: 10 };
  assert.deepEqual(buildExportOptions(s), {
    pdf: { color_mode: "rgb", bitmap_dpi: 600, text: "curves", crop_marks: true, bleed: true },
    cdr: { version: 17, text: "curves" },
    raster: { antialias: true },
    png: { mode: "dpi", dpi: 300, png_background: "white", padding_mm: 10 },
    jpeg: { mode: "dpi", dpi: 72, color: "cmyk" },
  });
  const d = buildExportOptions(defaultSettings(30 * IN, 40 * IN));
  assert.equal(d.pdf.color_mode, "cmyk"); // print-ready by default
  assert.equal(d.cdr.version, 21); // CorelDRAW 2019, what the designers run
  assert.equal(d.png.png_background, "transparent");
});
