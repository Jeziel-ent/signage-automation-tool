// What an Excel / CSV import turns into: the draft rows to add and the report the Shops Queue shows. Pure (no React, no network), so
// the Automation page only has to apply the result.
import { resolveMasterId } from "./masters.js";
import { withAutoTamil } from "./shopPayload.js";

const MISSING_TEXT = { size: "size (a Size column like 10*4, or Width and Height columns)", name: "shop name" };

/** "Could not find the size ... or the shop name." for the columns a sheet lacks. */
export function missingText(missing) {
  return `Could not find the ${missing.map((k) => MISSING_TEXT[k] || MISSING_TEXT.name).join(" or the ")}.`;
}

const plural = (n, one, many) => (n === 1 ? one : many);

function emptyNote(sheets) {
  const first = sheets.find((sh) => sh.missing.length) || sheets[0];
  return first?.missing.length ? missingText(first.missing) : "No data rows found.";
}

// A row without a Tamil name gets one transliterated from its English name (editable; marked "auto" until typed over); the sheet's
// Master column picks an uploaded master of this brand; Convert = no makes Convert All skip the row.
function parseRows(withShops, masters) {
  let masterMiss = 0;
  const rows = withShops.flatMap((sh) => sh.shops).map(withAutoTamil).map((x) => {
    const { master, convert, ...rest } = x;
    const id = master ? resolveMasterId(master, masters) : null;
    if (master && !id) masterMiss += 1;
    return { ...rest, ...(id ? { master_id: id } : {}), ...(convert === false ? { skip_convert: true } : {}) };
  });
  return { rows, masterMiss };
}

function importErrors(withShops, multi) {
  return withShops
    .flatMap((sh) => sh.errors.map((e) => ({ ...e, sheet: multi ? sh.name : undefined })))
    .sort((a, b) => (a.sheet || "").localeCompare(b.sheet || "") || (a.row ?? 0) - (b.row ?? 0));
}

function importNote(skippedSheets, masterMiss, skipCount) {
  const sheetNote = skippedSheets.length ? `No shop list found on sheet${plural(skippedSheets.length, "", "s")}: ${skippedSheets.join(", ")}` : "";
  const masterNote = masterMiss ? `${masterMiss} row${plural(masterMiss, "", "s")} named a master that is not uploaded for this brand - the default master is used.` : "";
  const skipNote = skipCount ? `${skipCount} row(s) have Convert = No: Convert All skips them.` : "";
  return [sheetNote, masterNote, skipNote].filter(Boolean).join(" ");
}

/**
 * `sheets` = parseShopWorkbook's answer. Returns {rows, report}: `rows` are the parsed shops to turn into drafts (empty when no sheet had
 * a usable shop list) and `report` is what the page shows ({file, added, errors, note, ...}).
 */
export function importOutcome(sheets, fileName, defaultUnit, masters) {
  const withShops = sheets.filter((sh) => !sh.missing.length && sh.shops.length);
  if (!withShops.length) return { rows: [], report: { file: fileName, added: 0, errors: [], note: emptyNote(sheets) } };
  const multi = sheets.length > 1;
  const { rows, masterMiss } = parseRows(withShops, masters);
  const skippedSheets = multi ? sheets.filter((sh) => !withShops.includes(sh)).map((sh) => sh.name) : [];
  const report = {
    file: fileName, added: rows.length, errors: importErrors(withShops, multi),
    defaulted: rows.filter((x) => x.unitSource === "default").length,
    translated: rows.filter((x) => x.ta_auto).length,
    unit: defaultUnit,
    sheets: multi ? withShops.map((sh) => `${sh.name} (${sh.shops.length})`) : [],
    note: importNote(skippedSheets, masterMiss, rows.filter((x) => x.skip_convert).length),
  };
  return { rows, report };
}
