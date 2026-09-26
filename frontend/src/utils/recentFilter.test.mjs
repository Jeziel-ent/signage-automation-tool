import test from "node:test";
import assert from "node:assert/strict";
import { matchesSearch } from "./recentFilter.js";

const row = { name: "Sri Kumar Stores", master_filename: "dalmia_master.cdr", files: { cdr: "Sri_Kumar_Stores.cdr", preview: "Sri_Kumar_Stores.png" } };

test("blank search matches everything", () => {
  assert.ok(matchesSearch(row, ""));
  assert.ok(matchesSearch(row, "   "));
  assert.ok(matchesSearch(row, undefined));
});
test("matches shop name, master filename and generated filenames, case-insensitively", () => {
  assert.ok(matchesSearch(row, "kumar"));
  assert.ok(matchesSearch(row, "DALMIA_MASTER"));
  assert.ok(matchesSearch(row, "stores.png"));
  assert.ok(!matchesSearch(row, "nippon"));
});
test("rows without files (not converted yet) still match by name", () => {
  assert.ok(matchesSearch({ name: "Devi", master_filename: "m.cdr", files: null }, "dev"));
  assert.ok(!matchesSearch({ name: "Devi", master_filename: "m.cdr", files: null }, "png"));
});
