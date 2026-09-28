import { test } from "node:test";
import assert from "node:assert/strict";
import { filenameFrom, fmtSqft, printTotals, sqFeet, todayISO } from "./printSheet.js";

test("totals match the reference sheet (3 Nos, 185 sq ft)", () => {
  const shops = [
    { width: 214, height: 36, unit: "in" },
    { width: 180, height: 36, unit: "in" },
    { width: 233.5, height: 53.5, width_unit: "in", height_unit: "in" },
  ];
  const t = printTotals(shops);
  assert.equal(t.qty, 3);
  assert.equal(fmtSqft(t.sqft), "185");
  assert.ok(Math.abs(t.sqft - 26676.25 / 144) < 1e-9);
});

test("mixed units and small boards", () => {
  assert.ok(Math.abs(sqFeet({ width: 10, width_unit: "ft", height: 48, height_unit: "in" }) - 40) < 1e-9);
  assert.equal(fmtSqft(sqFeet({ width: 10, height: 4, unit: "in" })), "0.3");
  assert.equal(fmtSqft(0), "0");
});

test("today is the local date, not UTC", () => {
  assert.equal(todayISO(new Date(2026, 8, 26, 23, 59)), "2026-09-26");
  assert.equal(todayISO(new Date(2026, 0, 5, 0, 1)), "2026-01-05");
});

test("download name comes from Content-Disposition", () => {
  assert.equal(filenameFrom('attachment; filename="Print_Details_22071195_26.09.2026.pdf"', "x"), "Print_Details_22071195_26.09.2026.pdf");
  assert.equal(filenameFrom("attachment; filename*=UTF-8''Print%20Details.jpg", "x"), "Print Details.jpg");
  assert.equal(filenameFrom(null, "fallback.pdf"), "fallback.pdf");
});
