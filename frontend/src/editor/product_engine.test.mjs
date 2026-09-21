// Unit tests for product_engine.js's own helpers (kindFromName, aspectFit, resolveFrame) - the
// golden cross-language cases in ops.test.mjs (golden product / golden product error) cover the
// ops/mapping end to end against the shared fixture; this file covers the pure math and edge cases
// that fixture doesn't exercise. Mirrors backend/tests/test_product_engine.py.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { buildIndex, OpError } from "./ops.js";
import {
  aspectFit, checkAsset, isBitmap, kindFromName, planImageSwap, resolveFrame, slotForNode, swapImageOp, updateSlotOp,
} from "./product_engine.js";

const here = dirname(fileURLToPath(import.meta.url));
const GOLDEN = JSON.parse(readFileSync(resolve(here, "../../../backend/tests/fixtures/ops_golden.json"), "utf8"));
const GOLDEN_BASE = GOLDEN.product_base;

test("kindFromName is case-insensitive and prefix-based", () => {
  assert.equal(kindFromName("Product_Image_1"), "product_image");
  assert.equal(kindFromName("BRAND_TITLE"), "brand_title");
  assert.equal(kindFromName("addressee"), "address"); // prefix match, not exact
  assert.equal(kindFromName("logo"), null);
  assert.equal(kindFromName(null), null);
  assert.equal(kindFromName(""), null);
});

test("isBitmap", () => {
  assert.equal(isBitmap({ kind: "shape", type: "bitmap" }), true);
  assert.equal(isBitmap({ kind: "shape", type: "curve" }), false);
  assert.equal(isBitmap({ kind: "group", type: "bitmap" }), false);
});

test("checkAsset accepts a well-formed asset", () => {
  assert.deepEqual(checkAsset({ name: " a.png ", w: "100", h: 50 }), { name: "a.png", w: 100, h: 50 });
});

for (const asset of [null, {}, { name: "" }, { name: "a", w: 0, h: 10 }, { name: "a", w: -1, h: 10 }, { name: "a", w: "x", h: 10 }]) {
  test(`checkAsset rejects a malformed asset: ${JSON.stringify(asset)}`, () => {
    assert.throws(() => checkAsset(asset), /asset must be an object/);
  });
}

test("aspectFit: contain centres the whole image in a wider frame", () => {
  assert.deepEqual(aspectFit(200, 100, { x: 0, y: 0, w: 100, h: 100 }), { x: 0, y: 25, w: 100, h: 50 });
});

test("aspectFit: cover fills the frame and overflows", () => {
  assert.deepEqual(aspectFit(200, 100, { x: 0, y: 0, w: 100, h: 100 }, "cover"), { x: -50, y: 0, w: 200, h: 100 });
});

test("aspectFit: padding insets the frame before fitting", () => {
  assert.deepEqual(aspectFit(100, 100, { x: 0, y: 0, w: 100, h: 100 }, "contain", 10), { x: 10, y: 10, w: 80, h: 80 });
});

test("aspectFit: a square asset in a taller frame fits by width", () => {
  assert.deepEqual(aspectFit(50, 50, { x: 0, y: 0, w: 40, h: 200 }), { x: 0, y: 80, w: 40, h: 40 });
});

test("aspectFit rejects an unknown fit mode", () => {
  assert.throws(() => aspectFit(10, 10, { x: 0, y: 0, w: 10, h: 10 }, "stretch"), /fit must be/);
});

test("aspectFit rejects a non-positive asset size", () => {
  assert.throws(() => aspectFit(0, 10, { x: 0, y: 0, w: 10, h: 10 }), /asset must be/);
});

test("aspectFit rejects negative padding", () => {
  assert.throws(() => aspectFit(10, 10, { x: 0, y: 0, w: 10, h: 10 }, "contain", -1), /padding must be/);
});

test("aspectFit rejects padding that consumes the whole frame", () => {
  assert.throws(() => aspectFit(10, 10, { x: 0, y: 0, w: 10, h: 10 }, "contain", 6), /padding leaves no room/);
});

function imgScene(overrides = {}) {
  const node = { id: "img", kind: "shape", type: "bitmap", x: 10, y: 10, w: 20, h: 10, rotation: 0, visible: true, locked: false, ...overrides };
  return { page: { width: 100, height: 100 }, layers: [{ id: "L1", name: "Layer 1", visible: true, locked: false, children: [node] }] };
}

test("resolveFrame defaults to the node's own box", () => {
  const { frame, clipId } = resolveFrame(buildIndex(imgScene()), "img");
  assert.deepEqual(frame, { x: 10, y: 10, w: 20, h: 10 });
  assert.equal(clipId, null);
});

test("resolveFrame uses the remembered slot_frame over the current box", () => {
  const { frame } = resolveFrame(buildIndex(imgScene({ slot_frame: { x: 0, y: 0, w: 5, h: 5 } })), "img");
  assert.deepEqual(frame, { x: 0, y: 0, w: 5, h: 5 });
});

test("resolveFrame: an explicit frame overrides both", () => {
  const idx = buildIndex(imgScene({ slot_frame: { x: 0, y: 0, w: 5, h: 5 } }));
  const { frame } = resolveFrame(idx, "img", "contain", { x: 1, y: 2, w: 3, h: 4 });
  assert.deepEqual(frame, { x: 1, y: 2, w: 3, h: 4 });
});

test("resolveFrame: cover without a PowerClip is refused", () => {
  assert.throws(() => resolveFrame(buildIndex(imgScene()), "img", "cover"), /needs the image to be inside a PowerClip/);
});

test("resolveFrame: an explicit frame with zero size is refused", () => {
  assert.throws(
    () => resolveFrame(buildIndex(imgScene()), "img", "contain", { x: 0, y: 0, w: 0, h: 5 }),
    /frame width\/height must be positive/,
  );
});

test("resolveFrame: inside a PowerClip the frame is always the container's box", () => {
  const scene = {
    page: { width: 100, height: 100 },
    layers: [{
      id: "L1", name: "L1", visible: true, locked: false, children: [{
        id: "pc", kind: "powerclip", type: "rectangle", x: 0, y: 0, w: 50, h: 50, rotation: 0, visible: true, locked: false, frame_rect: true,
        children: [{ id: "img", kind: "shape", type: "bitmap", x: 5, y: 5, w: 10, h: 10, rotation: 0, visible: true, locked: false }],
      }],
    }],
  };
  const idx = buildIndex(scene);
  const { frame, clipId } = resolveFrame(idx, "img");
  assert.deepEqual(frame, { x: 0, y: 0, w: 50, h: 50 });
  assert.equal(clipId, "pc");
  assert.throws(() => resolveFrame(idx, "img", "contain", { x: 1, y: 1, w: 50, h: 50 }), /frame differs from the PowerClip frame/);
  // a frame within FRAME_TOL_MM of the real one is accepted, not just an exact match
  const { frame: frame2 } = resolveFrame(idx, "img", "contain", { x: 0.1, y: 0, w: 50, h: 50 });
  assert.deepEqual(frame2, { x: 0, y: 0, w: 50, h: 50 });
});

test("planImageSwap reports the frame and box without mutating the scene", () => {
  const scene = structuredClone(GOLDEN_BASE);
  const plan = planImageSwap(scene, "pimg1", { name: "a.png", w: 400, h: 200 });
  assert.equal(plan.id, "pimg1");
  assert.equal(plan.containerId, null);
  assert.deepEqual(plan.frame, { x: 10, y: 10, w: 50, h: 30 });
  assert.deepEqual(plan.box, { x: 10, y: 12.5, w: 50, h: 25 });
  assert.deepEqual(scene, GOLDEN_BASE); // read-only
});

test("slotForNode finds the slot that owns a given node", () => {
  const slot = slotForNode(GOLDEN_BASE, "pcimg");
  assert.ok(slot);
  assert.equal(slot.kind, "product_image");
  assert.equal(slot.containerId, "pc2");
  assert.equal(slotForNode(GOLDEN_BASE, "bg"), null); // the background is never a slot
});

test("updateSlotOp rejects an unknown slot id", () => {
  assert.throws(
    () => updateSlotOp(GOLDEN_BASE, "product_image:does-not-exist", { asset: { name: "a", w: 1, h: 1 } }),
    (e) => e instanceof OpError && /unknown product slot/.test(e.message),
  );
});

test("swapImageOp rejects an unknown node id", () => {
  assert.throws(
    () => swapImageOp(GOLDEN_BASE, "does-not-exist", { name: "a", w: 1, h: 1 }),
    (e) => e instanceof OpError && /unknown id/.test(e.message),
  );
});
