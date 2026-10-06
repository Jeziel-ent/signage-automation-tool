// "Create Print File" page helpers: prefill from designer file names, section totals (mirror backend/app/print_file.py), spec building.
import { sqFeet } from "./printSheet.js";

const UNITS = { in: "in", inch: "in", inches: "in", ft: "ft", feet: "ft", foot: "ft", cm: "cm", mm: "mm" };
const NUMBER = /^\d{1,6}(?:\.\d{1,6})?$/;

/** "12 X 4 Feet" / "10.5x4in" -> {w, h, unit} (strings), or null. Split by hand: a one-regex version backtracks badly. */
function parseSize(piece) {
  const t = piece.trim();
  const at = t.search(/[xX*]/);
  if (at < 1) return null;
  const rest = t.slice(at + 1).trim();
  let k = 0;
  while (k < rest.length && /[\d.]/.test(rest[k])) k++;
  const w = t.slice(0, at).trim();
  const h = rest.slice(0, k);
  const unit = rest.slice(k).trim();
  const unitOk = unit === "" || (unit.length <= 10 && /^[A-Za-z]+$/.test(unit));
  return NUMBER.test(w) && NUMBER.test(h) && unitOk ? { w, h, unit } : null;
}

/** `16 - 12 X 4 Feet - Nonlit - AL MADEENA.cdr` -> {no, width, height, unit, type, name}; whatever cannot be read stays empty. */
export function parseFileName(fileName) {
  const base = String(fileName || "").replace(/\.[^.]+$/, "").trim();
  const parts = base.split(" - ").map((p) => p.trim());
  const out = { no: "", width: "", height: "", unit: "in", type: "", name: base };
  const at = parts.findIndex((p) => parseSize(p));
  if (at < 0) return out;
  const size = parseSize(parts[at]);
  out.width = size.w;
  out.height = size.h;
  out.unit = UNITS[size.unit.toLowerCase()] || "in";
  if (at >= 1 && /^\d+$/.test(parts[0].trim())) out.no = parts[0].trim();
  const rest = parts.slice(at + 1);
  if (rest.length >= 2) { out.type = rest[0].trim(); out.name = rest.slice(1).join(" - ").trim(); }
  else if (rest.length === 1) out.name = rest[0].trim();
  return out;
}

const num = (v) => (Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : 0);
const qtyOf = (i) => Math.max(1, Math.floor(num(i.qty)) || 1);

/** {qty, sqft} of a section's items (each item's qty multiplies both). */
export function sectionTotals(items) {
  return {
    qty: items.reduce((a, i) => a + qtyOf(i), 0),
    sqft: items.reduce((a, i) => a + qtyOf(i) * (num(i.width) && num(i.height) ? sqFeet({ width: i.width, height: i.height, unit: i.unit }) : 0), 0),
  };
}

/** The first problem that would make the server refuse the sheet, or "". */
export function validate(items) {
  if (!items.length) return "Add at least one file.";
  const bad = items.find((i) => !num(i.width) || !num(i.height));
  return bad ? `Enter a width and height for "${bad.name || bad.fileName}".` : "";
}

/** The JSON `spec` for POST /api/print-file/generate and the ordered file list its `file` indexes point into. */
export function buildSpec({ title, projectNo, date, lines, format, sections, items }) {
  const files = [];
  const index = new Map();
  const fileIndex = (it) => {
    if (!it.file) return null;
    if (!index.has(it.id)) { index.set(it.id, files.length); files.push(it.file); }
    return index.get(it.id);
  };
  const spec = {
    title, project_no: projectNo, date, format, lines: lines.filter((l) => l.trim()),
    sections: sections
      .map((s) => ({
        name: s.name, qty: s.qty === "" ? null : s.qty, sqft: s.sqft === "" ? null : s.sqft,
        items: items.filter((i) => i.sectionId === s.id).map((i) => ({
          file: fileIndex(i), name: i.name, width: Number(i.width), height: Number(i.height), unit: i.unit,
          type: i.type || s.name, no: i.no, qty: qtyOf(i),   // the caption's board type: the file name's, else its section's
        })),
      }))
      .filter((s) => s.items.length),
  };
  return { spec, files };
}

const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "");

/**
 * Where each newly added file goes: the section named like its board type (spaces, case and dashes ignored), else a new section
 * for that type (an empty untouched section is renamed instead of leaving it behind), else - no type in the name - `fallbackId`.
 * `types` are the parsed types in file order; `newId` makes ids for new sections. Returns {sections, sectionIds}.
 */
export function assignSections(sections, types, newId, fallbackId) {
  const out = sections.map((s) => ({ ...s }));
  const used = new Set();                                  // sections that hold, or are about to hold, a file
  return {
    sectionIds: types.map((raw) => {
      const type = String(raw || "").trim();
      if (!type) { used.add(fallbackId); return fallbackId; }
      let hit = out.find((s) => norm(s.name) === norm(type));
      if (!hit) {
        hit = out.find((s) => !s.name.trim() && !used.has(s.id));
        if (hit) hit.name = type.toUpperCase();
        else { hit = { id: newId(), name: type.toUpperCase(), qty: "", sqft: "" }; out.push(hit); }
      }
      used.add(hit.id);
      return hit.id;
    }),
    sections: out,
  };
}

const ACCEPTED = /\.(cdr|jpe?g|png|webp|bmp|tiff?)$/i;

/** Which of the dropped files can be used, parsed from their names and placed in sections (see assignSections). */
export function planAdd(fileList, sections, newId) {
  const all = [...fileList];
  const parsed = all.filter((f) => ACCEPTED.test(f.name)).map((file) => ({ file, ...parseFileName(file.name) }));
  const placed = assignSections(sections, parsed.map((p) => p.type), newId, sections.at(-1).id);
  return { skipped: all.length - parsed.length, parsed, sections: placed.sections, sectionIds: placed.sectionIds };
}

/** A section's QTY and Sq.feet as printed: the typed override, else the calculated figure (`totals` = sectionTotals). */
export function figures(section, totals) {
  return {
    qty: section.qty === "" ? totals.qty : Number(section.qty) || 0,
    sqft: section.sqft === "" ? totals.sqft : Number(section.sqft) || 0,
  };
}

/** Width of the progress bar (0-100): the upload fills the first 40 %, the render holds 40, the download is the rest. */
export function progressPct(busy, stage, uploadPct) {
  if (!busy) return 0;
  if (stage === "uploading") return Math.max(4, uploadPct * 0.4);
  return stage === "rendering" ? 40 : 90;
}

/** Hands a blob to the browser as a download. */
export function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
