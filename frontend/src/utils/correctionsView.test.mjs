import test from "node:test";
import assert from "node:assert/strict";
import { describeShift, describeStyle, diffRects, intelAllTitle, learnedCount, nothingLearnedNotice, pageLabel, pickSelection, sparkleTitle } from "./correctionsView.js";

test("diffRects flips y (the scene's origin is bottom-left) and scales to the drawing", () => {
  const change = { before: { cx: 0.25, cy: 0.25, w: 0.2, h: 0.2 }, after: { cx: 0.5, cy: 0.75, w: 0.2, h: 0.2 } };
  const r = diffRects(change, 1000, 500, 400);
  assert.equal(r.height, 200);
  assert.deepEqual(r.before, { x: 60, y: 130, w: 80, h: 40 });
  assert.deepEqual(r.after, { x: 160, y: 30, w: 80, h: 40 });
});

test("diffRects has no after rectangle for a hidden object", () => {
  const r = diffRects({ before: { cx: 0.5, cy: 0.5, w: 0.1, h: 0.1 }, after: null }, 100, 100);
  assert.equal(r.after, null);
  assert.ok(r.before);
});

test("describeShift reads like a designer", () => {
  assert.equal(describeShift({ dx_mm: 110, dy_mm: -40, dw_mm: 0, dh_mm: 0 }), "110 mm right, 40 mm down");
  assert.equal(describeShift({ dx_mm: -5, dy_mm: 0.2, dw_mm: 30, dh_mm: -12 }), "5 mm left, wider by 30 mm, shorter by 12 mm");
  assert.equal(describeShift({ dx_mm: 0, dy_mm: 0, dw_mm: 0, dh_mm: 0 }), "");
  assert.equal(describeShift(null), "");
});

test("pageLabel shows inches", () => {
  assert.equal(pageLabel(3657.6, 1524), "144 x 60 in");
});

test("selection keeps the current record or falls back to the first", () => {
  const rs = [{ id: "a" }, { id: "b" }];
  assert.equal(pickSelection(rs, "b"), "b");
  assert.equal(pickSelection(rs, "gone"), "a");
  assert.equal(pickSelection([], "a"), null);
});

test("describeStyle reads the text style a designer set", () => {
  assert.equal(describeStyle({ bold: true, line_spacing: 80 }), "bold, line spacing 80%");
  assert.equal(describeStyle({ italic: false, align: "center", size_pt: 59.6 }), "not italic, center aligned, size 60 pt");
  assert.equal(describeStyle(undefined), "");
});

test("learnedCount adds the top-level and the nested edits", () => {
  assert.equal(learnedCount({ applied: 2, nested: { applied: 3 } }), 5);
  assert.equal(learnedCount({ applied: 2 }), 2);
  assert.equal(learnedCount(undefined), 0);
});

test("the sparkle wording is singular / plural and says what is missing", () => {
  assert.match(nothingLearnedNotice(), /nothing learned/);
  assert.match(intelAllTitle(0), /Nothing learned/);
  assert.match(intelAllTitle(1), /Re-run 1 converted board with/);
  assert.match(intelAllTitle(4), /Re-run 4 converted boards with/);
  assert.match(sparkleTitle(0), /nothing learned/);
  assert.match(sparkleTitle(1), /1 learned designer correction$/);
  assert.match(sparkleTitle(2), /2 learned designer corrections$/);
});
