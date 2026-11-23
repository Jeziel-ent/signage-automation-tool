import test from "node:test";
import assert from "node:assert/strict";
import { opsKey, renderOverrides, textsToRender } from "./renderPlan.js";

const node = (id, extra = {}) => ({ id, kind: "shape", type: "text", x: 0, y: 0, w: 10, h: 5, visible: true, image: { file: id + ".png" }, text: { content: "A" }, ...extra });
const scene = (...nodes) => ({ layers: [{ id: "L1", children: nodes }] });

test("textsToRender picks stale or resized text, not untouched text, groups or hidden text", () => {
  const base = scene(node("a"), node("b"), node("c"), node("d"));
  const now = scene(node("a", { stale: true }), node("b", { w: 20 }), node("c"), node("d", { stale: true, visible: false }));
  assert.deepEqual(textsToRender(now, base), ["a", "b"]);
  assert.deepEqual(textsToRender(null, base), []);
});

test("textsToRender skips non-text nodes and caps the list", () => {
  const many = scene(...Array.from({ length: 60 }, (_, i) => node("t" + i, { stale: true })), { id: "g", kind: "group", children: [], image: null });
  assert.equal(textsToRender(many, many).length, 40);
});

test("renderOverrides applies only to the edits the render was made for", () => {
  const key = opsKey([{ op: "text" }]);
  const state = { key, exportId: "E1", files: { s1: { file: "s1.png" } } };
  assert.deepEqual(renderOverrides(state, key, "/api/r/"), { s1: "/api/r/E1/s1.png" });
  assert.equal(renderOverrides(state, opsKey([]), "/api/r/"), null);
  assert.equal(renderOverrides(null, key, "/api/r/"), null);
  assert.equal(renderOverrides({ key, exportId: "E1", files: {} }, key, "/api/r/"), null);
});
