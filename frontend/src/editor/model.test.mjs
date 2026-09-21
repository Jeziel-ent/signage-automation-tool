import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { applyOps, buildIndex, findNode } from "./ops.js";
import { flattenLeaves, resolveTarget, hitTest, buildRows, planDrop, marqueeSelect, cloneWithNewIds, unionBox, snapTargets, snapMove, snapResize, nearest, livePowerclip, renderItems, planNodes, dragTargets } from "./model.js";
import { fmt, toUnit, fromUnit, niceStep, rulerTicks } from "./units.js";

const here = dirname(fileURLToPath(import.meta.url));
const GOLDEN = JSON.parse(readFileSync(resolve(here, "../../../backend/tests/fixtures/ops_golden.json"), "utf8"));
const BASE = GOLDEN.base;

test("flattenLeaves: draw order, PowerClip is one leaf, hidden things are skipped", () => {
  assert.deepEqual(flattenLeaves(BASE).map((n) => n.id), ["a", "b", "c", "t", "pc"]);
  const hiddenChild = applyOps(BASE, [{ op: "visibility", id: "b", visible: false }]);
  assert.deepEqual(flattenLeaves(hiddenChild).map((n) => n.id), ["a", "c", "t", "pc"]);
  const hiddenGroup = applyOps(BASE, [{ op: "visibility", id: "g", visible: false }]);
  assert.deepEqual(flattenLeaves(hiddenGroup).map((n) => n.id), ["a", "t", "pc"]);
  const hiddenLayer = applyOps(BASE, [{ op: "visibility", id: "L1", visible: false }]);
  assert.deepEqual(flattenLeaves(hiddenLayer), []);
});

test("resolveTarget: a click on a group's child selects the group; inside the group it selects the child", () => {
  const idx = buildIndex(BASE);
  assert.deepEqual(resolveTarget(idx, "b", null), { targetId: "g", ctx: null });
  assert.deepEqual(resolveTarget(idx, "b", "g"), { targetId: "b", ctx: "g" });
  // clicking outside the entered group leaves it and selects the top-level object
  assert.deepEqual(resolveTarget(idx, "a", "g"), { targetId: "a", ctx: null });
  assert.deepEqual(resolveTarget(idx, "a", null), { targetId: "a", ctx: null });
});

test("hitTest: topmost first, locked skipped, transparent pixels fall through", () => {
  const leaves = flattenLeaves(BASE);
  const opaque = () => 255;
  assert.equal(hitTest(leaves, 30, 20, 0, opaque).id, "b");      // b is above a
  assert.equal(hitTest(leaves, 95, 95, 0, opaque).id, "a");
  assert.equal(hitTest(leaves, 150, 50, 0, opaque), null);       // off every shape
  const seeThroughB = (n) => (n.id === "b" ? 0 : 255);
  assert.equal(hitTest(leaves, 30, 20, 0, seeThroughB).id, "a"); // transparent b -> falls to a
  assert.equal(hitTest(leaves, 30, 20, 0, () => null).id, "b");  // not loaded yet -> bbox hit
  const locked = applyOps(BASE, []);
  locked.layers[0].children[0].locked = true;
  assert.equal(hitTest(flattenLeaves(locked), 95, 95, 0, opaque), null);
  // tolerance widens thin shapes
  assert.equal(hitTest(leaves, 100.5, 95, 1, opaque).id, "a");
});

test("hitTest maps the point into image space with v growing downward", () => {
  const leaves = [{ id: "s", x: 0, y: 0, w: 10, h: 10, kind: "shape" }];
  let seen;
  hitTest(leaves, 2, 9, 0, (n, u, v) => {
    seen = [u, v];
    return 255;
  });
  assert.ok(Math.abs(seen[0] - 0.2) < 1e-9 && Math.abs(seen[1] - 0.1) < 1e-9);
});

test("buildRows lists the stack top-first and respects expansion", () => {
  const collapsed = buildRows(BASE, new Set());
  assert.deepEqual(collapsed.map((r) => r.id), ["L1"]);
  const rows = buildRows(BASE, new Set(["L1", "g"]));
  assert.deepEqual(rows.map((r) => [r.id, r.depth]), [["L1", 0], ["pc", 1], ["t", 1], ["g", 1], ["c", 2], ["b", 2], ["a", 1]]);
});

test("planDrop: index semantics match scene_ops.reorder", () => {
  // drop t above g -> t sits directly above g in the layer
  let plan = planDrop(BASE, "t", "g", "above");
  assert.deepEqual(plan, { op: "reorder", id: "t", parent: "L1", index: 2 });
  let out = applyOps(BASE, [plan]);
  assert.deepEqual(out.layers[0].children.map((n) => n.id), ["a", "g", "t", "pc"]);
  // drop pc below a -> pc is at the very bottom
  plan = planDrop(BASE, "pc", "a", "below");
  out = applyOps(BASE, [plan]);
  assert.deepEqual(out.layers[0].children.map((n) => n.id), ["pc", "a", "g", "t"]);
  // drop a into group g -> top of g's children
  plan = planDrop(BASE, "a", "g", "into");
  assert.deepEqual(plan, { op: "reorder", id: "a", parent: "g", index: 2 });
  out = applyOps(BASE, [plan]);
  assert.deepEqual(out.layers[0].children.find((n) => n.id === "g").children.map((n) => n.id), ["b", "c", "a"]);
  // reorder inside a group: drop c below b
  plan = planDrop(BASE, "c", "b", "below");
  out = applyOps(BASE, [plan]);
  assert.deepEqual(out.layers[0].children.find((n) => n.id === "g").children.map((n) => n.id), ["c", "b"]);
});

test("planDrop refuses illegal drops", () => {
  assert.equal(planDrop(BASE, "g", "g", "into"), null);
  assert.equal(planDrop(BASE, "g", "b", "above"), null); // would put g inside itself
  assert.equal(planDrop(BASE, "a", "pc", "into"), null);  // PowerClip is not a container in v1
  assert.equal(planDrop(BASE, "a", "L1", "above"), null); // layers are not reorderable
  const nested = applyOps(BASE, [{ op: "group", ids: ["g", "t"], group_id: "G2" }]);
  assert.equal(planDrop(nested, "G2", "g", "into"), null); // into its own descendant
});

test("marqueeSelect picks fully-enclosed objects in the current context", () => {
  assert.deepEqual(marqueeSelect(BASE, null, { x: 5, y: 5, w: 90, h: 40 }), ["g"]);
  assert.deepEqual(marqueeSelect(BASE, null, { x: -1, y: -1, w: 300, h: 300 }).sort(), ["a", "g", "pc", "t"]);
  assert.deepEqual(marqueeSelect(BASE, "g", { x: 0, y: 0, w: 55, h: 100 }), ["b"]);
});

test("cloneWithNewIds gives every node a fresh id and remembers the source", () => {
  let n = 0;
  const c = cloneWithNewIds(BASE.layers[0].children[1], () => `n${++n}`);
  assert.deepEqual([c.id, c.children.map((k) => k.id)], ["n1", ["n2", "n3"]]);
  assert.deepEqual([c.src, c.children.map((k) => k.src)], ["g", ["b", "c"]]);
  const pasted = applyOps(BASE, [{ op: "paste", parent: "L1", nodes: [c] }]);
  assert.equal(pasted.layers[0].children.at(-1).id, "n1");
  assert.deepEqual(unionBox([{ x: 0, y: 0, w: 5, h: 5 }, { x: 10, y: -2, w: 5, h: 5 }]), { x: 0, y: -2, w: 15, h: 7 });
});

test("units: conversions and formatting", () => {
  assert.equal(toUnit(3048, "in"), 120);
  assert.equal(toUnit(1219.2, "ft"), 4);
  assert.ok(Math.abs(fromUnit(4, "ft") - 1219.2) < 1e-9);
  assert.equal(fmt(3048, "in"), "120");
  assert.equal(fmt(1219.2, "in"), "48");
  assert.equal(fmt(3048, "mm"), "3048");
  assert.equal(fmt(12.5, "mm"), "12.5");
});

test("rulerTicks: labelled majors at nice steps, minors only when there is room", () => {
  const t = rulerTicks(0, 3048, 0.3, "in"); // 0.3 px/mm ~ 7.6 px/in -> big steps
  const majors = t.filter((k) => k.major);
  assert.ok(majors.length >= 2 && majors.every((k) => k.label !== null));
  const stepPx = (majors[1].mm - majors[0].mm) * 0.3;
  assert.ok(stepPx >= 64, `major spacing ${stepPx}px`);
  assert.equal(niceStep(10, 64), 10); // 64px / 10 px-per-unit = 6.4 -> next nice value is 10
  const fine = rulerTicks(0, 100, 20, "mm");
  assert.ok(fine.some((k) => !k.major), "minor ticks appear once zoomed in");
  assert.ok(rulerTicks(0, 3048, 0.05, "mm").length < 100, "zoomed-out rulers stay sparse");
});

test("planDrop for layers: only above/below another layer, index semantics match layer_order", () => {
  const S = GOLDEN.base2; // L1 (bottom), L2 (top)
  let plan = planDrop(S, "L1", "L2", "above"); // L1 dropped above L2 -> becomes the top layer
  assert.deepEqual(plan, { op: "layer_order", id: "L1", index: 1 });
  assert.deepEqual(applyOps(S, [plan]).layers.map((l) => l.id), ["L2", "L1"]);
  plan = planDrop(S, "L2", "L1", "below");
  assert.deepEqual(applyOps(S, [plan]).layers.map((l) => l.id), ["L2", "L1"]);
  assert.equal(planDrop(S, "L1", "L2", "into"), null);
  assert.equal(planDrop(S, "L1", "b", "above"), null); // a layer cannot be dropped among objects
  assert.equal(planDrop(S, "b", "L1", "above"), null); // objects go INTO layers, not next to them
  assert.deepEqual(planDrop(S, "b", "L1", "into"), { op: "reorder", id: "b", parent: "L1", index: 1 });
});

test("snapTargets / nearest / snapMove", () => {
  const t = snapTargets({ width: 200, height: 100 }, [{ x: 50, y: 20, w: 10, h: 10 }, { x: 5, y: 5, w: 1, h: 1, visible: false }]);
  assert.deepEqual(t.xs, [0, 100, 200, 50, 55, 60]); // hidden objects are not snap targets
  assert.deepEqual(nearest([10, 20], [12, 40], 3), { d: 2, line: 12 });
  assert.equal(nearest([10], [30], 3), null);
  let s = snapMove({ x: 48.5, y: 70, w: 10, h: 10 }, t, 3); // left edge 1.5 short of x=50
  assert.equal(s.dx, 1.5);
  assert.equal(s.guideX, 50);
  s = snapMove({ x: 94, y: 40, w: 10, h: 20 }, t, 3); // centre 99 -> page centre 100
  assert.equal(s.dx, 1);
  assert.equal(s.guideX, 100);
  s = snapMove({ x: 120, y: 60, w: 10, h: 10 }, t, 2); // nothing close
  assert.deepEqual([s.dx, s.dy, s.guideX, s.guideY], [0, 0, null, null]);
});

test("snapResize moves only the dragged edges and leaves the opposite side fixed", () => {
  const t = { xs: [0, 100, 200], ys: [0, 50, 100] };
  let r = snapResize({ x: 20, y: 10, w: 77, h: 30 }, "e", t, 5); // right edge 97 -> 100
  assert.deepEqual(r.box, { x: 20, y: 10, w: 80, h: 30 });
  assert.equal(r.guideX, 100);
  r = snapResize({ x: 3, y: 10, w: 50, h: 30 }, "w", t, 5); // left edge 3 -> 0, right edge stays at 53
  assert.deepEqual(r.box, { x: 0, y: 10, w: 53, h: 30 });
  r = snapResize({ x: 20, y: 10, w: 50, h: 38 }, "n", t, 5); // top edge 48 -> 50
  assert.deepEqual(r.box, { x: 20, y: 10, w: 50, h: 40 });
  r = snapResize({ x: 20, y: 2, w: 50, h: 30 }, "s", t, 5); // bottom edge 2 -> 0, top stays at 32
  assert.deepEqual(r.box, { x: 20, y: 0, w: 50, h: 32 });
  r = snapResize({ x: 20, y: 10, w: 50, h: 30 }, "e", t, 1); // nothing in range
  assert.deepEqual([r.box.w, r.guideX], [50, null]);
});

import { resolveRaster, missingFonts, fmtBytes } from "./exportMath.js";

test("export size preview matches the server's limits (same cases as test_export_replay.py)", () => {
  let r = resolveRaster(3048, 1219.2, { mode: "max_px", max_px: 4000 });
  assert.deepEqual([r.w_px, r.h_px, r.megapixels, r.error], [4000, 1600, 6.4, undefined]);
  assert.equal(resolveRaster(3048, 1219.2, { mode: "dpi", dpi: 150 }).w_px, 18000);
  assert.match(resolveRaster(3048, 1219.2, { mode: "dpi", dpi: 300 }).error, /too large/);       // 36000 px
  assert.match(resolveRaster(2000, 2000, { mode: "dpi", dpi: 250 }).error, /megapixels/);
  assert.match(resolveRaster(100, 100, { mode: "max_px", max_px: 8 }).error, /at least 16/);
  r = resolveRaster(2286, 762, { mode: "max_px", max_px: 600 });                                // the low-dpi case checked live
  assert.deepEqual([r.w_px, r.h_px], [600, 200]);
});

test("missingFonts lists only edited text in fonts that are not installed", () => {
  const scene = { layers: [{ children: [
    { id: "a", stale: true, text: { font: "Noto Sans Tamil" } },
    { id: "b", stale: true, text: { font: "arial" } },                       // installed (case-insensitive)
    { id: "c", text: { font: "Noto Sans Tamil" } },                          // not edited: not this export's problem
    { id: "g", children: [{ id: "d", stale: true, text: { font: "Noto Sans Tamil" } }] },
  ] }] };
  assert.deepEqual(missingFonts(scene, { available: true, fonts: ["Arial"] }), [{ font: "Noto Sans Tamil", count: 2 }]);
  assert.deepEqual(missingFonts(scene, { available: false, fonts: [] }), []);      // no list: no false alarms
  assert.equal(fmtBytes(121267), "121 KB");
  assert.equal(fmtBytes(13607467), "13.6 MB");
});

// ---------------------------------------------------------------- live PowerClips

const img = (id, x, y, w, h, extra = {}) => ({ id, kind: "shape", type: "curve", x, y, w, h, visible: true, locked: false, image: { file: id + ".png" }, ...extra });
const pcScene = (frameRect = true, kidExtra = {}) => ({
  page: { width: 200, height: 100 },
  layers: [{
    id: "L", visible: true, locked: false,
    children: [
      img("bg", 0, 0, 200, 100),
      {
        id: "pc", kind: "powerclip", type: "curve", x: 10, y: 10, w: 50, h: 30, visible: true, locked: false, image: { file: "pc.png" }, frame_rect: frameRect,
        children: [img("k1", -20, 0, 120, 80), { id: "g", kind: "group", type: "group", x: 0, y: 0, w: 40, h: 40, visible: true, locked: false, image: null, children: [img("k2", 5, 5, 10, 10, kidExtra)] }],
      },
    ],
  }],
});

test("livePowerclip: rectangular frame whose contents all have images", () => {
  assert.equal(livePowerclip(pcScene().layers[0].children[1]), true);
});

test("livePowerclip: falls back to the flat image for a non-rect frame or a child without an image", () => {
  assert.equal(livePowerclip(pcScene(false).layers[0].children[1]), false);
  assert.equal(livePowerclip(pcScene(true, { image: null }).layers[0].children[1]), false);
  assert.equal(livePowerclip(pcScene(true, { image: null, visible: false }).layers[0].children[1]), true); // a hidden child needs no image
  const old = pcScene(); delete old.layers[0].children[1].frame_rect;
  assert.equal(livePowerclip(old.layers[0].children[1]), false);
});

test("renderItems: a live PowerClip draws its contents inside a clip instead of its flat image", () => {
  const items = renderItems(pcScene());
  assert.equal(items.length, 2);
  assert.equal(items[0].leaf.id, "bg");
  assert.equal(items[1].clip.id, "pc");
  assert.deepEqual(items[1].items.map((i) => i.leaf.id), ["k1", "k2"]);              // group flattened, order kept
  assert.deepEqual(planNodes(items).map((n) => n.id), ["bg", "pc", "k1", "k2"]);
  const flat = renderItems(pcScene(false));
  assert.deepEqual(flat.map((i) => (i.leaf || i.clip).id), ["bg", "pc"]);
  assert.ok(flat[1].leaf);
});

test("renderItems: hidden container or hidden child is not drawn", () => {
  const s = pcScene(); s.layers[0].children[1].children[0].visible = false;
  assert.deepEqual(renderItems(s)[1].items.map((i) => i.leaf.id), ["k2"]);
  s.layers[0].children[1].visible = false;
  assert.equal(renderItems(s).length, 1);
});

test("dragTargets: a live container updates its clip and all contents; a flat one only itself", () => {
  const s = pcScene();
  assert.deepEqual(dragTargets(s.layers[0].children[1]).map((n) => n.id), ["pc", "k1", "k2"]);
  assert.deepEqual(dragTargets(pcScene(false).layers[0].children[1]).map((n) => n.id), ["pc"]);
  assert.deepEqual(dragTargets(s.layers[0].children[1].children[1]).map((n) => n.id), ["k2"]);
});

test("nested move/resize ops carry absolute coordinates and keep the frame fixed", () => {
  const s = pcScene();
  const moved = applyOps(s, [{ op: "move", ids: ["k1"], dx: 7, dy: -3 }]);
  const k = findNode(moved, "k1"), pc = findNode(moved, "pc");
  assert.deepEqual([k.x, k.y, k.w, k.h], [-13, -3, 120, 80]);
  assert.deepEqual([pc.x, pc.y, pc.w, pc.h], [10, 10, 50, 30]);
  assert.equal(livePowerclip(pc), true);                                            // stays live after an edit
  const resized = applyOps(s, [{ op: "resize", ids: ["k1"], from: { x: -20, y: 0, w: 120, h: 80 }, to: { x: -20, y: 0, w: 60, h: 40 } }]);
  assert.deepEqual([findNode(resized, "k1").w, findNode(resized, "k1").h], [60, 40]);
});
