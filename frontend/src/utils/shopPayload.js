// The body sent for a Shops-table row (PATCH on blur, and with every convert): only name, width, height and the ONE
// shared unit, as currently on screen - including inline edits made after an Excel import. Numbers are sent as
// numbers; a still-empty numeric field stays "" so the server rejects it with a readable 400 instead of converting a
// stale value.
import { toTamil } from "./tamilTranslit.js";

export function shopPayload(x) {
  return {
    name: x.name,
    // sent whenever the row has the field, so clearing the Tamil name in the table clears it on the server too
    ...(x.shop_name_local || "shop_name_local" in x ? { shop_name_local: x.shop_name_local || "" } : {}),
    ...(x.board_type ? { board_type: x.board_type } : {}),
    // its Excel sheet. No font fields: conversions keep the master's own fonts (fonts are changed in the Signage Editor)
    ...(x.sheet_name ? { sheet_name: x.sheet_name } : {}),
    width: x.width === "" || x.width == null ? "" : +x.width,
    height: x.height === "" || x.height == null ? "" : +x.height,
    unit: x.unit || "in",
  };
}

let draftCounter = 0;
export const isDraft = (shop) => typeof shop?.id === "string" && shop.id.startsWith("draft-");

/** A local, not-yet-saved table row for one parsed spreadsheet shop (see Automation.jsx: nothing is sent to the
 *  server until the row is converted). */
export function toDraftRow(parsed) {
  draftCounter += 1;
  return {
    id: `draft-${Date.now().toString(36)}-${draftCounter}`,
    name: parsed.name,
    ...(parsed.shop_name_local ? { shop_name_local: parsed.shop_name_local } : {}),
    ...(parsed.board_type ? { board_type: parsed.board_type } : {}),
    ...(parsed.sheet_name ? { sheet_name: parsed.sheet_name } : {}),
    // true while the Tamil name is the automatic transliteration (it follows edits of the English name until typed over)
    ...(parsed.ta_auto ? { ta_auto: true } : {}),
    width: parsed.width,
    height: parsed.height,
    unit: parsed.unit || "in",
    // where the unit came from: "excel" (the file said), "default" (the page's Default Unit - follows its changes) or
    // "manual" (picked by hand); absent on rows loaded from the server, which the Default Unit never touches
    ...(parsed.unitSource ? { unitSource: parsed.unitSource } : {}),
    status: "new",
    progress_pct: 0,
  };
}

/**
 * The Shops queue after a master is uploaded, replaced or removed: every shop that was saved to the server (converted, converting, queued
 * or failed - all tied to the OLD master's job) becomes a fresh draft with the same name, size and unit, so converting it again creates a
 * new shop on the new master. Untouched drafts stay as they are. The old server shops are not deleted: their boards stay under Recently
 * generated. Returns { shops, reset } - `reset` = how many rows were turned back into drafts.
 */
export function resetForNewMaster(shops) {
  let reset = 0;
  const out = shops.map((x) => {
    if (isDraft(x) && (x.status === "new" || !x.status)) return x;
    reset += 1;
    return toDraftRow({ name: x.name, shop_name_local: x.shop_name_local, board_type: x.board_type, sheet_name: x.sheet_name,
      ta_auto: x.ta_auto, width: x.width, height: x.height, unit: x.unit || x.width_unit || "in", unitSource: x.unitSource });
  });
  return { shops: out, reset };
}

/** Rows the Default Unit may change: they took the default (unitSource "default") and are still editable - not yet
 *  converted or in the queue. Units read from the Excel file, picked by hand, or on rows loaded from the server are never
 *  touched. */
export const followsDefaultUnit = (x) => x.unitSource === "default" && (x.status === "new" || x.status === "failed" || !x.status);

/** The Shops queue after the Default Unit changes to `unit`: every row that follows it takes the new unit (numbers are
 *  kept - the default says what the typed numbers MEAN). Returns { shops, changed } - `changed` = ids whose unit changed. */
export function applyDefaultUnit(shops, unit) {
  const changed = [];
  const out = shops.map((x) => {
    if (!followsDefaultUnit(x) || x.unit === unit) return x;
    changed.push(x.id);
    return { ...x, unit };
  });
  return { shops: out, changed };
}
/** "Type of board" choices in the Shops table (the designers' own file names use these). A value from an import that is
 *  not in the list is kept and offered as an extra choice. */
export const BOARD_TYPES = ["Nonlit", "Frontlit", "Backlit", "Lit", "OneWayVision", "GSB", "ACP", "Vinyl"];
export const DEFAULT_BOARD_TYPE = "Nonlit";

/** A type spelt any way ("non lit", "NONLIT", "one way vision") -> the list's spelling; unknown -> trimmed as given. */
export function normalizeBoardType(v) {
  const t = String(v ?? "").trim();
  if (!t) return "";
  const key = t.toLowerCase().replace(/[\s_-]+/g, "");
  const alias = { nonlight: "Nonlit", nonlite: "Nonlit", frontlight: "Frontlit", backlight: "Backlit", owv: "OneWayVision", glowsignboard: "GSB" };
  return BOARD_TYPES.find((b) => b.toLowerCase() === key) || alias[key] || t;
}

/** "Download All CDRs": the GET that streams one ZIP of the CDRs of `shops` ({id, no} - converted rows). */
export function cdrDownloadUrl(shops) {
  const ids = shops.map((x) => x.id).join(",");
  const nos = shops.map((x) => x.no).join(",");
  return `/api/v2/download-cdrs?ids=${encodeURIComponent(ids)}&nos=${encodeURIComponent(nos)}`;
}

/** An imported / new row gets a Tamil name transliterated from its English one when the sheet gave none (`ta_auto`). */
export function withAutoTamil(parsed) {
  if (parsed.shop_name_local || !parsed.name) return parsed;
  const ta = toTamil(parsed.name);
  return ta ? { ...parsed, shop_name_local: ta, ta_auto: true } : parsed;
}

/** The patch to apply when a row's names are edited: typing the Tamil name makes it the designer's own (no longer
 *  automatic); typing the English name re-transliterates the Tamil one while it is still automatic (or empty and never
 *  typed over). */
export function nameEditPatch(row, patch, { deferTamil = false } = {}) {
  if ("shop_name_local" in patch) return { ...patch, ta_auto: false };
  if ("name" in patch && followsEnglish(row)) {
    // deferTamil: the caller fills the Tamil name itself once typing pauses (withFreshAutoTamil)
    return deferTamil ? { ...patch, ta_auto: true } : { ...patch, shop_name_local: toTamil(patch.name), ta_auto: true };
  }
  return patch;
}

/** The Tamil name still follows the English one: it is automatic, or empty and never typed over. */
export const followsEnglish = (row) => !!row && (row.ta_auto === true || (!row.shop_name_local && row.ta_auto !== false));

/** `row` with its automatic Tamil name brought up to date with the English name (unchanged when the Tamil is the user's). */
export function withFreshAutoTamil(row) {
  if (!row || !followsEnglish(row) || !row.name) return row;
  const ta = toTamil(row.name);
  return ta === row.shop_name_local && row.ta_auto ? row : { ...row, shop_name_local: ta, ta_auto: true };
}
