import { test } from "node:test";
import assert from "node:assert/strict";
import { zipIssuesText, zipRequest, zipSummaryText } from "./assetZip.js";

test("request carries the queue order and S.no numbers", () => {
  assert.deepEqual(zipRequest([{ id: "a", no: 1 }, { id: "b", no: 3 }]), { shop_ids: ["a", "b"], numbers: { a: 1, b: 3 } });
});

test("summary line: counts, missing files, unexported edits", () => {
  assert.equal(
    zipSummaryText({ shops: 2, files: 6, bytes: 0, missing: [], notes: [] }),
    "Signage_Assets_Export.zip: 2 shops, 6 files.",
  );
  const t = zipSummaryText({
    shops: 3, files: 4, missing: ["01_A.jpg", "pdf/01_A.pdf", "02_B.jpg", "pdf/02_B.pdf", "pdf/03_C.pdf"],
    notes: ["02_B: has editor edits that were not exported", "03_C: x"],
  });
  assert.match(t, /3 shops, 4 files\./);
  assert.match(t, /Not available: 01_A\.jpg, pdf\/01_A\.pdf, 02_B\.jpg, pdf\/02_B\.pdf and 1 more\./);
  assert.match(t, /02_B: has editor edits that were not exported \(\+1 more shop\)\./);
});

test("issues alone: never cut at the dot in the file name", () => {
  const s = { shops: 1, files: 1, bytes: 180, missing: ["01_A.jpg"], notes: [] };
  assert.equal(zipIssuesText(s), "Not available: 01_A.jpg.");
  assert.equal(zipIssuesText({ shops: 1, files: 3, missing: [], notes: [] }), "");
  assert.ok(zipSummaryText(s).startsWith("Signage_Assets_Export.zip: 1 shop, 1 file (180 B). Not available"));
});
