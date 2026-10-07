// Corel Intelligence review screen helpers: how a stored correction is drawn and described (the server sends page fractions + mm shifts).

export const STATUS_LABEL = { pending: "Pending review", approved: "Approved", rejected: "Rejected" };
export const ACTION_LABEL = {
  moved: "Moved", resized: "Resized", "moved+resized": "Moved and resized", hidden: "Hidden", deleted: "Deleted", styled: "Restyled",
};

/** A fraction box (centre + size, origin bottom-left) as an SVG rect in a `width`-wide drawing of the page. */
function rectOf(box, width, height) {
  if (!box) return null;
  return { x: (box.cx - box.w / 2) * width, y: (1 - box.cy - box.h / 2) * height, w: box.w * width, h: box.h * height };
}

/** Drawing geometry of one change: the page size in the drawing and the before / after rectangles (null when there is none). */
export function diffRects(change, pageW, pageH, width = 360) {
  const height = Math.round((width * pageH) / pageW);
  return { width, height, before: rectOf(change.before, width, height), after: rectOf(change.after, width, height) };
}

function dirWord(v, pos, neg) {
  return `${Math.abs(v)} mm ${v >= 0 ? pos : neg}`;
}

/** "110 mm right, 40 mm down" / "wider by 30 mm" - what a designer would say about the change; "" when nothing measurable. */
export function describeShift(shift) {
  if (!shift) return "";
  const parts = [];
  if (Math.abs(shift.dx_mm) >= 0.5) parts.push(dirWord(shift.dx_mm, "right", "left"));
  if (Math.abs(shift.dy_mm) >= 0.5) parts.push(dirWord(shift.dy_mm, "up", "down"));
  if (Math.abs(shift.dw_mm) >= 0.5) parts.push(`${shift.dw_mm > 0 ? "wider" : "narrower"} by ${Math.abs(shift.dw_mm)} mm`);
  if (Math.abs(shift.dh_mm) >= 0.5) parts.push(`${shift.dh_mm > 0 ? "taller" : "shorter"} by ${Math.abs(shift.dh_mm)} mm`);
  return parts.join(", ");
}

/** "144 x 60 in" for a page given in mm. */
export function pageLabel(wMm, hMm) {
  const inch = (v) => String(Math.round((v / 25.4) * 10) / 10);
  return `${inch(wMm)} x ${inch(hMm)} in`;
}

/** Which of the review list's tabs a record belongs under ("all" shows everything). */
export function inTab(record, tab) {
  return tab === "all" || record.status === tab;
}

/** The first record to show after a list refresh: keep the selection if it is still in the tab, else the first one. */
export function pickSelection(records, tab, currentId) {
  const visible = records.filter((r) => inTab(r, tab));
  return visible.find((r) => r.id === currentId)?.id ?? visible[0]?.id ?? null;
}

const STYLE_LABEL = { bold: "bold", italic: "italic", underline: "underline" };

/** "bold, line spacing 80%, font Arial" for the text style a designer set (the text itself is never learned); "" when there is none. */
export function describeStyle(style) {
  if (!style) return "";
  const parts = [];
  for (const [k, v] of Object.entries(style)) {
    if (k in STYLE_LABEL) parts.push(v ? STYLE_LABEL[k] : `not ${STYLE_LABEL[k]}`);
    else if (k === "line_spacing") parts.push(`line spacing ${v}%`);
    else if (k === "char_spacing") parts.push(`character spacing ${v}%`);
    else if (k === "size_pt") parts.push(`size ${Math.round(v)} pt`);
    else if (k === "align") parts.push(`${v} aligned`);
    else if (k === "font") parts.push(`font ${v}`);
  }
  return parts.join(", ");
}

/** What the learned edits of one board add up to: the ones placed during the conversion plus the ones replayed as editor edits. */
export function learnedCount(intelligence) {
  return (intelligence?.applied ?? 0) + (intelligence?.nested?.applied ?? 0);
}
