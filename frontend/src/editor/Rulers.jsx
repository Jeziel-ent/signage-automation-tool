import { fmt, rulerTicks } from "./units.js";

export const RULER = 22; // ruler thickness (px)
export const DIM = 20; // dimension-marker strip thickness (px)

const ARROW = 6;
// colours come from CSS variables (editor-dark.css) with the old light values as fallbacks
const line = "var(--ed-tick, var(--color-gray-600))";
const DIM_BG = "var(--ed-dim-bg, var(--color-white))"; // masks the dimension line behind its label
// the selection's extent on a ruler: a translucent red band with crisp edges (the rulers' accent guides)
const BAND_FILL = "rgba(239, 68, 68, 0.18)";

/** Horizontal ruler. `view.x` is the screen x of the page's left edge (px), `zoom` px/mm. */
export function TopRuler({ width, view, unit, cursor, span }) {
  const ticks = rulerTicks((0 - view.x) / view.zoom, (width - view.x) / view.zoom, view.zoom, unit);
  return (
    <svg className="ed-ruler" width={width} height={RULER}>
      {ticks.map((t, i) => {
        const x = view.x + t.mm * view.zoom;
        return (
          <g key={i}>
            <line x1={x} x2={x} y1={RULER} y2={t.major ? 6 : 15} stroke={line} strokeWidth="1" />
            {t.label !== null && <text x={x + 3} y={11} className="ed-ruler-label">{t.label}</text>}
          </g>
        );
      })}
      {span && (
        <g pointerEvents="none">
          <rect x={view.x + span.x * view.zoom} y={0} width={Math.max(1, span.w * view.zoom)} height={RULER} fill={BAND_FILL} />
          <line x1={view.x + span.x * view.zoom} x2={view.x + span.x * view.zoom} y1={0} y2={RULER} stroke="var(--color-red)" strokeWidth="1" />
          <line x1={view.x + (span.x + span.w) * view.zoom} x2={view.x + (span.x + span.w) * view.zoom} y1={0} y2={RULER} stroke="var(--color-red)" strokeWidth="1" />
        </g>
      )}
      {cursor && <line x1={view.x + cursor.x * view.zoom} x2={view.x + cursor.x * view.zoom} y1="0" y2={RULER} stroke="var(--color-red)" strokeWidth="1" />}
    </svg>
  );
}

/** Vertical ruler: values run from the page's bottom edge upward (CorelDRAW's origin). */
export function LeftRuler({ height, view, pageH, unit, cursor, span }) {
  const top = pageH + view.y / view.zoom; // mm at the very top of the viewport
  const bottom = pageH - (height - view.y) / view.zoom;
  const ticks = rulerTicks(bottom, top, view.zoom, unit);
  const sy = (mm) => view.y + (pageH - mm) * view.zoom;
  return (
    <svg className="ed-ruler" width={RULER} height={height}>
      {ticks.map((t, i) => {
        const y = sy(t.mm);
        return (
          <g key={i}>
            <line x1={RULER} x2={t.major ? 6 : 15} y1={y} y2={y} stroke={line} strokeWidth="1" />
            {t.label !== null && (
              <text transform={`translate(11 ${y - 3}) rotate(-90)`} className="ed-ruler-label">{t.label}</text>
            )}
          </g>
        );
      })}
      {span && (
        <g pointerEvents="none">
          <rect x={0} y={sy(span.y + span.h)} width={RULER} height={Math.max(1, span.h * view.zoom)} fill={BAND_FILL} />
          <line x1={0} x2={RULER} y1={sy(span.y + span.h)} y2={sy(span.y + span.h)} stroke="var(--color-red)" strokeWidth="1" />
          <line x1={0} x2={RULER} y1={sy(span.y)} y2={sy(span.y)} stroke="var(--color-red)" strokeWidth="1" />
        </g>
      )}
      {cursor && <line x1="0" x2={RULER} y1={sy(cursor.y)} y2={sy(cursor.y)} stroke="var(--color-red)" strokeWidth="1" />}
    </svg>
  );
}

const clampPx = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/** Double-headed arrow across the page's width with its size, in the strip above the ruler. */
export function TopDimension({ width, view, pageW, unit }) {
  const x0 = view.x;
  const x1 = view.x + pageW * view.zoom;
  const y = DIM / 2;
  const label = `${fmt(pageW, unit)} ${unit}`;
  const cx = clampPx((x0 + x1) / 2, x0 + 40, x1 - 40);
  const labelX = clampPx(cx, 40, width - 40);
  return (
    <svg className="ed-dim" width={width} height={DIM}>
      <line x1={clampPx(x0, 0, width)} x2={clampPx(x1, 0, width)} y1={y} y2={y} stroke="var(--color-red)" strokeWidth="1" />
      {x0 >= 0 && x0 <= width && <path d={`M${x0 + ARROW} ${y - 3.5} L${x0} ${y} L${x0 + ARROW} ${y + 3.5} M${x0} 2 V${DIM - 2}`} fill="none" stroke="var(--color-red)" strokeWidth="1" />}
      {x1 >= 0 && x1 <= width && <path d={`M${x1 - ARROW} ${y - 3.5} L${x1} ${y} L${x1 - ARROW} ${y + 3.5} M${x1} 2 V${DIM - 2}`} fill="none" stroke="var(--color-red)" strokeWidth="1" />}
      <rect x={labelX - 36} y={2} width="72" height={DIM - 4} fill={DIM_BG} />
      <text x={labelX} y={y + 4} textAnchor="middle" className="ed-dim-label">{label}</text>
    </svg>
  );
}

/** Same, vertically, left of the vertical ruler. */
export function LeftDimension({ height, view, pageH, unit }) {
  const y0 = view.y;
  const y1 = view.y + pageH * view.zoom;
  const x = DIM / 2;
  const label = `${fmt(pageH, unit)} ${unit}`;
  const cy = clampPx((y0 + y1) / 2, y0 + 40, y1 - 40);
  const labelY = clampPx(cy, 40, height - 40);
  return (
    <svg className="ed-dim" width={DIM} height={height}>
      <line y1={clampPx(y0, 0, height)} y2={clampPx(y1, 0, height)} x1={x} x2={x} stroke="var(--color-red)" strokeWidth="1" />
      {y0 >= 0 && y0 <= height && <path d={`M${x - 3.5} ${y0 + ARROW} L${x} ${y0} L${x + 3.5} ${y0 + ARROW} M2 ${y0} H${DIM - 2}`} fill="none" stroke="var(--color-red)" strokeWidth="1" />}
      {y1 >= 0 && y1 <= height && <path d={`M${x - 3.5} ${y1 - ARROW} L${x} ${y1} L${x + 3.5} ${y1 - ARROW} M2 ${y1} H${DIM - 2}`} fill="none" stroke="var(--color-red)" strokeWidth="1" />}
      <rect x={2} y={labelY - 36} width={DIM - 4} height="72" fill={DIM_BG} />
      <text transform={`translate(${x + 4} ${labelY}) rotate(-90)`} textAnchor="middle" className="ed-dim-label">{label}</text>
    </svg>
  );
}
