import test from "node:test";
import assert from "node:assert/strict";
import { importOutcome, missingText } from "./importOutcome.js";
import { batchFinishedText } from "./batchStats.js";
import { apiDetail, createShopFromDraft, fetchShopStatuses } from "./shopApi.js";

const masters = { landscape: [], portrait: [] };
const sheet = (name, shops, missing = [], errors = []) => ({ name, shops, missing, errors });

test("missingText names the lacking columns", () => {
  assert.match(missingText(["size"]), /Size column/);
  assert.equal(missingText(["name"]), "Could not find the shop name.");
  assert.match(missingText(["name", "size"]), /shop name\.$|or the size/);
});

test("importOutcome: nothing usable gives a note and no rows", () => {
  const a = importOutcome([sheet("S1", [], ["size"])], "f.xlsx", "in", masters);
  assert.equal(a.rows.length, 0);
  assert.equal(a.report.added, 0);
  assert.match(a.report.note, /Size column/);
  assert.equal(importOutcome([sheet("S1", [])], "f.xlsx", "in", masters).report.note, "No data rows found.");
});

test("importOutcome: rows, skip flag, errors sorted, notes", () => {
  const shops = [{ name: "A", width: 1, height: 2, unit: "in", unitSource: "default", convert: false },
                 { name: "B", width: 1, height: 2, unit: "in", master: "nope" }];
  const out = importOutcome([sheet("S1", shops, [], [{ row: 5, reason: "x" }, { row: 2, reason: "y" }]), sheet("S2", [], ["size"])], "f.xlsx", "in", masters);
  assert.equal(out.rows.length, 2);
  assert.equal(out.rows[0].skip_convert, true);
  assert.equal(out.report.defaulted, 1);
  assert.deepEqual(out.report.errors.map((e) => e.row), [2, 5]);
  assert.deepEqual(out.report.sheets, ["S1 (2)"]);
  assert.match(out.report.note, /No shop list found on sheet: S2/);
  assert.match(out.report.note, /1 row named a master that is not uploaded/);
  assert.match(out.report.note, /1 row\(s\) have Convert = No/);
});

test("batchFinishedText", () => {
  assert.equal(batchFinishedText(3, 0), "Batch finished: 3 converted.");
  assert.equal(batchFinishedText(3, 2), "Batch finished: 3 converted, 2 failed.");
});

test("shopApi helpers", async () => {
  assert.equal(await apiDetail({ json: async () => ({ detail: "bad" }) }, "x"), "bad");
  assert.equal(await apiDetail({ json: async () => { throw new Error("no"); } }, "x"), "x");
  const real = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ a: { status: "done" } }) });
    assert.deepEqual(await fetchShopStatuses(["a"]), { a: { status: "done" } });
    globalThis.fetch = async () => ({ ok: false });
    assert.equal(await fetchShopStatuses(["a"]), null);
    globalThis.fetch = async () => { throw new Error("offline"); };
    assert.equal(await fetchShopStatuses(["a"]), null);
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ id: "s1" }) });
    assert.deepEqual(await createShopFromDraft("j", { name: "N" }, {}), { saved: { id: "s1" } });
    globalThis.fetch = async () => ({ ok: false, json: async () => ({ detail: "no room" }) });
    assert.deepEqual(await createShopFromDraft("j", { name: "N" }, {}), { error: "N: no room" });
  } finally {
    globalThis.fetch = real;
  }
});
