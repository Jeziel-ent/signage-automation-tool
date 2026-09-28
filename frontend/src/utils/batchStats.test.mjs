import test from "node:test";
import assert from "node:assert/strict";
import { batchStats, fmtEta, monotonicProgress, recordFinishes, smoothEta } from "./batchStats.js";

const shop = (id, status, progress_pct = 0) => ({ id, status, progress_pct });
const batch = (ids, total, extra = {}) => ({ startedAt: 0, total, ids, notStarted: 0, ...extra });

test("no batch -> zeros", () => {
  assert.deepEqual(batchStats(null, [], 0), { batchProgress: 0, estimatedTimeRemaining: null, currentShopIndex: 0, allSettled: false, done: 0, failed: 0 });
});

test("weighted progress: finished = 100, converting = its own %, queued = 0", () => {
  const shops = [shop("a", "done"), shop("b", "converting", 50), shop("c", "queued"), shop("d", "failed")];
  const s = batchStats(batch(["a", "b", "c", "d"], 4), shops, 10_000);
  assert.equal(s.batchProgress, 63);                 // (1 + 0.5 + 0 + 1) / 4
  assert.equal(s.currentShopIndex, 3);
  assert.equal(s.allSettled, false);
});

test("time remaining = moving average of the completion intervals x work left (in-flight % included)", () => {
  const shops = [shop("a", "done"), shop("b", "done"), shop("c", "converting", 50), shop("d", "queued")];
  const s = batchStats(batch(["a", "b", "c", "d"], 4, { finishTimes: [10_000, 20_000] }), shops, 25_000);
  assert.equal(s.estimatedTimeRemaining, 15);        // 10 s per shop x (4 - 2.5)
  // it does NOT grow while a shop is working (the old elapsed / finished estimate did): same state later, same estimate
  assert.equal(batchStats(batch(["a", "b", "c", "d"], 4, { finishTimes: [10_000, 20_000] }), shops, 60_000).estimatedTimeRemaining, 15);
});

test("the moving average uses only the last intervals: a slow first shop (CorelDRAW launch) stops counting", () => {
  const ids = ["a", "b", "c", "d", "e", "f", "g", "h"];
  const shops = ids.map((id, i) => shop(id, i < 6 ? "done" : "queued"));
  const finishTimes = [60_000, 70_000, 80_000, 90_000, 100_000, 110_000];          // 60 s, then 10 s each
  const s = batchStats(batch(ids, 8, { finishTimes }), shops, 110_000);
  assert.equal(s.estimatedTimeRemaining, 20);        // last 5 gaps = 10 s each, 2 shops left
});

test("never negative, even with more finishes than shops (e.g. a retried shop recorded twice)", () => {
  const shops = [shop("a", "done"), shop("b", "done")];
  const s = batchStats(batch(["a", "b"], 2, { finishTimes: [5_000, 6_000, 7_000] }), shops, 8_000);
  assert.equal(s.estimatedTimeRemaining, 0);
  assert.equal(s.batchProgress, 100);
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

test("displayed progress never goes backwards within a batch", () => {
  let peak = 0;
  const seen = [];
  for (const raw of [14, 28, 25, 42, 5, 57, 100]) {       // a shop dropping back (the old server bug) must not show
    peak = monotonicProgress(peak, raw);
    seen.push(peak);
  }
  assert.deepEqual(seen, [14, 28, 28, 42, 42, 57, 100]);
  assert.equal(monotonicProgress(0, 3), 3);               // a new batch starts from its own 0
});

test("recordFinishes stamps each newly settled shop once", () => {
  assert.deepEqual(recordFinishes([], 2, 500), [500, 500]);
  assert.deepEqual(recordFinishes([500], 1, 900), [500]);
  assert.deepEqual(recordFinishes([500], 2, 900), [500, 900]);
});

test("ETA counts down between estimates, eases toward new ones, never below 0", () => {
  let e = smoothEta(null, 100, 0);
  assert.equal(e.value, 100);
  e = smoothEta(e, 100, 10_000);                          // 10 s later, same estimate: 90 + (100 - 90) / 4
  assert.equal(e.value, 92.5);
  e = smoothEta(e, null, 20_000);                         // no estimate: keeps counting down
  assert.equal(e.value, 82.5);
  e = smoothEta(e, 0, 200_000);
  assert.equal(e.value, 0);
  assert.equal(smoothEta(null, null, 0), null);
});

test("ETA text", () => {
  assert.equal(fmtEta(null), "Calculating...");
  assert.equal(fmtEta(0), "Finishing up...");
  assert.equal(fmtEta(-216), "Finishing up...");
  assert.equal(fmtEta(45), "~45s remaining");
  assert.equal(fmtEta(135.4), "~2m 15s remaining");
  assert.equal(fmtEta(60), "~1m 0s remaining");
});
