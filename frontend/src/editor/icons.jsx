// Small inline SVG icons (no icon-font dependency). 16x16 viewBox, currentColor.
const base = { width: 16, height: 16, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round", strokeLinejoin: "round" };

export const Eye = () => (
  <svg {...base}><path d="M1 8s2.5-4.5 7-4.5S15 8 15 8s-2.5 4.5-7 4.5S1 8 1 8z" /><circle cx="8" cy="8" r="2" /></svg>
);
export const EyeOff = () => (
  <svg {...base}><path d="M2 2l12 12M6.5 4a6.6 6.6 0 0 1 1.5-.2C12.5 3.8 15 8 15 8a11 11 0 0 1-2.3 2.8M4 5.2A11 11 0 0 0 1 8s2.5 4.2 7 4.2c1 0 1.9-.2 2.7-.6" /></svg>
);
export const Undo = () => (
  <svg {...base}><path d="M3 6h7a3.5 3.5 0 0 1 0 7H6M3 6l3-3M3 6l3 3" /></svg>
);
export const Redo = () => (
  <svg {...base}><path d="M13 6H6a3.5 3.5 0 0 0 0 7h4M13 6l-3-3M13 6l-3 3" /></svg>
);
export const ZoomIn = () => (
  <svg {...base}><circle cx="7" cy="7" r="4.5" /><path d="M10.5 10.5L14 14M7 5v4M5 7h4" /></svg>
);
export const ZoomOut = () => (
  <svg {...base}><circle cx="7" cy="7" r="4.5" /><path d="M10.5 10.5L14 14M5 7h4" /></svg>
);
export const Fit = () => (
  <svg {...base}><path d="M2 6V2h4M14 6V2h-4M2 10v4h4M14 10v4h-4" /></svg>
);
export const Lock = () => (
  <svg {...base}><rect x="3.5" y="7" width="9" height="6.5" rx="1" /><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" /></svg>
);
export const Caret = ({ open }) => (
  <svg {...base} style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform .1s" }}><path d="M6 3l5 5-5 5" /></svg>
);
export const GroupIcon = () => (
  <svg {...base}><rect x="1.5" y="1.5" width="5" height="5" /><rect x="9.5" y="9.5" width="5" height="5" /><path d="M8 4h4v4M4 8v4h4" strokeDasharray="1.5 1.5" /></svg>
);
export const UngroupIcon = () => (
  <svg {...base}><rect x="1.5" y="1.5" width="5" height="5" /><rect x="9.5" y="9.5" width="5" height="5" /></svg>
);
export const KindIcon = ({ kind, type }) => {
  if (kind === "group") return <GroupIcon />;
  if (kind === "powerclip") return <svg {...base}><rect x="2" y="2" width="12" height="12" rx="2" /><circle cx="8" cy="8" r="3" /></svg>;
  if (type === "text") return <svg {...base}><path d="M3 4V3h10v1M8 3v10M6 13h4" /></svg>;
  if (type === "bitmap") return <svg {...base}><rect x="2" y="3" width="12" height="10" /><circle cx="6" cy="7" r="1.2" /><path d="M2 12l4-3 3 2 2-1.5 3 2.5" /></svg>;
  return <svg {...base}><path d="M2.5 12C4 4 8 3 9.5 7S12 12 13.5 4" /></svg>;
};
