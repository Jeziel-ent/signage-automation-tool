import test from "node:test";
import assert from "node:assert/strict";
import { fmt, trimZeros } from "./units.js";

test("trimZeros drops trailing zeros and a dangling point, nothing else", () => {
  assert.equal(trimZeros("12.500"), "12.5");
  assert.equal(trimZeros("12.000"), "12");
  assert.equal(trimZeros("0.050"), "0.05");
  assert.equal(trimZeros("0.000"), "0");
  assert.equal(trimZeros("120"), "120"); // no decimal point: the zeros are significant
  assert.equal(trimZeros("100"), "100");
  assert.equal(trimZeros("-3.250"), "-3.25");
});

test("trimZeros is linear on a long zero run (no regex backtracking)", () => {
  const long = "1." + "0".repeat(100000);
  const t0 = Date.now();
  assert.equal(trimZeros(long), "1");
  assert.ok(Date.now() - t0 < 500);
});

test("fmt formats through trimZeros", () => {
  assert.equal(fmt(25.4, "in"), "1");
  assert.equal(fmt(12.7, "in"), "0.5");
});
