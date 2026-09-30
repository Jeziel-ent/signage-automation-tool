import test from "node:test";
import assert from "node:assert/strict";
import { downloadHref, SINGLE_FORMATS } from "./shopDownload.js";

test("row download link: format + S.no for the standard file name", () => {
  assert.deepEqual(SINGLE_FORMATS, ["cdr", "jpg", "png", "pdf"]);
  assert.equal(downloadHref({ id: "a1", no: 7 }, "jpg"), "/api/v2/shops/a1/download/jpg?no=7");
  assert.equal(downloadHref({ id: "a1" }, "cdr"), "/api/v2/shops/a1/download/cdr");
});
