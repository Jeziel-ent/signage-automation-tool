import test from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_LINE_EM, fitTransform, textStyle } from "./textFormat.js";

const parse = (tf) => tf.match(/-?\d+(?:\.\d+)?(?:e-?\d+)?/g).map(Number); // [x, top, k, -ax, -ay]

test("no formatting: exactly the old live-text placement (centred, 1.2 em lines)", () => {
  const style = textStyle({});
  assert.equal(style.anchor, "middle");
  assert.equal(style.lineEm, DEFAULT_LINE_EM);
  const bb = { x: -150, y: -80, width: 300, height: 100 };
  const [x, top, k, ax, ay] = parse(fitTransform(bb, { w: 60, h: 20, lines: 1, origLines: 1, fontSize: 100, style }));
  // old: translate(w/2 h/2) scale(k) translate(-cx -cy) - the glyph box centre lands on the box centre
  assert.equal(x, 30);
  assert.equal(k, 0.2);
  assert.equal(ax, 0); // -(bb.x + width/2)
  assert.ok(Math.abs(top + k * (bb.y - bb.y) + k * bb.height / 2 - 10) < 1e-9 && ay === 80);
});

test("styles map to SVG: bold/italic/underline, alignment anchors, spacing", () => {
  const s = textStyle({ align: "right", bold: true, italic: true, underline: true, char_spacing: 100, line_spacing: 150 });
  assert.deepEqual([s.anchor, s.fontWeight, s.fontStyle, s.textDecoration], ["end", "bold", "italic", "underline"]);
  assert.equal(s.letterSpacingEm, 0.25);
  assert.ok(Math.abs(s.lineEm - 1.8) < 1e-9);
  assert.equal(textStyle({ align: "left" }).anchor, "start");
  assert.equal(textStyle({ align: "justify" }).anchor, "start");
  assert.equal(textStyle({ align: "center" }).anchor, "middle");
});

test("alignment anchors the glyph box at the left edge / right edge of the node box", () => {
  const bb = { x: 0, y: -80, width: 300, height: 100 };
  const left = parse(fitTransform(bb, { w: 60, h: 20, lines: 1, origLines: 1, fontSize: 100, style: textStyle({ align: "left" }) }));
  assert.equal(left[0], 0);
  assert.equal(left[3], 0);
  const right = parse(fitTransform(bb, { w: 60, h: 20, lines: 1, origLines: 1, fontSize: 100, style: textStyle({ align: "right" }) }));
  assert.equal(right[0], 60);
  assert.equal(right[3], -300);
});

test("extra line spacing keeps the glyph size and grows the block downwards", () => {
  const plain = { x: 0, y: -80, width: 300, height: 100 + 120 };            // 2 lines at 1.2 em
  const spaced = { ...plain, height: 100 + 240 };                           // same 2 lines at 2.4 em (line_spacing 200)
  const a = parse(fitTransform(plain, { w: 60, h: 40, lines: 2, origLines: 2, fontSize: 100, style: textStyle({}) }));
  const b = parse(fitTransform(spaced, { w: 60, h: 40, lines: 2, origLines: 2, fontSize: 100, style: textStyle({ line_spacing: 200 }) }));
  assert.equal(a[2], b[2]);   // same scale = same glyph size
  assert.equal(a[1], b[1]);   // same top
});
