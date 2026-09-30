// The Shops Queue row's quick Download popup (components/ShopDownloadModal.jsx).

export const SINGLE_FORMATS = ["cdr", "jpg", "png", "pdf"];

/** GET that downloads one converted shop's file; `shop.no` (its S.no in the table) numbers the standard file name. */
export function downloadHref(shop, fmt) {
  const no = Number(shop.no);
  return `/api/v2/shops/${encodeURIComponent(shop.id)}/download/${fmt}${no > 0 ? `?no=${no}` : ""}`;
}
