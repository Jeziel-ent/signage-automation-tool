// Up to four master templates per brand: Landscape 1 / 2 and Portrait 1 / 2 (unit-tested in masters.test.mjs). Each is its own
// upload (a `jobs` row with that orientation). A shop row converts from a master of ITS orientation; when two exist for that
// orientation the row picks one (`master_slot` 1 or 2), otherwise the one that exists is used.

export const MASTER_SLOTS = ["landscape_1", "landscape_2", "portrait_1", "portrait_2"];
export const EMPTY_MASTERS = { landscape_1: null, landscape_2: null, portrait_1: null, portrait_2: null };

// The same rule as the server's routing (orientation_adapter.LANDSCAPE_MASTER_MIN_RATIO): only boards at least 1.25 times as
// wide as tall use a landscape master; square and near-square boards (5x5, 6x6 ft, 60x75 in) use a portrait one.
export const LANDSCAPE_MIN_RATIO = 1.25;

/** "landscape" | "portrait" | null (no usable size yet) for a row's width / height (one shared unit per row). */
export function boardOrientation(row) {
  const w = +row?.width;
  const h = +row?.height;
  if (!(w > 0) || !(h > 0)) return null;
  return w / h >= LANDSCAPE_MIN_RATIO - 1e-9 ? "landscape" : "portrait";
}

/** The uploaded masters of one orientation, as [{slot: 1|2, job}] (slot 1 first). */
export function mastersOf(masters, orientation) {
  return [1, 2].map((n) => ({ slot: n, job: masters?.[`${orientation}_${n}`] || null })).filter((m) => m.job);
}

/** Which master number (1 | 2) a row uses for its orientation: its own choice when that master exists, else the one that
 *  exists (a row that chose 2 falls back to 1 once Master 2 is removed, and vice versa). */
export function rowMasterSlot(row, masters) {
  const o = boardOrientation(row);
  if (!o) return 1;
  const have = mastersOf(masters, o).map((m) => m.slot);
  const chosen = +row?.master_slot || 1;
  return have.includes(chosen) ? chosen : have[0] || 1;
}

/** The master ids sent with a row (POST shops / convert): the chosen master for the row's own orientation, and Master 1 (else
 *  2) of the other orientation - the server falls back to that one only when the row's orientation has no master at all. */
export function rowMasterIds(row, masters) {
  const o = boardOrientation(row);
  const pick = (orientation) => {
    const list = mastersOf(masters, orientation);
    if (!list.length) return null;
    if (orientation === o) {
      const slot = rowMasterSlot(row, masters);
      return (list.find((m) => m.slot === slot) || list[0]).job.id;
    }
    return list[0].job.id;
  };
  return { landscape_master_id: pick("landscape"), portrait_master_id: pick("portrait") };
}

/** The OTHER orientation a row will be converted from because its own orientation has no master ("landscape" |
 *  "portrait"), or null when a master of its own orientation exists (or there is none at all). A 16x3 ft landscape design
 *  forced into a 3x6 ft portrait board does not fit - the caller warns before converting. */
export function masterFallback(row, masters) {
  const o = boardOrientation(row);
  if (!o || mastersOf(masters, o).length) return null;
  const other = o === "landscape" ? "portrait" : "landscape";
  return mastersOf(masters, other).length ? other : null;
}

/** The confirmation text for converting rows from the other orientation's master (null when none would be). */
export function fallbackWarning(rows, masters) {
  const hit = rows.filter((r) => masterFallback(r, masters));
  if (!hit.length) return null;
  const o = boardOrientation(hit[0]);
  const used = masterFallback(hit[0], masters);
  const names = hit.slice(0, 4).map((r) => `\u2022 ${r.name || "Shop"} (${r.width} \u00d7 ${r.height} ${r.unit || "in"})`).join("\n");
  return `No ${o} master is uploaded, so ${hit.length === 1 ? "this board" : `${hit.length} boards`} will be made from the ${used} ` +
    `master and the layout will not fit (stretched background, repeated logos):\n${names}${hit.length > 4 ? "\n\u2026" : ""}\n\n` +
    `Upload a ${o === "portrait" ? "Portrait" : "Landscape"} Master first for a proper layout. Convert anyway?`;
}

/** The job new shops are saved on: the first master that exists. */
export function primaryMaster(masters) {
  for (const s of MASTER_SLOTS) if (masters?.[s]) return masters[s];
  return null;
}
