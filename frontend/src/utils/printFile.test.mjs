import test from "node:test";
import assert from "node:assert/strict";
import { buildSpec, parseFileName, sectionTotals, validate } from "./printFile.js";

test("parseFileName reads designer-style names", () => {
  assert.deepEqual(parseFileName("16 - 12 X 4 Feet - Nonlit - AL MADEENA - Copy.cdr"),
    { no: "16", width: "12", height: "4", unit: "ft", type: "Nonlit", name: "AL MADEENA - Copy" });
  assert.equal(parseFileName("10.5x4in - Shop.jpg").unit, "in");
});

test("parseFileName keeps the plain name when nothing matches", () => {
  const p = parseFileName("random photo.png");
  assert.equal(p.name, "random photo");
  assert.equal(p.width, "");
});

test("sectionTotals multiplies by qty", () => {
  const t = sectionTotals([{ width: 10, height: 4, unit: "ft", qty: 2 }, { width: 12, height: 12, unit: "in", qty: "" }]);
  assert.equal(t.qty, 3);
  assert.ok(Math.abs(t.sqft - 81) < 1e-6);
});

test("validate asks for sizes", () => {
  assert.match(validate([]), /at least one/);
  assert.match(validate([{ name: "A", width: "", height: "4" }]), /width and height/);
  assert.equal(validate([{ width: "1", height: "1" }]), "");
});

test("buildSpec indexes files, drops empty sections and nulls blank overrides", () => {
  const f = { name: "a.png" };
  const { spec, files } = buildSpec({
    title: "T", projectNo: "P", date: "2026-10-06", lines: ["MDU", " "], format: "pdf",
    sections: [{ id: "s1", name: "ACP", qty: "", sqft: "" }, { id: "s2", name: "EMPTY", qty: "5", sqft: "" }],
    items: [{ id: "i1", sectionId: "s1", file: f, name: "A", width: "10", height: "4", unit: "ft", type: "", no: "1", qty: "" },
            { id: "i2", sectionId: "s1", file: null, name: "B", width: "1", height: "1", unit: "in", type: "", no: "", qty: 3 }],
  });
  assert.deepEqual(files, [f]);
  assert.equal(spec.sections.length, 1);
  assert.deepEqual(spec.lines, ["MDU"]);
  assert.equal(spec.sections[0].qty, null);
  assert.deepEqual(spec.sections[0].items.map((i) => i.type), ["ACP", "ACP"]);   // blank type -> the section name
  assert.deepEqual(spec.sections[0].items.map((i) => [i.file, i.qty]), [[0, 1], [null, 3]]);
});

import { assignSections } from "./printFile.js";

test("assignSections puts a file in the section named like its type, or makes one", () => {
  let n = 0;
  const secs = [{ id: "a", name: "ACP BOARD", qty: "", sqft: "" }];
  const r = assignSections(secs, ["acp-board", "Glow Sign", "GLOWSIGN", ""], () => `n${++n}`, "a");
  assert.deepEqual(r.sectionIds, ["a", "n1", "n1", "a"]);
  assert.deepEqual(r.sections.map((s) => s.name), ["ACP BOARD", "GLOW SIGN"]);
  assert.equal(secs.length, 1);                              // input not mutated
});

test("assignSections reuses an empty unnamed section", () => {
  const r = assignSections([{ id: "a", name: "", qty: "", sqft: "" }], ["Nonlit"], () => "x", "a");
  assert.deepEqual(r.sectionIds, ["a"]);
  assert.equal(r.sections[0].name, "NONLIT");
});

import { figures, planAdd, progressPct } from "./printFile.js";

test("planAdd skips unusable files and files the rest by type", () => {
  const secs = [{ id: "a", name: "ACP BOARD", qty: "", sqft: "" }];
  const files = [{ name: "1 - 10 X 4 Feet - GSB - A.png" }, { name: "notes.txt" }, { name: "B.cdr" }];
  const r = planAdd(files, secs, () => "g");
  assert.equal(r.skipped, 1);
  assert.deepEqual(r.sectionIds, ["g", "a"]);                 // GSB -> a new section, no type -> the last existing one
  assert.equal(r.parsed[0].width, "10");
});

test("figures prefers the typed override", () => {
  assert.deepEqual(figures({ qty: "", sqft: "" }, { qty: 3, sqft: 9 }), { qty: 3, sqft: 9 });
  assert.deepEqual(figures({ qty: "5", sqft: "x" }, { qty: 3, sqft: 9 }), { qty: 5, sqft: 0 });
});

test("progressPct follows the stages", () => {
  assert.equal(progressPct(false, "uploading", 50), 0);
  assert.equal(progressPct(true, "uploading", 50), 20);
  assert.equal(progressPct(true, "uploading", 0), 4);
  assert.equal(progressPct(true, "rendering", 100), 40);
  assert.equal(progressPct(true, "downloading", 100), 90);
});
