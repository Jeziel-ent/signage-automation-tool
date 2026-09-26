// The body sent for a Shops-table row (PATCH on blur, and with every convert): only name, width, height and the ONE
// shared unit, as currently on screen - including inline edits made after an Excel import. Numbers are sent as
// numbers; a still-empty numeric field stays "" so the server rejects it with a readable 400 instead of converting a
// stale value.
export function shopPayload(x) {
  return {
    name: x.name,
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
    width: parsed.width,
    height: parsed.height,
    unit: parsed.unit || "in",
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
    return toDraftRow({ name: x.name, width: x.width, height: x.height, unit: x.unit || x.width_unit || "in" });
  });
  return { shops: out, reset };
}
