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
