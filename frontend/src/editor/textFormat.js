// CorelDRAW's paragraph formatting (node.text: align, bold, italic, underline, line_spacing % of character height, char_spacing % of a
// space) in SVG terms. A space is ~0.25 em wide and CorelDRAW's 100% line spacing is ~1.2 em baseline to baseline - close enough for a
// preview; Save and Generate renders the real thing. With none of these set the text is drawn exactly as before (centred, 1.2 em).
export const DEFAULT_LINE_EM = 1.2;
export function textStyle(t = {}) {
  const align = t.align === "right" ? "end" : t.align === "center" ? "middle" : t.align ? "start" : "middle"; // justify: start
  return {
    anchor: align,
    fontWeight: t.bold ? "bold" : undefined,
    fontStyle: t.italic ? "italic" : undefined,
    textDecoration: t.underline ? "underline" : undefined,
    letterSpacingEm: t.char_spacing ? (t.char_spacing / 100) * 0.25 : 0,
    lineEm: DEFAULT_LINE_EM * ((t.line_spacing || 100) / 100),
  };
}

/** Where the measured glyph box goes in the node box: horizontally by alignment (left edge / centre / right edge, like CorelDRAW
 *  re-anchors artistic text), vertically centred on the box as it would be at default line spacing, so extra spacing grows the text
 *  downwards (the export keeps the top). `k` scales one line of the new text to one line of the original. */
export function fitTransform(bb, { w, h, lines, origLines, fontSize, style }) {
  const extra = (lines - 1) * fontSize * (style.lineEm - DEFAULT_LINE_EM); // spacing beyond the default, in measuring units
  const plainH = Math.max(1e-6, bb.height - extra);
  const k = (h / Math.max(1, origLines)) / (plainH / lines);
  const top = h / 2 - (k * plainH) / 2;
  const [x, ax] = style.anchor === "start" ? [0, bb.x] : style.anchor === "end" ? [w, bb.x + bb.width] : [w / 2, bb.x + bb.width / 2];
  return `translate(${x} ${top}) scale(${k}) translate(${-ax} ${-bb.y})`;
}
