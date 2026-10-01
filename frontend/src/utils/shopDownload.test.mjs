import test from "node:test";
import assert from "node:assert/strict";
import { downloadHref, SINGLE_FORMATS } from "./shopDownload.js";

test("row download link: format + S.no for the standard file name", () => {
  assert.deepEqual(SINGLE_FORMATS, ["cdr", "jpg", "png", "pdf"]);
  assert.equal(downloadHref({ id: "a1", no: 7 }, "jpg"), "/api/v2/shops/a1/download/jpg?no=7");
  assert.equal(downloadHref({ id: "a1" }, "cdr"), "/api/v2/shops/a1/download/cdr");
});

test("row download link carries a text S.No too", () => {
  assert.equal(downloadHref({ id: "s1", no: "SL-01" }, "pdf"), "/api/v2/shops/s1/download/pdf?no=SL-01");
  assert.equal(downloadHref({ id: "s1", no: "" }, "pdf"), "/api/v2/shops/s1/download/pdf");
});

test("Download All link: chosen format, ids and S.Nos in table order", async () => {
  const { allDownloadUrl } = await import("./shopDownload.js");
  assert.equal(allDownloadUrl([{ id: "a1", no: 1 }, { id: "b2", no: "SL-3" }], "pdf"),
    "/api/v2/download-all?format=pdf&ids=a1%2Cb2&nos=1%2CSL-3");
});
