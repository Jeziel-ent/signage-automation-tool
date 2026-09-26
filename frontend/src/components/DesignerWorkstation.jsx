import { useEffect, useState } from "react";

// Isometric illustration for the editor loader: a PC workstation running CorelDRAW, where the artwork on the monitor keeps
// resizing - stretching wide, scaling up, going portrait - on a smooth loop while its layout re-flows to each size (logo + text side by
// side when wide, stacked when tall), with live W/H readouts. No figure or hand: the desk, mat and keyboard are aligned on the monitor
// stand's centre line, and a glow-accented optical mouse on the right of the mat moves EXACTLY with the on-screen cursor (its position is
// a linear map of the cursor's position on the canvas); its red under-glow brightens while the artwork is actively changing.
// Pure SVG driven by one requestAnimationFrame clock; reduced motion shows a
// single still frame. Decorative (aria-hidden) - the loader's status line carries the real progress.

const LOOP_S = 5.2;
// artwork box sizes (screen units) the loop passes through; the last equals the first so the loop is seamless
const KEYS = [
  { w: 64, h: 36 },
  { w: 118, h: 40 },
  { w: 126, h: 70 },
  { w: 62, h: 76 },
  { w: 64, h: 36 },
];
const ACTIONS = ["Stretching to target size...", "Scaling vector nodes...", "Auto-aligning layout...", "Scaling vector nodes..."];
const MM_PER_UNIT = 16; // readout scale: 118 units wide reads 1888 mm

const smooth = (t) => t * t * (3 - 2 * t);

function frameAt(sec) {
  const n = KEYS.length - 1;
  // normalised into [0, LOOP_S): a negative time (see the clock below) must not index KEYS[-1]
  const u = ((((sec % LOOP_S) + LOOP_S) % LOOP_S) / LOOP_S) * n;
  const i = Math.min(n - 1, Math.floor(u));
  // hold briefly at each key, then ease to the next (feels like a drag + release)
  const local = u - i;
  const t = smooth(Math.min(1, Math.max(0, (local - 0.18) / 0.82)));
  const a = KEYS[i];
  const b = KEYS[i + 1];
  return { w: a.w + (b.w - a.w) * t, h: a.h + (b.h - a.h) * t, dragging: t > 0 && t < 1, action: ACTIONS[i] };
}

// the monitor's canvas area (screen coordinates inside the 360 x 270 viewBox)
const CANVAS = { x: 118, y: 47, w: 154, h: 104 };
const CX = CANVAS.x + CANVAS.w / 2;
// the mouse's resting spot on the right of the mat and how far it travels (x, y); mapped from the cursor's own travel over the loop
// (it drags the bottom-right handle, so it spans CX + w/2 and CY + h/2 over the KEYS sizes) - still a linear map, so still exact
const MOUSE_HOME = { x: 252, y: 211 };
const MOUSE_RANGE = { x: 26, y: 8 };
const CURSOR_SPAN = {
  x0: Math.min(...KEYS.map((k) => k.w)) / 2, x1: Math.max(...KEYS.map((k) => k.w)) / 2,
  y0: Math.min(...KEYS.map((k) => k.h)) / 2, y1: Math.max(...KEYS.map((k) => k.h)) / 2,
};
const CY = CANVAS.y + CANVAS.h / 2;

export default function DesignerWorkstation() {
  const [still] = useState(() => typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
  const [sec, setSec] = useState(1.6); // reduced motion: a mid-loop still
  useEffect(() => {
    if (still) return undefined;
    let raf;
    const t0 = performance.now();
    const tick = (now) => {
      setSec(Math.max(0, (now - t0) / 1000)); // the first rAF timestamp can be slightly EARLIER than performance.now() at start
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [still]);

  const { w, h, dragging, action } = frameAt(sec);
  const x = CX - w / 2;
  const y = CY - h / 2;
  const wide = w / h >= 1.6;
  // responsive artwork inside the box: logo + two text lines, re-flowed for wide vs tall
  const pad = Math.min(w, h) * 0.12;
  const logoR = wide ? (h - 2 * pad) * 0.32 : Math.min(w * 0.22, h * 0.16);
  const logoC = wide ? { x: x + pad + logoR, y: y + h / 2 } : { x: x + w / 2, y: y + pad + logoR };
  const textX = wide ? logoC.x + logoR + pad * 0.8 : x + pad;
  const textW = wide ? x + w - pad - textX : w - 2 * pad;
  const textY = wide ? y + h / 2 - h * 0.12 : logoC.y + logoR + pad * 0.9;
  const lineH = Math.max(2.2, Math.min(h, w) * 0.09);
  const handles = [
    [x, y], [x + w / 2, y], [x + w, y],
    [x, y + h / 2], [x + w, y + h / 2],
    [x, y + h], [x + w / 2, y + h], [x + w, y + h],
  ];
  const cursor = { x: x + w + 1.5, y: y + h + 1.5 };
  // the physical mouse is the cursor: its position on its area of the mat is a linear map of the cursor's position on the canvas
  // (left/right -> left/right, up/down -> back/forward, compressed for the isometric desk), so the two always move exactly together
  const mouseX = MOUSE_HOME.x + ((w / 2 - CURSOR_SPAN.x0) / (CURSOR_SPAN.x1 - CURSOR_SPAN.x0) - 0.5) * MOUSE_RANGE.x;
  const mouseY = MOUSE_HOME.y + ((h / 2 - CURSOR_SPAN.y0) / (CURSOR_SPAN.y1 - CURSOR_SPAN.y0) - 0.5) * MOUSE_RANGE.y;
  const mmW = Math.round((w * MM_PER_UNIT) / 10) * 10;
  const mmH = Math.round((h * MM_PER_UNIT) / 10) * 10;

  // the mouse's under-glow brightens while the artwork is actively changing
  const next = frameAt(sec + 0.06);
  const stretch = still ? 0 : Math.min(1, (Math.abs(next.w - w) + Math.abs(next.h - h)) / 3);
  const glow = 0.3 + 0.6 * stretch;

  return (
    <svg className="dw" viewBox="0 0 360 270" aria-hidden="true">
      <defs>
        <radialGradient id="dw-glow-red" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#ef4444" stopOpacity="0.35" />
          <stop offset="100%" stopColor="#ef4444" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="dw-glow-cyan" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#22d3ee" stopOpacity="0.22" />
          <stop offset="100%" stopColor="#22d3ee" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="dw-desk" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#1e293b" />
          <stop offset="100%" stopColor="#111827" />
        </linearGradient>
        <linearGradient id="dw-mat" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#7f1d1d" stopOpacity="0.7" />
          <stop offset="100%" stopColor="#ef4444" stopOpacity="0.45" />
        </linearGradient>
        <linearGradient id="dw-art" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#ef4444" />
          <stop offset="100%" stopColor="#991b1b" />
        </linearGradient>
        <filter id="dw-soft" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="2.2" />
        </filter>
        <pattern id="dw-dots" width="6" height="6" patternUnits="userSpaceOnUse">
          <circle cx="1" cy="1" r="0.6" fill="#334155" />
        </pattern>
        <clipPath id="dw-canvas-clip">
          <rect x={CANVAS.x} y={CANVAS.y} width={CANVAS.w} height={CANVAS.h} />
        </clipPath>
      </defs>

      {/* ambient light behind the monitor */}
      <ellipse cx="190" cy="98" rx="150" ry="100" fill="url(#dw-glow-red)" className="dw-breathe" />
      <ellipse cx="230" cy="80" rx="80" ry="60" fill="url(#dw-glow-cyan)" />

      {/* desk: isometric top + front face, glowing mat */}
      <polygon points="38,200 310,200 342,226 70,226" fill="url(#dw-desk)" stroke="#334155" strokeWidth="1" />
      <polygon points="70,226 342,226 342,236 70,236" fill="#0b1220" />
      <polygon points="97,203 283,203 297,219 111,219" fill="url(#dw-mat)" className="dw-mat" />

      {/* monitor stand */}
      <rect x="183" y="166" width="14" height="30" rx="2" fill="#334155" />
      <ellipse cx="190" cy="199" rx="30" ry="4.5" fill="#475569" />

      {/* monitor */}
      <rect x="100" y="24" width="180" height="146" rx="9" fill="#0b1220" stroke="#475569" strokeWidth="1.5" />
      <rect x="104" y="28" width="172" height="138" rx="6" fill="#0f172a" />
      {/* CorelDRAW-like title bar */}
      <rect x="104" y="28" width="172" height="11" rx="6" fill="#111827" />
      <circle cx="111" cy="33.5" r="2" fill="#ef4444" />
      <text x="117" y="35.6" className="dw-title">CorelDRAW · master.cdr</text>
      <circle cx="262" cy="33.5" r="1.6" fill="#334155" />
      <circle cx="268" cy="33.5" r="1.6" fill="#334155" />
      {/* toolbox */}
      <rect x="104" y="41" width="11" height="115" fill="#0b1220" />
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <rect key={i} x="106.5" y={45 + i * 10} width="6" height="6" rx="1.2" fill={i === 1 ? "#ef4444" : "#334155"} />
      ))}
      {/* canvas */}
      <rect x={CANVAS.x} y={CANVAS.y} width={CANVAS.w} height={CANVAS.h} fill="#12161f" />
      <rect x={CANVAS.x} y={CANVAS.y} width={CANVAS.w} height={CANVAS.h} fill="url(#dw-dots)" />
      <g clipPath="url(#dw-canvas-clip)">
        {/* centre alignment guides while dragging */}
        {dragging && (
          <g className="dw-guides">
            <line x1={CX} y1={CANVAS.y} x2={CX} y2={CANVAS.y + CANVAS.h} />
            <line x1={CANVAS.x} y1={CY} x2={CANVAS.x + CANVAS.w} y2={CY} />
          </g>
        )}
        {/* the artwork: a small signboard that re-flows as it resizes */}
        <rect x={x} y={y} width={w} height={h} rx="2" fill="url(#dw-art)" />
        <circle cx={logoC.x} cy={logoC.y} r={logoR} fill="#fff" />
        <circle cx={logoC.x} cy={logoC.y} r={logoR * 0.45} fill="#ef4444" />
        <rect x={textX} y={textY} width={Math.max(4, textW)} height={lineH} rx={lineH / 2} fill="#fff" />
        <rect x={textX} y={textY + lineH * 1.9} width={Math.max(3, textW * 0.62)} height={lineH * 0.75} rx={lineH / 2} fill="#fecaca" />
        {/* selection: dashed cyan bounding box + 8 scale handles (the dragged corner highlighted) */}
        <rect x={x - 2} y={y - 2} width={w + 4} height={h + 4} fill="none" stroke="#22d3ee" strokeWidth="0.8" strokeDasharray="3 2" />
        {handles.map(([hx, hy], i) => (
          <rect key={i} x={hx - 2.4} y={hy - 2.4} width="4.8" height="4.8" fill={i === 7 && dragging ? "#22d3ee" : "#fff"} stroke="#ef4444" strokeWidth="0.8" />
        ))}
        {/* cursor on the dragged handle */}
        <path d={`M${cursor.x} ${cursor.y} l0 9 l2.6 -2.4 l1.8 4 l1.6 -0.8 l-1.8 -3.9 l3.6 -0.2 z`} fill="#fff" stroke="#0f172a" strokeWidth="0.6" />
      </g>
      {/* status bar */}
      <rect x="104" y="156" width="172" height="10" fill="#0b1220" />
      <text x="119" y="162.8" className="dw-status ok">Transform: {dragging ? "resizing" : "idle"}</text>
      <text x="272" y="162.8" textAnchor="end" className="dw-status">{mmW} × {mmH} mm</text>

      {/* keyboard, directly in front of the monitor stand */}
      <polygon points="156,205 224,205 230,214 162,214" fill="#111827" stroke="#334155" strokeWidth="0.8" />
      <polygon points="158.5,206.3 222.4,206.3 227.2,212.7 163.3,212.7" fill="#0d1117" />
      {[0, 1, 2].map((r) => (
        <line key={r} x1={161 + r * 1.6} y1={207.8 + r * 1.9} x2={222 + r * 1.6} y2={207.8 + r * 1.9} stroke="#475569" strokeWidth="0.7" strokeDasharray="2.6 1.2" />
      ))}

      {/* optical mouse on the right of the mat, moving exactly with the on-screen cursor; its cable runs to the back of the desk */}
      <path d={`M${mouseX.toFixed(2)} ${(mouseY - 5.4).toFixed(2)} C${(mouseX + 2).toFixed(2)} ${(mouseY - 11).toFixed(2)} 282 198 300 199`} stroke="#1e293b" strokeWidth="1" fill="none" />
      <g transform={`translate(${mouseX.toFixed(2)} ${mouseY.toFixed(2)})`}>
        <ellipse cx="0" cy="1" rx="10" ry="5.2" fill="#ef4444" opacity={0.2 + 0.35 * glow} filter="url(#dw-soft)" />
        <g transform="scale(0.8 0.64)">
          <path d="M0 -8.5 C5.2 -8.5 7.2 -4 7.2 1 C7.2 6.2 4.2 8.8 0 8.8 C-4.2 8.8 -7.2 6.2 -7.2 1 C-7.2 -4 -5.2 -8.5 0 -8.5 Z" fill="#111827" stroke="#475569" strokeWidth="0.9" />
          <line x1="0" y1="-8.4" x2="0" y2="-2.2" stroke="#334155" strokeWidth="0.7" />
          <path d="M-7 -1.4 C-4.5 -2.4 4.5 -2.4 7 -1.4" stroke="#334155" strokeWidth="0.6" fill="none" />
          <rect x="-0.95" y="-7" width="1.9" height="3.6" rx="0.95" fill="#ef4444" className="dw-wheel" />
          <path d="M-6.5 2.5 C-6 -3.4 -3.6 -7 0 -7.8" stroke="#38bdf8" strokeWidth="0.8" strokeLinecap="round" fill="none" opacity="0.7" />
        </g>
      </g>

      {/* floating dimension markers + action pill */}
      <g className="dw-marker">
        <rect x="116" y="6" width="58" height="13" rx="6.5" />
        <text x="145" y="15" textAnchor="middle">W: {mmW} mm</text>
      </g>
      <g className="dw-marker">
        <rect x="286" y="60" width="58" height="13" rx="6.5" />
        <text x="315" y="69" textAnchor="middle">H: {mmH} mm</text>
      </g>
      <g className="dw-action">
        <rect x="4" y="150" width="106" height="14" rx="7" />
        <circle cx="12" cy="157" r="2.2" className="dw-action-dot" />
        <text x="18" y="159.4">{action}</text>
      </g>
    </svg>
  );
}
