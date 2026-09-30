import { normalizeBoardType } from "./shopPayload.js";

// Excel / CSV shop import. Client sheets come in any layout, so this reads ONLY Shop Name, Width and Height and finds
// them by fuzzy header matching, falling back to looking at the cell values. Pure functions (unit-tested in
// shopImport.test.mjs) plus one SheetJS wrapper, loaded on demand so the ~500 KB library is not in the main bundle.

const NAME_RE = /shop|store|name|client|outlet|particulars|dealer|vendor/i;
// Which name header wins when several match: "Shop Name" beats "Contact Name" beats a bare "Name".
const NAME_RANK = [/shop|store/i, /client|outlet|dealer|vendor|particulars/i, /name/i];
const SIZE_HEADER_RE = /size|dimension|board|measurement|recce|hoarding|flex/i;
// "Width", "Actual Width", "Width (ft)", "Width in inches", "W", "Wd", "Wdt", "Breadth"
const WIDTH_RE = /width|breadth|^w$|^wd\.?$|^wdt\.?$|w\s*\(/i;
// "Height", "Actual Height", "Height (in)", "H", "Ht", "Hgt", "Length"
const HEIGHT_RE = /height|length|^h$|^ht\.?$|^hgt\.?$|h\s*\(/i;
// never a shop-name column, even if the word 'shop'/'store'/'name' appears in it ("Store Size", "Contact Name")
// the LOCAL-script shop name ("Shop Name (Local)", "Local Name", "Tamil Name", "Shop Name (Tamil)") - read into
// shop_name_local, never picked as the English name column
const LOCAL_NAME_RE = /local|tamil|regional|vernacular|native/i;
// "Type", "Board Type", "Type of Board", "Sign Type", "Lit / Nonlit", "Lighting", "Illumination"
const TYPE_RE = /\btype\b|lighting|illumination|^lit\b|non\s*-?\s*lit/i;
const NAME_EXCLUDE_RE = new RegExp(
  [String.raw`phone|mobile|contact|gst|address|e-?mail|sl\.?\s*no|s\.?\s*no|^#$`, SIZE_HEADER_RE.source, WIDTH_RE.source, HEIGHT_RE.source,
    TYPE_RE.source].join("|"),
  "i");

// a column that holds just the unit of each row's size ("Unit": ft / Feet / in / Inches ...)
const UNIT_COL_RE = /\bunits?\b|\buom\b|\bu\.o\.m\b/i;

const UNIT_TOKEN ="ft\\.?|feet|foot|'|’|\"|inches|inch|in\\.?|cm|mm";
// "120 * 48", "10 x 4", "30X40", "10ft x 4ft", "12' * 4'", "10 by 4", "10.5 × 4": width, optional unit, separator,
// height, optional unit. (The plain `(\d)\s*[*xX×-]\s*(\d)` form cannot see a unit between the number and the
// separator, so "10ft x 4ft" and "12' * 4'" would not match it.)
// A number may carry thousands separators ("3,000 x 1,200 mm"); a bare comma between two numbers is a separator only
// when the cell is nothing but the size ("10, 4" / "10,4 ft"), so an address like "No 5, 3rd Cross" never reads as a size.
const NUM = String.raw`(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)`;
const SIZE_RE = new RegExp(
  `${NUM}\\s*(${UNIT_TOKEN})?\\s*(?:[*xX×✕\\-]|\\bby\\b)\\s*${NUM}\\s*(${UNIT_TOKEN})?`, "i");
const SIZE_COMMA_RE = new RegExp(`^\\s*${NUM}\\s*(${UNIT_TOKEN})?\\s*,\\s*${NUM}\\s*(${UNIT_TOKEN})?\\s*$`, "i");
const toNum = (s) => Number(String(s).replace(/,/g, ""));
const matchSize = (s) => SIZE_RE.exec(s) || SIZE_COMMA_RE.exec(s);

const cellText = (v) => String(v ?? "").trim();

/** "ft" | "in" | "cm" | "mm" | null from a unit word/symbol found anywhere in `s`. */
export function detectUnit(s) {
  const t = cellText(s).toLowerCase();
  if (!t) return null;
  if (/(?<![a-z])(ft|feet|foot)(?![a-z])|['’]/.test(t)) return "ft";
  if (/(?<![a-z])(cm|centimet\w*)(?![a-z])/.test(t)) return "cm";
  if (/(?<![a-z])(mm|millimet\w*)(?![a-z])/.test(t)) return "mm";
  if (/(?<![a-z])(in|inch|inches)(?![a-z])|"/.test(t)) return "in";
  return null;
}

/** {width, height, width_unit, height_unit} from a combined size cell, or null. A unit on either side applies to
 *  both when only one is given ("10 x 4 ft"); no unit anywhere -> defaultUnit. */
export function parseSize(cell, defaultUnit = "in") { // defaultUnit null -> width_unit/height_unit stay null when the cell has none
  const m = matchSize(cellText(cell));
  if (!m) return null;
  const w = toNum(m[1]);
  const h = toNum(m[3]);
  if (!(w > 0) || !(h > 0)) return null;
  const uL = detectUnit(m[2]);
  const uR = detectUnit(m[4]);
  return { width: w, height: h, width_unit: uL || uR || defaultUnit, height_unit: uR || uL || defaultUnit };
}

/** A number (with an optional unit) from a separate Width/Height cell: "10", "10 ft", "12'", "3,000". */
export function parseDimension(cell, defaultUnit = null) {
  const t = cellText(cell);
  const m = /^(\d[\d,]*(?:\.\d+)?)/.exec(t.replace(/^\s+/, ""));
  if (!m) return null;
  const n = Number(m[1].replace(/,/g, ""));
  return n > 0 ? { value: n, unit: detectUnit(t.slice(m[1].length)) || defaultUnit } : null; // defaultUnit may be null
}

const TO_INCH = { in: 1, ft: 12, cm: 1 / 2.54, mm: 1 / 25.4 };

/** One shared unit ("in" | "ft") for a board. No unit anywhere -> `defaultUnit` (the page's Default Unit, "in" if not
 *  given); a unit on one side applies to both; ft (or in) on both -> that unit; anything else (ft next to in, cm, mm) is
 *  converted to inches so the numbers stay true. */
export function resolveUnit(width, wUnit, height, hUnit, defaultUnit = "in") {
  const wu = wUnit || hUnit || defaultUnit;
  const hu = hUnit || wUnit || defaultUnit;
  if (wu === hu && (wu === "in" || wu === "ft")) return { width, height, unit: wu };
  const inch = (v, u) => Math.round(v * TO_INCH[u] * 100) / 100;
  return { width: inch(width, wu), height: inch(height, hu), unit: "in" };
}

const looksLikeSize = (v) => !!matchSize(cellText(v));
const hasLetters = (v) => /[a-z஀-௿]/i.test(cellText(v));
const isPhoneLike = (v) => /^[+\d][\d\s\-()]{7,}$/.test(cellText(v));

const HEADER_SCAN_ROWS = 20; // titles, logos, blank rows and a project line can sit above the real header

/** Index of the header row: the best of the top rows containing >= 1 recognisable header, else -1 (headerless). */
function findHeaderRow(rows) {
  let best = -1;
  let bestScore = 0;
  for (let i = 0; i < Math.min(HEADER_SCAN_ROWS, rows.length); i++) {
    const cells = rows[i].map(cellText);
    if (cells.filter(Boolean).length < 2) continue;
    // a data row ("Shop A", "10*4") must not be mistaken for a header just because a shop is called "... Store"
    if (cells.some(looksLikeSize)) continue;
    const score = cells.filter((c) => c && (NAME_RE.test(c) || SIZE_HEADER_RE.test(c) || WIDTH_RE.test(c) || HEIGHT_RE.test(c))).length;
    if (score > bestScore) {
      best = i;
      bestScore = score;
    }
  }
  return best;
}

function pickNameColumn(headers, taken) {
  const cands = headers.map((h, i) => [h, i])
    .filter(([h, i]) => h && !taken.has(i) && NAME_RE.test(h) && !NAME_EXCLUDE_RE.test(h) && !LOCAL_NAME_RE.test(h));
  for (const rank of NAME_RANK) {
    const hit = cands.find(([h]) => rank.test(h));
    if (hit) return hit[1];
  }
  return -1;
}

function pickLocalNameColumn(headers, taken) {
  return headers.findIndex((h, i) => h && !taken.has(i) && LOCAL_NAME_RE.test(h) && (NAME_RE.test(h) || /tamil/i.test(h))
    && !NAME_EXCLUDE_RE.test(h));
}

// A designer file name used as the shop name: "73 - 60 X 75 Inch - Nonlit - SRI AMBIRAMI PROVISON STORES.cdr"
// -> "SRI AMBIRAMI PROVISON STORES" (everything after the third " - ", like backend batch_import.parse_shop_lines).
const DESIGN_FILE_RE = /^\s*\d+\s*-\s*\d+(?:\.\d+)?\s*[xX×*]\s*\d+(?:\.\d+)?\s*[a-z'"]*\s*-\s*([^-]+?)\s*-\s*(.+?)\s*$/i;

/** The shop name to print from a name cell: a trailing ".cdr" is dropped and a designer file name is reduced to its
 *  shop-name part; anything else is returned trimmed. */
export function cleanShopName(v) {
  let t = cellText(v).replace(/\.cdr$/i, "").trim();
  const m = DESIGN_FILE_RE.exec(t);
  if (m) t = m[2].replace(/\s+-\s+copy$/i, "").trim();
  return t;
}

/** The board type written in a designer file name used as the shop name ("73 - 60 X 75 Inch - Nonlit - SHOP" -> "Nonlit"),
 *  else "". */
export function boardTypeFromName(v) {
  const m = DESIGN_FILE_RE.exec(cellText(v).replace(/\.cdr$/i, "").trim());
  return m ? normalizeBoardType(m[1]) : "";
}

/**
 * rows: array of arrays (SheetJS `sheet_to_json(sheet, {header: 1, defval: ""})`). Returns
 *   { shops: [{name, shop_name_local?, width, height, unit, row}], errors: [{row, reason}], missing: [...], layout }
 * `row` is the 1-based spreadsheet row. `missing` is ["name"] and/or ["size"] when a column cannot be found even by
 * looking at the values - nothing is imported then. `layout` describes what was detected (for the tests/messages).
 */
export function mapSheetRows(rows, { defaultUnit = "in" } = {}) {
  if (defaultUnit !== "in" && defaultUnit !== "ft") defaultUnit = "in";
  rows = rows.map((r) => (Array.isArray(r) ? r : []));
  const headerRow = findHeaderRow(rows);
  let headers = headerRow >= 0 ? rows[headerRow].map(cellText) : [];
  let dataStart = headerRow + 1;
  // Two-row header: a merged "Size" cell over "Width" | "Height" sub-headers. The sub-header row is folded into the
  // header ("Size" + "Width") so the Width/Height columns are found and the sub-header is not read as a shop.
  const sub = headerRow >= 0 && rows[dataStart] ? rows[dataStart].map(cellText) : [];
  if (sub.some((c) => WIDTH_RE.test(c)) && sub.some((c) => HEIGHT_RE.test(c)) && !sub.some(looksLikeSize)
    && !headers.some((c) => WIDTH_RE.test(c) && !SIZE_HEADER_RE.test(c))) {
    const n = Math.max(headers.length, sub.length);
    headers = Array.from({ length: n }, (_, i) => [headers[i] || "", sub[i] || ""].filter(Boolean).join(" "));
    dataStart += 1;
  }
  const data = rows.slice(dataStart).map((cells, k) => ({ cells, row: dataStart + k + 1 }))
    .filter(({ cells }) => cells.some((c) => cellText(c) !== ""));
  const width_ = Math.max(0, ...rows.map((r) => r.length));
  const col = (c) => data.map(({ cells }) => cellText(cells[c])).filter(Boolean);
  const share = (vals, pred) => (vals.length ? vals.filter(pred).length / vals.length : 0);

  // ---- dimensions: a combined size column first, then separate Width / Height columns
  const taken = new Set();
  let sizeCol = -1;
  let widthCol = -1;
  let heightCol = -1;
  const nameByHeader = pickNameColumn(headers, taken);
  headers.forEach((h, i) => {
    if (sizeCol < 0 && i !== nameByHeader && SIZE_HEADER_RE.test(h) && share(col(i), looksLikeSize) >= 0.5) sizeCol = i;
  });
  if (sizeCol < 0) {
    headers.forEach((h, i) => {
      if (i === nameByHeader) return;
      if (widthCol < 0 && WIDTH_RE.test(h)) widthCol = i;
      else if (heightCol < 0 && HEIGHT_RE.test(h) && i !== widthCol) heightCol = i;
    });
    if (widthCol < 0 || heightCol < 0) {
      widthCol = heightCol = -1;
      // no usable headers: the column whose values look like "10*4"
      for (let c = 0; c < width_; c++) {
        if (c !== nameByHeader && share(col(c), looksLikeSize) >= 0.6) {
          sizeCol = c;
          break;
        }
      }
    }
  }
  if (sizeCol >= 0) taken.add(sizeCol);
  if (widthCol >= 0) taken.add(widthCol), taken.add(heightCol);

  // ---- name: by header, else the first mostly-text column that is not a size/phone column
  let nameCol = nameByHeader >= 0 && !taken.has(nameByHeader) ? nameByHeader : -1;
  if (nameCol < 0) {
    for (let c = 0; c < width_; c++) {
      const vals = col(c);
      if (taken.has(c) || !vals.length) continue;
      if (share(vals, (v) => hasLetters(v) && !looksLikeSize(v) && !isPhoneLike(v)) >= 0.6) {
        nameCol = c;
        break;
      }
    }
  }

  if (nameCol >= 0) taken.add(nameCol);
  const localCol = pickLocalNameColumn(headers, taken);
  if (localCol >= 0) taken.add(localCol);
  // ---- "Type of board" column (Nonlit / Frontlit / Backlit ...), optional
  const typeCol = headers.findIndex((h, i) => h && !taken.has(i) && TYPE_RE.test(h));
  if (typeCol >= 0) taken.add(typeCol);
  // ---- a separate Unit column ("Unit" / "UOM" header whose values are mostly units: ft, Feet, in, Inches, ')
  const unitCol = headers.findIndex((h, i) => h && !taken.has(i) && UNIT_COL_RE.test(h) && share(col(i), (v) => !!detectUnit(v)) >= 0.5);

  const missing = [];
  if (nameCol < 0) missing.push("name");
  if (sizeCol < 0 && widthCol < 0) missing.push("size");
  const layout = { headerRow: headerRow >= 0 ? headerRow + 1 : null, nameCol, localCol, typeCol, unitCol, sizeCol, widthCol, heightCol };
  const shops = [];
  const errors = [];
  if (missing.length) return { shops, errors, missing, layout };

  const hdr = (c) => (c >= 0 ? headers[c] || "" : "");
  const headerUnit = detectUnit(hdr(sizeCol)) || null;
  data.forEach(({ cells, row }) => {
    const name = cleanShopName(cells[nameCol]);
    const local = localCol >= 0 ? cellText(cells[localCol]) : "";
    if (!name) return errors.push({ row, reason: "missing shop name" });
    // unit precedence: written in the size cell itself > this row's Unit column > a header hint ("Size (ft)") > the default
    const rowUnit = unitCol >= 0 ? detectUnit(cells[unitCol]) : null;
    let dims = null;
    let detected = false;
    if (sizeCol >= 0) {
      const p = parseSize(cells[sizeCol], rowUnit || headerUnit);
      if (!p) return errors.push({ row, reason: `could not read a size like "10*4" from "${cellText(cells[sizeCol])}"` });
      detected = !!(p.width_unit || p.height_unit);
      dims = resolveUnit(p.width, p.width_unit, p.height, p.height_unit, defaultUnit);
    } else {
      const w = parseDimension(cells[widthCol], rowUnit || detectUnit(hdr(widthCol)));
      const h = parseDimension(cells[heightCol], rowUnit || detectUnit(hdr(heightCol)));
      if (!w) return errors.push({ row, reason: `width must be a number > 0 (got "${cellText(cells[widthCol])}")` });
      if (!h) return errors.push({ row, reason: `height must be a number > 0 (got "${cellText(cells[heightCol])}")` });
      detected = !!(w.unit || h.unit);
      dims = resolveUnit(w.value, w.unit, h.value, h.unit, defaultUnit);
    }
    // unitSource: "excel" when the file said which unit, "default" when it fell back to the page's Default Unit (such rows
    // follow later changes of the Default Unit; "excel" rows never do)
    const boardType = (typeCol >= 0 ? normalizeBoardType(cells[typeCol]) : "") || boardTypeFromName(cells[nameCol]);
    shops.push({ name, ...(local ? { shop_name_local: local } : {}), ...(boardType ? { board_type: boardType } : {}), ...dims, unitSource: detected ? "excel" : "default", row });
  });
  return { shops, errors, missing, layout };
}

/** Read an .xlsx / .xls / .csv File and map it: the first sheet that yields shops (a workbook often opens on a cover or
 *  summary sheet), else the first sheet's result so its "missing" message is shown. */
export async function parseShopFile(file, opts = {}) {
  const XLSX = await import("xlsx");
  const wb = XLSX.read(await file.arrayBuffer(), { type: "array" });
  let first = null;
  for (const name of wb.SheetNames) {
    // raw:false -> formatted text ("10*4" stays text, 12 stays "12"); header:1 -> rows as arrays, so a title above the
    // header row, blank or duplicate header names cannot break the mapping
    const rows = XLSX.utils.sheet_to_json(wb.Sheets[name], { header: 1, defval: "", raw: false, blankrows: true });
    const res = { ...mapSheetRows(rows, opts), sheet: name };
    if (!res.missing.length && res.shops.length) return res;
    first ??= res;
  }
  return first || { shops: [], errors: [], missing: ["name", "size"], layout: {} };
}

/** Every sheet of a workbook: { sheets: [{ name, shops, errors, missing, layout }] } in workbook order. Each shop carries
 *  `sheet_name`, so the Shops Queue can show one tab per sheet. Sheets with no shops (a cover page) are kept in the list
 *  with their `missing` reason, and the caller reports them. */
export async function parseShopWorkbook(file, opts = {}) {
  const XLSX = await import("xlsx");
  const wb = XLSX.read(await file.arrayBuffer(), { type: "array" });
  const multi = wb.SheetNames.length > 1;
  const sheets = wb.SheetNames.map((name) => {
    const rows = XLSX.utils.sheet_to_json(wb.Sheets[name], { header: 1, defval: "", raw: false, blankrows: true });
    const res = mapSheetRows(rows, opts);
    // a CSV (or a one-sheet workbook) has no meaningful sheet name to show as a tab
    return { name, ...res, shops: res.shops.map((s) => (multi ? { ...s, sheet_name: name } : s)) };
  });
  return { sheets };
}
