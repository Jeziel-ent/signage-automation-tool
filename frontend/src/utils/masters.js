// Master templates: any number per orientation, from the server's registry (GET /api/masters - see
// context/MasterContext.jsx), unit-tested in masters.test.mjs. `masters` here is ONE brand's set, grouped as
// { landscape: [master, ...], portrait: [master, ...] }, oldest first - the first of each orientation is its default.
// A shop row converts from a master of ITS orientation: the one it picked (`master_id`) when that is still in the list
// for its orientation, else the orientation's default.

export const EMPTY_MASTERS = { landscape: [], portrait: [] };
export const ORIENTATIONS = ["landscape", "portrait"];

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

/** A flat list of masters (any brands) -> { landscape, portrait } for one brand, keeping the server's order. */
export function groupMasters(list, brand) {
  const mine = (list || []).filter((m) => brand == null || m.brand === brand);
  return {
    landscape: mine.filter((m) => m.orientation === "landscape"),
    portrait: mine.filter((m) => m.orientation === "portrait"),
  };
}

/** The masters of one orientation (oldest first). */
export function mastersOf(masters, orientation) {
  return masters?.[orientation] || [];
}

export const masterCount = (masters) => ORIENTATIONS.reduce((n, o) => n + mastersOf(masters, o).length, 0);

/** The dropdown text of a master: "L: Master 2 - Promotional" / "P: Master 1". */
export function masterLabel(m) {
  return `${m.orientation === "landscape" ? "L" : "P"}: ${m.name}`;
}

/** The id of the master a row converts from: its own pick when that master is one of its orientation's, else the
 *  orientation's default (first). null when the row has no size yet or its orientation has no master. */
export function rowMasterId(row, masters) {
  const o = boardOrientation(row);
  const list = o ? mastersOf(masters, o) : [];
  if (!list.length) return null;
  return list.some((m) => m.id === row?.master_id) ? row.master_id : list[0].id;
}

/** The master ids sent with a row (POST shops / convert): `master_id` = the row's master (above), plus each orientation's
 *  default - the server falls back to those when the pick is unusable, and to the OTHER orientation's only when the row's
 *  orientation has no master at all. */
export function rowMasterIds(row, masters) {
  const first = (o) => mastersOf(masters, o)[0]?.id || null;
  return { master_id: rowMasterId(row, masters), landscape_master_id: first("landscape"), portrait_master_id: first("portrait") };
}

/** The master the row's user actually PICKED (its `master_id` when that is one of its orientation's masters), else null = "Auto". */
export function pickedMasterId(row, masters) {
  const o = boardOrientation(row);
  const list = o ? mastersOf(masters, o) : [];
  return list.some((m) => m.id === row?.master_id) ? row.master_id : null;
}

/** The ids sent to the server: like rowMasterIds, but `master_id` is only the master the user picked. Left empty ("Auto") the server
 *  chooses - the master the nearest designer board was made from, else the one whose shape fits the board, else the orientation's
 *  default (= what rowMasterIds sends) - so a brand with several design styles gets the right one per board. */
export function rowMasterIdsAuto(row, masters) {
  return { ...rowMasterIds(row, masters), master_id: pickedMasterId(row, masters) };
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
  const names = hit.slice(0, 4).map((r) => `• ${r.name || "Shop"} (${r.width} × ${r.height} ${r.unit || "in"})`).join("\n");
  return `No ${o} master is uploaded, so ${hit.length === 1 ? "this board" : `${hit.length} boards`} will be made from the ${used} ` +
    `master and the layout will not fit (stretched background, repeated logos):\n${names}${hit.length > 4 ? "\n…" : ""}\n\n` +
    `Upload a ${o === "portrait" ? "Portrait" : "Landscape"} Master first for a proper layout. Convert anyway?`;
}

/** The job new shops are saved on: the first landscape master, else the first portrait one. */
export function primaryMaster(masters) {
  return mastersOf(masters, "landscape")[0] || mastersOf(masters, "portrait")[0] || null;
}

const normMaster = (t) => String(t ?? "").toLowerCase().replace(/\.cdr$/i, "").replace(/[^a-z0-9஀-௿]+/g, " ").trim();

/** The id of the uploaded master a sheet cell names ("Master 7", "L: Master 7 - Promotional", "6 X 3", "6 X 3.cdr", "master7"), or
 *  null when none matches. Matches the master's name, its dropdown label or its file name, whole text first, then a prefix. */
export function resolveMasterId(text, masters) {
  const want = normMaster(text).replace(/^[lp] /, "");
  if (!want) return null;
  const all = ORIENTATIONS.flatMap((o) => mastersOf(masters, o));
  const keys = (m) => [normMaster(m.name), normMaster(masterLabel(m)), normMaster(m.file_name)].filter(Boolean);
  const squash = (t) => t.replaceAll(" ", "");
  const exact = all.find((m) => keys(m).some((k) => k === want || k.replace(/^[lp] /, "") === want || squash(k) === squash(want)));
  if (exact) return exact.id;
  const prefix = all.find((m) => keys(m).some((k) => k.replace(/^[lp] /, "").startsWith(want + " ") || want.startsWith(k.replace(/^[lp] /, "") + " ")));
  return prefix ? prefix.id : null;
}
