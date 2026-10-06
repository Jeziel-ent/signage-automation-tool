// "Create Print File" page helpers: prefill from designer file names, section totals (mirror backend/app/print_file.py), spec building.
import { sqFeet } from "./printSheet.js";

const UNITS = { in: "in", inch: "in", inches: "in", ft: "ft", feet: "ft", foot: "ft", cm: "cm", mm: "mm" };
const SIZE_RE = /^(\d+(?:\.\d+)?)\s*[xX*]\s*(\d+(?:\.\d+)?)\s*([A-Za-z]+)?$/;

/** `16 - 12 X 4 Feet - Nonlit - AL MADEENA.cdr` -> {no, width, height, unit, type, name}; whatever cannot be read stays empty. */
export function parseFileName(fileName) {
  const base = String(fileName || "").replace(/\.[^.]+$/, "").trim();
  const parts = base.split(/\s+-\s+/);
  const out = { no: "", width: "", height: "", unit: "in", type: "", name: base };
  const at = parts.findIndex((p) => SIZE_RE.test(p.trim()));
  if (at < 0) return out;
  const m = SIZE_RE.exec(parts[at].trim());
  out.width = m[1];
  out.height = m[2];
  out.unit = UNITS[(m[3] || "").toLowerCase()] || "in";
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
