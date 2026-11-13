import test from "node:test";
import assert from "node:assert/strict";
import { ago, fmtUptime, presence, spacesPct, usageTotals } from "./adminView.js";

test("ago says how long, in words", () => {
  assert.equal(ago(null), "never");
  assert.equal(ago(990, 1000), "just now");
  assert.equal(ago(1000 - 180, 1000), "3 min ago");
  assert.equal(ago(1000 - 7300, 1000), "2 h ago");
  assert.equal(ago(1000 - 86400, 1000), "1 day ago");
  assert.equal(ago(1000 - 5 * 86400, 1000), "5 days ago");
});

test("fmtUptime keeps the two biggest units", () => {
  assert.equal(fmtUptime(null), "unknown");
  assert.equal(fmtUptime(480), "8 min");
  assert.equal(fmtUptime(5 * 3600 + 12 * 60), "5 h 12 min");
  assert.equal(fmtUptime(3 * 86400 + 4 * 3600), "3 d 4 h");
});

test("presence: PC online + signed in is working, then PC, signed in, away", () => {
  const pc = { online: true };
  assert.equal(presence({ pcs: [pc], signed_in: true }).key, "working");
  assert.equal(presence({ pcs: [pc], signed_in: false }).key, "pc");
  assert.equal(presence({ pcs: [{ online: false }], signed_in: true }).key, "signed");
  assert.equal(presence({ pcs: [], signed_in: false }).label, "Away");
});

test("usageTotals adds the designers up", () => {
  const t = usageTotals([{ converted: 3, exported: 1, failed: 0, logins: 2 }, { converted: 4, exported: 2, failed: 1, logins: 5 }]);
  assert.deepEqual(t, { converted: 7, exported: 3, failed: 1, logins: 7 });
});

test("spacesPct needs an allowance and never passes 100", () => {
  assert.equal(spacesPct({ bytes: 5 }), null);
  assert.equal(spacesPct({ bytes: 25, quota_bytes: 100 }), 25);
  assert.equal(spacesPct({ bytes: 500, quota_bytes: 100 }), 100);
});

import { niceMax, sparkPath, fmtSeconds, jobResult, seriesTotals } from "./adminView.js";

test("niceMax rounds up to 1/2/5 steps and never below 4", () => {
  assert.equal(niceMax(0), 5);
  assert.equal(niceMax(3), 5);
  assert.equal(niceMax(7), 10);
  assert.equal(niceMax(13), 20);
  assert.equal(niceMax(48), 50);
  assert.equal(niceMax(120), 200);
});

test("sparkPath draws a 0-100 series into its box", () => {
  assert.equal(sparkPath([50], 100, 20), "");
  assert.equal(sparkPath([0, 100], 100, 20), "M0.0,20.0 L100.0,0.0");
  assert.equal(sparkPath([0, 150, null, 50], 90, 10), "M0.0,10.0 L45.0,0.0 L90.0,5.0");
});

test("fmtSeconds", () => {
  assert.equal(fmtSeconds(null), "-");
  assert.equal(fmtSeconds(8.43), "8.4 s");
  assert.equal(fmtSeconds(42), "42 s");
  assert.equal(fmtSeconds(125), "2 min 05 s");
});

test("jobResult: a stop is not a failure", () => {
  assert.equal(jobResult({ state: "done" }).label, "Done");
  assert.equal(jobResult({ state: "cancelled" }).label, "Stopped");
  assert.equal(jobResult({ state: "failed" }).label, "Failed");
});

test("seriesTotals adds every day", () => {
  assert.deepEqual(seriesTotals([{ converted: 2, failed: 1, stopped: 0, exported: 3 }, { converted: 1, failed: 0, stopped: 2, exported: 0 }]),
    { converted: 3, failed: 1, stopped: 2, exported: 3 });
});
