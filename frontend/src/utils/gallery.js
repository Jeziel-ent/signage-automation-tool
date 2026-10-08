// The preview gallery's image list (unit-tested in gallery.test.mjs).

const RASTER = ["png", "jpeg", "jpg"];

/**
 * The ONE image that shows a converted shop as it is now: the PNG (else JPG) of its newest finished editor export - it carries the
 * designer's edits - or, when the board was never exported, the conversion's own preview. (It used to list every export's PNG and
 * JPG plus the unedited conversion, so the original and duplicates sat next to the edited board.) `status` = GET /api/v2/shops/{id}/status (its `files.preview`), `exports` = GET
 * /api/editor/{job}/{shop}/exports. Each URL ends in `?v=<v>` (a cache-buster fixed when the gallery opens), so an image
 * re-exported under the same file name is fetched again instead of shown from the browser cache.
 */
export function galleryImages(shop, status, exports, v) {
  const out = [];
  const bust = (url) => `${url}${url.includes("?") ? "&" : "?"}v=${v}`;
  const done = (exports || []).filter((e) => e.status === "done" && e.files)
    .sort((a, b) => (b.completed_at || b.created_at || 0) - (a.completed_at || a.created_at || 0));
  for (const e of done) {
    const kind = RASTER.find((k) => e.files[k]);
    if (!kind) continue;
    return [{
      key: `${e.export_id}-${kind}`,
      url: bust(`/api/editor/${shop.job_id}/${shop.id}/exports/${encodeURIComponent(e.export_id)}/files/${encodeURIComponent(e.files[kind])}`),
      label: "Edited version",
      detail: when(e.completed_at || e.created_at),
    }];
  }
  const preview = status?.files?.preview;
  if (preview) {
    out.push({
      key: "conversion",
      url: bust(`/api/v2/shops/${shop.id}/files/${encodeURIComponent(preview)}`),
      label: "As converted (no edits yet)",
      detail: when(status.completed_at),
    });
  }
  return out;
}

function when(t) {
  if (!t) return "";
  const d = new Date(t * 1000);
  return d.toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

/** The queue gallery's cards: converted shops whose English or Tamil name, or board type, contains `query` (case-insensitive;
 *  blank = all), in queue order. */
export function galleryItems(shops, query) {
  const q = String(query ?? "").trim().toLowerCase();
  if (!q) return shops;
  return shops.filter((s) => [s.name, s.shop_name_local, s.board_type].some((t) => String(t ?? "").toLowerCase().includes(q)));
}
