import test from "node:test";
import assert from "node:assert/strict";
import { fmtBytes } from "./fileSize.js";

test("file sizes are shown in B / KB / MB / GB", () => {
  assert.equal(fmtBytes(512), "512 B");
  assert.equal(fmtBytes(2048), "2.0 KB");
  assert.equal(fmtBytes(9166890), "8.7 MB");
  assert.equal(fmtBytes(208.8 * 1024 * 1024), "209 MB");
  assert.equal(fmtBytes(3 * 1024 ** 3), "3.0 GB");
  assert.equal(fmtBytes(undefined), "");
  assert.equal(fmtBytes(-1), "");
});
