// "Download ZIP" (Shops queue): the request body and the notice shown after POST /api/export-zip.
import { fmtBytes } from "./fileSize.js";

/** The queue's converted shops as the API wants them: ids in table order, numbered by their S.no. */
export function zipRequest(printable) {
  return {
    shop_ids: printable.map((s) => s.id),
    numbers: Object.fromEntries(printable.map((s) => [s.id, s.no])),
  };
}

/** One line for the queue notice: what was packed, what is missing, which shops' edits are not in their files. */
export function zipSummaryText(summary) {
  const head = `Signage_Assets_Export.zip: ${summary.shops} shop${summary.shops === 1 ? "" : "s"}, ${summary.files} file${summary.files === 1 ? "" : "s"}${summary.bytes ? ` (${fmtBytes(summary.bytes)})` : ""}.`;
  const issues = zipIssuesText(summary);
  return issues ? `${head} ${issues}` : head;
}

/** Only what is missing and which shops' edits are not in their files ("" when nothing is). */
export function zipIssuesText(summary) {
  const parts = [];
  const missing = summary.missing || [];
  if (missing.length) {
    const shown = missing.slice(0, 4).join(", ");
    parts.push(`Not available: ${shown}${missing.length > 4 ? ` and ${missing.length - 4} more` : ""}.`);
  }
  const notes = summary.notes || [];
  if (notes.length) parts.push(`${notes[0]}${notes.length > 1 ? ` (+${notes.length - 1} more shop${notes.length > 2 ? "s" : ""})` : ""}.`);
  return parts.join(" ");
}
