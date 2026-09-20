import { useEffect, useRef, useState } from "react";

/**
 * Smoothly eases a displayed progress percentage toward the end of
 * whichever step the backend last reported, instead of jumping straight
 * to each new polled value - per review feedback after Phase A ("ease the
 * displayed value toward the next real step value using measured step
 * durations, cap it just below the next step, never show 100% until the
 * backend reports Done"). Reusable for both the Automation page's convert
 * progress and Phase D's export progress - anything driven by a sequence
 * of named backend steps polled over HTTP rather than a byte-accurate
 * progress event.
 *
 * @param {Array<{key: string, endPct: number}>} steps - ordered steps,
 *   `endPct` is the cumulative percent reached once that step is CONFIRMED
 *   complete (i.e. the backend has moved on to the next one, or is done).
 * @param {string|null} currentStepKey - the step the backend most
 *   recently reported as in progress (null/undefined if nothing has
 *   started yet).
 * @param {boolean} done - true once the backend reports the whole job
 *   finished; only then does the displayed value reach 100.
 * @param {Object<string, number>} estimates - measured average duration
 *   in SECONDS per step key (e.g. from `GET /api/v2/step-estimates`) -
 *   real numbers when available; steps missing from this object fall back
 *   to `defaultDurationMs`.
 * @param {number} defaultDurationMs - fallback pacing for a step with no
 *   measured data yet (a brand-new install, or a step that's never been
 *   timed before).
 */
export function useSteppedProgress(steps, currentStepKey, done, estimates, defaultDurationMs = 4000) {
  const [displayed, setDisplayed] = useState(0);
  const stepStateRef = useRef({ key: null, startedAt: 0, from: 0 });

  useEffect(() => {
    if (done) {
      setDisplayed(100);
      return undefined;
    }
    if (!currentStepKey) return undefined;

    const idx = steps.findIndex((s) => s.key === currentStepKey);
    if (idx === -1) return undefined;

    const endPct = steps[idx].endPct;
    const startPct = idx === 0 ? 0 : steps[idx - 1].endPct;
    const cap = Math.max(startPct, endPct - 1); // never touch the next real threshold ourselves

    if (stepStateRef.current.key !== currentStepKey) {
      stepStateRef.current = {
        key: currentStepKey,
        startedAt: Date.now(),
        from: Math.max(displayed, startPct),
      };
    }

    const estimateSeconds = estimates && estimates[currentStepKey];
    const durationMs = estimateSeconds > 0 ? estimateSeconds * 1000 : defaultDurationMs;

    const tick = setInterval(() => {
      const { startedAt, from } = stepStateRef.current;
      const t = Math.min(1, (Date.now() - startedAt) / durationMs);
      // ease-out (decelerating) - quick at first, creeps as it nears the cap,
      // so a step that runs longer than estimated doesn't visibly stall dead.
      const eased = from + (cap - from) * (1 - (1 - t) * (1 - t));
      setDisplayed((prev) => Math.max(prev, Math.min(cap, eased)));
    }, 150);

    return () => clearInterval(tick);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentStepKey, done, estimates]);

  return Math.round(displayed);
}
