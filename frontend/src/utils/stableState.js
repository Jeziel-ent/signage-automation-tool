// Polled data that has not changed must not re-render the app. A poll hands back a NEW object every time; storing it as it is re-renders
// every consumer of the context (the Automation page sits mounted under the welcome screen) every few seconds, which showed up as the
// welcome screen's 3D city stuttering / flickering. `keepIfSame(prev, next)` returns `prev` (same reference = no re-render) when next
// has the same content: `setX((prev) => keepIfSame(prev, next))`.
export function keepIfSame(prev, next) {
  if (prev === next) return prev;
  try {
    return JSON.stringify(prev) === JSON.stringify(next) ? prev : next;
  } catch {
    return next;
  }
}
