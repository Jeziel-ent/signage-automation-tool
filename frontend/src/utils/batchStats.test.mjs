import test from "node:test";
import assert from "node:assert/strict";
import { batchStats, fmtSeconds } from "./batchStats.js";

const shop = (id, status, progress_pct = 0) => ({ id, status, progress_pct });
const batch = (ids, total, extra = {}) => ({ startedAt: 0, total, ids, notStarted: 0, ...extra });

test("no batch -> zeros", () => {
  assert.deepEqual(batchStats(null, [], 0), { batchProgress: 0, estimatedTimeRemaining: null, currentShopIndex: 0, allSettled: false, done: 0, failed: 0 });
});

test("progress counts finished shops plus the in-flight shop's own percentage", () => {
  const shops = [shop("a", "done"), shop("b", "converting", 50), shop("c", "queued"), shop("d", "queued")];
  const s = batchStats(batch(["a", "b", "c", "d"], 4), shops, 10_000);
  assert.equal(s.batchProgress, 38);                 // (1 + 0.5) / 4
  assert.equal(s.currentShopIndex, 2);
  assert.equal(s.allSettled, false);
});

test("time remaining = average seconds per finished shop x shops left", () => {
  const shops = [shop("a", "done"), shop("b", "done"), shop("c", "converting", 10), shop("d", "queued")];
  const s = batchStats(batch(["a", "b", "c", "d"], 4), shops, 20_000);   // 20 s for 2 shops -> 10 s each, 2 left
  assert.equal(s.estimatedTimeRemaining, 20);
});

test("before the first shop finishes the estimate is extrapolated from overall progress; null when too little data", () => {
  const early = batchStats(batch(["a", "b"], 2), [shop("a", "converting", 50), shop("b", "queued")], 10_000);   // 25% in 10 s
  assert.equal(early.estimatedTimeRemaining, 30);
  const tiny = batchStats(batch(["a", "b"], 2), [shop("a", "converting", 2), shop("b", "queued")], 1_000);
  assert.equal(tiny.estimatedTimeRemaining, null);
});

test("settled only when every shop started and reached done/failed; failures and not-started shops count as finished", () => {
  assert.equal(batchStats(batch(["a"], 2), [shop("a", "done")], 0).allSettled, false);           // second shop has not started yet
  const s = batchStats(batch(["a", "b"], 3, { notStarted: 1 }), [shop("a", "done"), shop("b", "failed")], 5_000);
  assert.equal(s.allSettled, true);
  assert.deepEqual([s.done, s.failed, s.batchProgress], [1, 1, 100]);
});

test("seconds formatting", () => {
  assert.equal(fmtSeconds(null), "calculating…");
  assert.equal(fmtSeconds(42), "42s");
  assert.equal(fmtSeconds(125), "2m 5s");
});
