// The Shops Queue row's quick Download popup (components/ShopDownloadModal.jsx).

export const SINGLE_FORMATS = ["cdr", "jpg", "png", "pdf"];

/** GET that downloads one converted shop's file; `shop.no` (its S.no in the table) numbers the standard file name. */
export function downloadHref(shop, fmt) {
  const no = String(shop.no ?? "").trim();            // numeric ("76") or the sheet's own text serial ("SL-01")
  return `/api/v2/shops/${encodeURIComponent(shop.id)}/download/${fmt}${no && no !== "0" ? "?no=" + encodeURIComponent(no) : ""}`;
}

/** "Download All": the GET that streams one ZIP of every listed converted shop's file in one format (`shops` = [{id, no}],
 *  `no` = the row's S.No). */
export function allDownloadUrl(shops, fmt) {
  const ids = shops.map((x) => x.id).join(",");
  const nos = shops.map((x) => x.no).join(",");
  return `/api/v2/download-all?format=${encodeURIComponent(fmt)}&ids=${encodeURIComponent(ids)}&nos=${encodeURIComponent(nos)}`;
}
