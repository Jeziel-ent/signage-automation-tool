// "Create Print File" helpers - the QTY / Sq.feet shown in the dialog mirror backend/app/print_sheet.py (the server
// recomputes them for the sheet itself).
const MM = { in: 25.4, ft: 304.8, cm: 10, mm: 1 };
const MM_PER_FOOT = 304.8;

export const BOARD_TYPES = ["ACP BOARD", "ACP-LIT", "NONLIT", "GLOWSIGN", "FLEX", "LED SIGNAGE"];

/** Square feet of one board; each dimension may have its own unit. */
export function sqFeet(shop) {
  const w = Number(shop.width) * (MM[shop.width_unit || shop.unit] ?? 25.4);
  const h = Number(shop.height) * (MM[shop.height_unit || shop.unit] ?? 25.4);
  return (w / MM_PER_FOOT) * (h / MM_PER_FOOT);
}

export function printTotals(shops) {
  return { qty: shops.length, sqft: shops.reduce((a, s) => a + sqFeet(s), 0) };
}

/** Whole square feet like the reference sheet (185.25 -> "185"); one decimal below 10. */
export function fmtSqft(total) {
  if (total >= 10) return String(Math.round(total));
  const r = Math.round(total * 10) / 10;
  return Number.isInteger(r) ? String(r) : r.toFixed(1);
}

/** Today in the LOCAL time zone as YYYY-MM-DD (the date input's format) - not toISOString(), which is UTC. */
export function todayISO(d = new Date()) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** The download name the server sends, from a Content-Disposition header (fallback when it is missing). */
export function filenameFrom(disposition, fallback) {
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition || "");
  return m ? decodeURIComponent(m[1]) : fallback;
}
