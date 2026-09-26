// Pure batch-progress maths for the Shops Queue banner (unit-tested in batchStats.test.mjs).

/** Batch progress from the shops' own state: finished = done or failed; each in-flight shop adds its backend %.
 *  Time remaining = (average time per finished shop) x (shops left); before the first shop finishes it is
 *  extrapolated from the overall progress fraction instead. null = not enough data yet. */
export function batchStats(batch, shops, now) {
  if (!batch) return { batchProgress: 0, estimatedTimeRemaining: null, currentShopIndex: 0, allSettled: false, done: 0, failed: 0 };
  const mine = shops.filter((x) => batch.ids.includes(x.id));
  const done = mine.filter((x) => x.status === "done").length;
  const failed = mine.filter((x) => x.status === "failed").length;
  const inFlight = mine.filter((x) => x.status === "converting").reduce((a, x) => a + (x.progress_pct || 0) / 100, 0);
  const finished = done + failed + batch.notStarted;
  const fraction = Math.min(1, (finished + inFlight) / batch.total);
  const elapsed = Math.max(0, (now - batch.startedAt) / 1000);
  let remaining = null;
  if (finished > 0) remaining = Math.round((elapsed / finished) * (batch.total - finished));
  else if (fraction > 0.05) remaining = Math.round((elapsed / fraction) * (1 - fraction));
  return {
    batchProgress: Math.round(fraction * 100),
    estimatedTimeRemaining: remaining,
    currentShopIndex: Math.min(finished + 1, batch.total),
    // settled = every shop in the batch has started (or failed to) and reached a final state
    allSettled: batch.ids.length + batch.notStarted === batch.total && finished === batch.total,
    done,
    failed,
  };
}

export const fmtSeconds = (n) => (n == null ? "calculating…" : n >= 90 ? `${Math.floor(n / 60)}m ${n % 60}s` : `${n}s`);

