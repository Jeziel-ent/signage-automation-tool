import test from "node:test";
import assert from "node:assert/strict";
import { mapSheetRows } from "./shopImport.js";
import { resetForNewMaster, shopPayload } from "./shopPayload.js";

test("an imported row edited from 10*4 to 12*4 sends the edited values", () => {
  const { shops } = mapSheetRows([["Shop Name", "Size"], ["Sri Kumar", "10*4"]]);
  // the row as the table holds it after the batch endpoint echoes it back (snake_case, strings from inputs)
  let row = { id: "abc", ...shops[0] };
  assert.deepEqual([row.width, row.height], [10, 4]);
  row = { ...row, width: "12" };                                   // the user retypes the width input: "10" -> "12"
  const body = shopPayload(row);
  assert.deepEqual(body, { name: "Sri Kumar", width: 12, height: 4, unit: "in" });
  assert.equal(typeof body.width, "number");
});

test("only name, width, height and unit are sent; an emptied number stays empty; unit defaults to in", () => {
  const body = shopPayload({ name: "A", width: "", height: 4, unit: "ft", phone: "98", gst: "G", address: "x", width_unit: "cm" });
  assert.deepEqual(body, { name: "A", width: "", height: 4, unit: "ft" });
  assert.equal(shopPayload({ name: "B", width: 1, height: 2 }).unit, "in");
});

import { readFileSync } from "node:fs";
import { isDraft, toDraftRow } from "./shopPayload.js";
import { parseShopFile } from "./shopImport.js";
import * as XLSX from "xlsx";

test("an imported sheet becomes editable draft rows with every table field, entirely in the browser", async () => {
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([["Client", "Size"], ["A", "10*4"], ["B", "12ft x 4ft"]]), "S");
  const parsed = await parseShopFile(new File([XLSX.write(wb, { type: "array", bookType: "xlsx" })], "x.xlsx"));
  const rows = parsed.shops.map(toDraftRow);
  assert.equal(rows.length, 2);
  assert.ok(rows.every(isDraft) && new Set(rows.map((r) => r.id)).size === 2);
  assert.deepEqual(Object.keys(rows[0]).sort(), ["height", "id", "name", "progress_pct", "status", "unit", "unitSource", "width"]);
  assert.deepEqual([rows[1].width, rows[1].height, rows[1].unit], [12, 4, "ft"]);
  assert.equal(rows[0].status, "new");
  assert.equal(isDraft({ id: "a1b2c3" }), false);
});

test("Automation.jsx: importFile makes no network request", () => {
  const src = readFileSync(new URL("../pages/Automation.jsx", import.meta.url), "utf8");
  const start = src.indexOf("async function importFile");
  const body = src.slice(start, src.indexOf("function pollShop", start));
  assert.ok(start > 0 && body.length > 200);
  assert.doesNotMatch(body, /fetch\(|XMLHttpRequest|axios/);
  assert.doesNotMatch(src, /import-excel|shops\/batch/);
});

test("a new/replaced master turns every saved shop back into a fresh draft, keeping name, size and unit", () => {
  const shops = [
    { id: "a1", job_id: "old", name: "Done", width: 120, height: 48, unit: "in", status: "done", progress_pct: 100, files: { cdr: "x.cdr" } },
    { id: "a2", job_id: "old", name: "Busy", width: 10, height: 4, width_unit: "ft", height_unit: "ft", status: "converting", progress_pct: 74 },
    { id: "a3", job_id: "old", name: "Bad", width: 30, height: 40, unit: "in", status: "failed", error: "boom" },
    { id: "a4", job_id: "old", name: "Saved", width: 5, height: 2, unit: "ft", status: "new" },
    { id: "draft-keep", name: "Draft", width: 8, height: 3, unit: "ft", status: "new", progress_pct: 0 },
  ];
  const { shops: out, reset } = resetForNewMaster(shops);
  assert.equal(reset, 4);
  assert.equal(out.length, 5);
  assert.equal(out[4], shops[4]); // an untouched draft is kept as is (same object, same id)
  for (const [i, x] of out.slice(0, 4).entries()) {
    assert.ok(isDraft(x), `row ${i} is a draft`);
    assert.equal(x.status, "new");
    assert.equal(x.progress_pct, 0);
    assert.equal(x.job_id, undefined);
    assert.equal(x.files, undefined);
    assert.equal(x.error, undefined);
    assert.equal(x.name, shops[i].name);
    assert.equal(x.width, shops[i].width);
    assert.equal(x.height, shops[i].height);
  }
  assert.equal(out[1].unit, "ft"); // a server row without `unit` falls back to width_unit
  assert.deepEqual(resetForNewMaster([]), { shops: [], reset: 0 });
});

test("the local shop name rides along from an import draft into the convert payload", async () => {
  const { shopPayload: pay, toDraftRow: draft } = await import("./shopPayload.js");
  const d = draft({ name: "ANISH STORES", shop_name_local: "அனிஷ் ஸ்டோர்ஸ்", width: 8, height: 4, unit: "ft" });
  assert.equal(pay(d).shop_name_local, "அனிஷ் ஸ்டோர்ஸ்");
  assert.equal("shop_name_local" in pay(draft({ name: "X", width: 1, height: 1 })), false);
});

test("applyDefaultUnit: only rows that took the default and are still editable change", async () => {
  const { applyDefaultUnit, toDraftRow } = await import("./shopPayload.js");
  const rows = [
    toDraftRow({ name: "default", width: 10, height: 4, unit: "in", unitSource: "default" }),
    toDraftRow({ name: "excel", width: 120, height: 48, unit: "in", unitSource: "excel" }),
    { ...toDraftRow({ name: "manual", width: 5, height: 2, unit: "in", unitSource: "default" }), unitSource: "manual" },
    { id: "srv1", name: "saved default", width: 8, height: 3, unit: "in", unitSource: "default", status: "failed" },
    { id: "srv2", name: "converted", width: 8, height: 3, unit: "in", unitSource: "default", status: "done" },
    { id: "srv3", name: "loaded from server", width: 8, height: 3, unit: "in", status: "new" },
  ];
  const { shops, changed } = applyDefaultUnit(rows, "ft");
  assert.deepEqual(shops.map((x) => [x.name, x.unit]), [
    ["default", "ft"], ["excel", "in"], ["manual", "in"], ["saved default", "ft"], ["converted", "in"], ["loaded from server", "in"],
  ]);
  assert.deepEqual(changed, [rows[0].id, "srv1"]);
  assert.equal(shops[0].width, 10); // numbers are kept: the default says what they mean
  assert.deepEqual(applyDefaultUnit(shops, "ft").changed, []); // already ft: nothing to do
});

test("toDraftRow keeps unitSource; resetForNewMaster carries it over", async () => {
  const { toDraftRow } = await import("./shopPayload.js");
  const d = toDraftRow({ name: "A", width: 1, height: 1, unit: "ft", unitSource: "default" });
  assert.equal(d.unitSource, "default");
  const { shops } = resetForNewMaster([{ id: "s1", name: "A", width: 1, height: 1, unit: "ft", unitSource: "excel", status: "done" }]);
  assert.equal(shops[0].unitSource, "excel");
  assert.equal(toDraftRow({ name: "B", width: 1, height: 1 }).unitSource, undefined);
});

test("board type: normalised spellings, carried by drafts and the payload; Tamil name clears with an empty string", async () => {
  const { normalizeBoardType, toDraftRow: draft, shopPayload: pay, cdrDownloadUrl } = await import("./shopPayload.js");
  assert.equal(normalizeBoardType("NON LIT"), "Nonlit");
  assert.equal(normalizeBoardType("one-way vision"), "OneWayVision");
  assert.equal(normalizeBoardType("2 Nos Double Side GSB"), "2 Nos Double Side GSB");
  assert.equal(normalizeBoardType(""), "");
  const d = draft({ name: "A", width: 1, height: 1, board_type: "Backlit" });
  assert.equal(pay(d).board_type, "Backlit");
  assert.equal("board_type" in pay(draft({ name: "B", width: 1, height: 1 })), false);
  assert.equal(pay({ name: "C", width: 1, height: 1, shop_name_local: null }).shop_name_local, "");
  assert.equal(cdrDownloadUrl([{ id: "a1", no: 1 }, { id: "b2", no: 3 }]), "/api/v2/download-cdrs?ids=a1%2Cb2&nos=1%2C3");
});
