// The preview gallery's image list (unit-tested in gallery.test.mjs).

const RASTER = ["png", "jpeg", "jpg"];

/**
 * Every rendered image of one converted shop, newest first: the PNG/JPEG of each finished editor export, then the
 * conversion's own preview. `status` = GET /api/v2/shops/{id}/status (its `files.preview`), `exports` = GET
 * /api/editor/{job}/{shop}/exports. Each URL ends in `?v=<v>` (a cache-buster fixed when the gallery opens), so an image
 * re-exported under the same file name is fetched again instead of shown from the browser cache.
 */
export function galleryImages(shop, status, exports, v) {
  const out = [];
  const bust = (url) => `${url}${url.includes("?") ? "&" : "?"}v=${v}`;
  const done = (exports || []).filter((e) => e.status === "done" && e.files)
    .sort((a, b) => (b.completed_at || b.created_at || 0) - (a.completed_at || a.created_at || 0));
  for (const e of done) {
    for (const kind of RASTER) {
      const name = e.files[kind];
      if (!name) continue;
      out.push({
        key: `${e.export_id}-${kind}`,
        url: bust(`/api/editor/${shop.job_id}/${shop.id}/exports/${encodeURIComponent(e.export_id)}/files/${encodeURIComponent(name)}`),
        label: `Editor export · ${kind === "png" ? "PNG" : "JPG"}`,
        detail: when(e.completed_at || e.created_at),
      });
    }
  }
  const preview = status?.files?.preview;
  if (preview) {
    out.push({
      key: "conversion",
      url: bust(`/api/v2/shops/${shop.id}/files/${encodeURIComponent(preview)}`),
      label: "Conversion preview",
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
