// Corel Intelligence screen helpers: how a stored correction is drawn and described (the server sends page fractions + mm shifts).

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

/** The first record to show after a list refresh: keep the selection if it is still there, else the first one. */
export function pickSelection(records, currentId) {
  return records.find((r) => r.id === currentId)?.id ?? records[0]?.id ?? null;
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

const plural = (n, one, many) => (n === 1 ? one : many);

/** The queue notice when the re-run button is clicked but nothing learned fits the board. */
export function nothingLearnedNotice() {
  return "Corel Intelligence has nothing learned for this board size yet. Correct a board of this size in the editor first.";
}

/** Tooltip of the footer "Corel Intelligence (N)" button. */
export function intelAllTitle(n) {
  if (!n) return "Nothing learned yet for the converted boards - correct a board in the editor first";
  return `Re-run ${n} converted board${plural(n, "", "s")} with what designers corrected on the same size`;
}

/** Tooltip of a row's re-run button. */
export function sparkleTitle(n) {
  if (!n) return "Corel Intelligence: nothing learned for this board size yet";
  return `Use Corel Intelligence: re-run this board with ${n} learned designer correction${plural(n, "", "s")}`;
}

/** The accuracy strip of the Corel Intelligence page, from GET /api/v2/intelligence/stats: how much designers still moved things (average, in % of
 *  the page) on boards made without and with Corel Intelligence, the work it saved, and how many stored lessons the designers kept. */
export function accuracyCards(s) {
  if (!s?.with || !s?.without) return [];
  const pct = (v) => (v == null ? "-" : `${v}%`);
  const boards = (n) => (n ? `${n} saved board${plural(n, "", "s")}` : "no saved boards yet");
  const { kept, corrected, paused } = s.lessons || { kept: 0, corrected: 0, paused: 0 };
  const judged = kept + corrected;
  let gainNote = "needs saved boards made with and without it";
  if (s.improvement_pct != null) gainNote = s.improvement_pct >= 0 ? "less moving by hand than without it" : "more moving by hand - check the lessons";
  return [
    { key: "without", label: "Without Corel Intelligence", value: pct(s.without.avg_cost), note: `average edit per board - ${boards(s.without.boards)}` },
    { key: "with", label: "With Corel Intelligence", value: pct(s.with.avg_cost), note: `average edit per board - ${boards(s.with.boards)}` },
    { key: "gain", label: "Work saved", value: pct(s.improvement_pct), note: gainNote },
    { key: "lessons", label: "Lessons kept", value: judged ? `${Math.round((kept / judged) * 100)}%` : "-",
      note: judged ? `${kept} kept, ${corrected} corrected again${paused ? `, ${paused} paused` : ""}` : "no board has judged a lesson yet" },
  ];
}

/** The hover text of a row's "Learned (n)" badge: what Corel Intelligence applied to the board and what it held back or undid
 *  (`report.layout.intelligence`: applied, nested.applied, family, identity, held_back, gated, avg_confidence). */
export function learnedTitle(intelligence) {
  const i = intelligence || {};
  const n = learnedCount(i);
  const parts = [`${n} learned change${plural(n, "", "s")} applied${i.auto ? " automatically" : ""}`];
  if (i.family) parts.push(`${i.family} from other sizes of this master`);
  if (i.avg_confidence) parts.push(`average confidence ${Math.round(i.avg_confidence * 100)}%`);
  if (i.held_back) parts.push(`${i.held_back} held back (not sure enough)`);
  if (i.gated) parts.push(`${i.gated} undone (would leave the page or cover another object)`);
  return parts.join(" - ");
}

/** The sentence under the "apply automatically" switch on the Corel Intelligence page (`GET /api/v2/intelligence/settings`). */
export function autoApplyNote(s) {
  if (!s) return "";
  const pct = (v) => `${Math.round((v ?? 0) * 100)}%`;
  return s.auto_apply
    ? `On: every Convert applies the learned changes Corel Intelligence is at least ${pct(s.auto_min_confidence)} sure about. The button on a row still applies everything down to ${pct(s.button_min_confidence)}.`
    : `Off: learned changes are only applied when you press the Corel Intelligence button on a row (or the footer). Turn on to apply the ones it is at least ${pct(s.auto_min_confidence)} sure about on every Convert.`;
}
