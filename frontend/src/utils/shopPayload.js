// The body sent for a Shops-table row (PATCH on blur, and with every convert): only name, width, height and the ONE
// shared unit, as currently on screen - including inline edits made after an Excel import. Numbers are sent as
// numbers; a still-empty numeric field stays "" so the server rejects it with a readable 400 instead of converting a
// stale value.
export function shopPayload(x) {
  return {
    name: x.name,
    // sent whenever the row has the field, so clearing the Tamil name in the table clears it on the server too
    ...(x.shop_name_local || "shop_name_local" in x ? { shop_name_local: x.shop_name_local || "" } : {}),
    ...(x.board_type ? { board_type: x.board_type } : {}),
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
    return toDraftRow({ name: x.name, shop_name_local: x.shop_name_local, board_type: x.board_type, width: x.width, height: x.height, unit: x.unit || x.width_unit || "in", unitSource: x.unitSource });
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
