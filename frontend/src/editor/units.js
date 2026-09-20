// Unit conversion (the scene is always mm) and ruler tick generation.

export const UNITS = {
  in: { mm: 25.4, decimals: 3 },
  mm: { mm: 1, decimals: 2 },
  cm: { mm: 10, decimals: 3 },
  ft: { mm: 304.8, decimals: 4 },
};
export const UNIT_NAMES = Object.keys(UNITS);

export const toUnit = (mm, unit) => mm / UNITS[unit].mm;
export const fromUnit = (v, unit) => v * UNITS[unit].mm;

/** "120 in", "3048 mm" - trailing zeros trimmed. */
export function fmt(mm, unit) {
  const v = toUnit(mm, unit);
  const s = v.toFixed(UNITS[unit].decimals);
  return s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s;
}

/** A "nice" step (1/2/5 x 10^k, in the ruler's unit) that is at least `minPx` wide on screen. */
export function niceStep(pxPerUnit, minPx = 64) {
  const raw = minPx / pxPerUnit;
  const pow = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 5, 10]) if (m * pow >= raw) return m * pow;
  return 10 * pow;
}

/**
 * Ticks for a ruler covering [startMm, endMm] (scene mm, measured from the
 * page origin) at `zoom` px/mm, shown in `unit`. Major ticks carry a label.
 */
export function rulerTicks(startMm, endMm, zoom, unit) {
  const u = UNITS[unit].mm;
  const step = niceStep(zoom * u);
  const minor = step / 5;
  const showMinor = minor * u * zoom >= 6;
  const from = Math.floor(startMm / u / minor) - 1;
  const to = Math.ceil(endMm / u / minor) + 1;
  const ticks = [];
  for (let i = from; i <= to && ticks.length < 1500; i++) {
    const val = i * minor;
    const major = Math.abs(val / step - Math.round(val / step)) < 1e-6;
    if (!major && !showMinor) continue;
    const decimals = step >= 1 ? 0 : Math.min(4, Math.ceil(-Math.log10(step)));
    ticks.push({ mm: val * u, major, label: major ? val.toFixed(decimals) : null });
  }
  return ticks;
}
