// Pure batch-progress maths for the Shops Queue banner (unit-tested in batchStats.test.mjs). The page holds the small
// amount of state these need (the batch, its finish times, the displayed peak and ETA) and resets it for every new batch.

export const ETA_WINDOW = 5; // completion intervals in the moving average

/** Batch progress from the shops' own state: finished (done or failed) = 1 shop's worth, a converting shop = its backend %,
 *  queued = 0. Time remaining = moving average of the last ETA_WINDOW completion intervals x the work still left (the
 *  in-flight shops' own % included, so it drops continuously instead of only when a shop finishes); before the first shop
 *  finishes it is extrapolated from the progress so far. Never negative; null = not enough data yet.
 *  `batch.finishTimes` = when each batch shop reached done/failed (ms, ascending; see recordFinishes). */
export function batchStats(batch, shops, now) {
  if (!batch) return { batchProgress: 0, estimatedTimeRemaining: null, currentShopIndex: 0, allSettled: false, done: 0, failed: 0 };
  const mine = shops.filter((x) => batch.ids.includes(x.id));
  const done = mine.filter((x) => x.status === "done").length;
  const failed = mine.filter((x) => x.status === "failed").length;
  const inFlight = mine.filter((x) => x.status === "converting").reduce((a, x) => a + Math.min(100, Math.max(0, x.progress_pct || 0)) / 100, 0);
  const finished = Math.min(batch.total, done + failed + batch.notStarted);
  const work = Math.min(batch.total, finished + inFlight);
  const fraction = batch.total ? work / batch.total : 0;
  const elapsed = Math.max(0, (now - batch.startedAt) / 1000);

  let perShop = null;
  const times = (batch.finishTimes || []).filter((t) => t >= batch.startedAt);
  if (times.length) {
    const points = [batch.startedAt, ...times];
    const gaps = points.slice(1).map((t, i) => (t - points[i]) / 1000).slice(-ETA_WINDOW);
    perShop = gaps.reduce((a, g) => a + g, 0) / gaps.length;
  } else if (fraction > 0.05) {
    perShop = elapsed / work;
  }
  const remaining = perShop == null ? null : Math.max(0, Math.round(perShop * (batch.total - work)));
  return {
    batchProgress: Math.round(fraction * 100),
    estimatedTimeRemaining: remaining,
    currentShopIndex: Math.min(finished + 1, batch.total),
    // settled = every shop in the batch has started (or failed to) and reached a final state
    allSettled: batch.ids.length + batch.notStarted === batch.total && done + failed + batch.notStarted >= batch.total,
    done,
    failed,
  };
}

/** `finishTimes` extended to `settled` entries (new ones stamped `now`) - called whenever the settled count changes. */
export function recordFinishes(finishTimes, settled, now) {
  const t = finishTimes || [];
  return settled > t.length ? [...t, ...new Array(settled - t.length).fill(now)] : t;
}

/** The displayed batch %: never below what was already shown in this batch (the page resets `peak` to 0 per batch). */
export function monotonicProgress(peak, raw) {
  return Math.min(100, Math.max(peak || 0, raw || 0));
}

/** The displayed ETA: counts down by itself between estimates and moves a quarter of the way to each new estimate, so it
 *  neither jumps nor sits still; never negative. `prev` = {value, at} or null, `est` = seconds or null. */
export function smoothEta(prev, est, now) {
  if (est == null) return prev ? { value: Math.max(0, prev.value - (now - prev.at) / 1000), at: now } : null;
  if (!prev) return { value: Math.max(0, est), at: now };
  const counted = Math.max(0, prev.value - (now - prev.at) / 1000);
  return { value: Math.max(0, counted + (est - counted) * 0.25), at: now };
}

/** "~2m 15s remaining", "~45s remaining", "Finishing up...", "Calculating..." - never negative. */
export function fmtEta(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return "Calculating...";
  const s = Math.max(0, Math.round(seconds));
  if (s === 0) return "Finishing up...";
  const m = Math.floor(s / 60);
  return m > 0 ? `~${m}m ${s % 60}s remaining` : `~${s}s remaining`;
}

/** The line shown when Convert All has finished: how many converted and how many failed (or never started). */
export function batchFinishedText(done, failed) {
  const tail = failed ? ", " + failed + " failed" : "";
  return `Batch finished: ${done} converted${tail}.`;
}
