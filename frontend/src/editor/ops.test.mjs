// Run with: npm test   (node's built-in runner, no extra dependencies)
// The same golden cases are run by backend/tests/test_scene_ops.py against the
// Python implementation - see backend/app/scene_ops.py.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { applyOps, applyOp, cloneScene, findNode, OpError } from "./ops.js";

const here = dirname(fileURLToPath(import.meta.url));
const GOLDEN = JSON.parse(readFileSync(resolve(here, "../../../backend/tests/fixtures/ops_golden.json"), "utf8"));
const TOL = 1e-3;

const project = (n) => {
  const p = [n.id, n.x, n.y, n.w, n.h];
  if (n.children && n.children.length) p.push(n.children.map(project));
  return p;
};

function assertTreeClose(actual, expected, path = "") {
  assert.equal(actual.length, expected.length, `${path}: ${actual.map((a) => a[0])} != ${expected.map((e) => e[0])}`);
  actual.forEach((a, i) => {
    const e = expected[i];
    assert.equal(a[0], e[0], `${path}: order/id mismatch`);
    ["x", "y", "w", "h"].forEach((name, k) => {
      assert.ok(Math.abs(a[k + 1] - e[k + 1]) < TOL, `${path}/${a[0]}.${name}: ${a[k + 1]} != ${e[k + 1]}`);
    });
    assert.equal(a.length > 5, e.length > 5, `${path}/${a[0]}: children presence differs`);
    if (e.length > 5) assertTreeClose(a[5], e[5], `${path}/${a[0]}`);
  });
}

for (const c of GOLDEN.cases) {
  test(`golden: ${c.name}`, () => {
    const out = applyOps(GOLDEN.base, c.ops);
    const layer = out.layers[0];
    if (c.expect) assertTreeClose(layer.children.map(project), c.expect);
    if (c.expect_ids) assert.deepEqual(layer.children.map((n) => n.id), c.expect_ids);
    if (c.expect_page) assert.deepEqual([out.page.width, out.page.height], c.expect_page);
    for (const [id, want] of Object.entries(c.expect_text || {})) {
      for (const [k, v] of Object.entries(want)) assert.equal(findNode(out, id).text[k], v);
    }
    for (const id of c.expect_stale || []) assert.equal(findNode(out, id).stale, true);
    for (const [id, want] of Object.entries(c.expect_visible || {})) {
      const target = id === layer.id ? layer : findNode(out, id);
      assert.equal(target.visible, want);
    }
  });
}

for (const c of GOLDEN.errors) {
  test(`golden error: ${c.name}`, () => {
    assert.throws(() => applyOps(GOLDEN.base, c.ops), (e) => e instanceof OpError && e.message.includes(c.error));
  });
}

test("applyOps does not mutate its input", () => {
  const before = cloneScene(GOLDEN.base);
  for (const c of GOLDEN.cases) applyOps(GOLDEN.base, c.ops);
  assert.deepEqual(GOLDEN.base, before);
});

test("replay equals step-by-step application", () => {
  const ops = [
    { op: "move", ids: ["g"], dx: 3, dy: 4 },
    { op: "resize", ids: ["g"], from: { x: 13, y: 14, w: 80, h: 30 }, to: { x: 13, y: 14, w: 40, h: 30 } },
    { op: "order", id: "a", mode: "front" },
    { op: "group", ids: ["a", "t"], group_id: "G2" },
    { op: "visibility", id: "G2", visible: false },
  ];
  const once = applyOps(GOLDEN.base, ops);
  let step = cloneScene(GOLDEN.base);
  for (const op of ops) step = applyOps(step, [op]);
  assert.deepEqual(step, once);
});

test("error names the failing op index", () => {
  assert.throws(
    () => applyOps(GOLDEN.base, [{ op: "move", ids: ["a"], dx: 1, dy: 1 }, { op: "move", ids: ["zzz"], dx: 1, dy: 1 }]),
    /op #1 \(move\)/,
  );
});

test("locked objects reject edits; missing fields are OpErrors", () => {
  const s = cloneScene(GOLDEN.base);
  s.layers[0].children[0].locked = true;
  assert.throws(() => applyOp(s, { op: "move", ids: ["a"], dx: 1, dy: 1 }), /locked/);
  assert.throws(() => applyOps(GOLDEN.base, [{ op: "move", ids: ["a"] }]), /missing field/);
});

for (const c of GOLDEN.layer_cases) {
  test(`golden layers: ${c.name}`, () => {
    const out = applyOps(GOLDEN.base2, c.ops);
    assert.deepEqual(out.layers.map((l) => l.id), c.expect_layers);
    for (const [id, box] of Object.entries(c.expect_boxes || {})) {
      const n = findNode(out, id);
      assert.deepEqual([n.x, n.y, n.w, n.h], box);
    }
  });
}

for (const c of GOLDEN.layer_errors) {
  test(`golden layer error: ${c.name}`, () => {
    assert.throws(() => applyOps(GOLDEN.base2, c.ops), (e) => e instanceof OpError && e.message.includes(c.error));
  });
}

for (const c of GOLDEN.powerclip_cases) {
  test(`golden powerclip: ${c.name}`, () => {
    const out = applyOps(GOLDEN.base3, c.ops);
    for (const [id, want] of Object.entries(c.expect_text || {})) {
      for (const [k, v] of Object.entries(want)) assert.equal(findNode(out, id).text[k], v);
    }
    for (const [id, box] of Object.entries(c.expect_boxes || {})) {
      const n = findNode(out, id);
      [n.x, n.y, n.w, n.h].forEach((v, k) => assert.ok(Math.abs(v - box[k]) < TOL, `${id}: ${[n.x, n.y, n.w, n.h]} != ${box}`));
    }
    for (const id of c.expect_stale || []) assert.equal(findNode(out, id).stale, true);
  });
}

for (const c of GOLDEN.powerclip_errors) {
  test(`golden powerclip error: ${c.name}`, () => {
    assert.throws(() => applyOps(GOLDEN.base3, c.ops), (e) => e instanceof OpError && e.message.includes(c.error));
  });
}

for (const c of GOLDEN.product_cases) {
  test(`golden product: ${c.name}`, () => {
    const out = applyOps(GOLDEN.product_base, c.ops);
    for (const [id, box] of Object.entries(c.expect_boxes || {})) {
      const n = findNode(out, id);
      [n.x, n.y, n.w, n.h].forEach((v, k) => assert.ok(Math.abs(v - box[k]) < TOL, `${id}: ${[n.x, n.y, n.w, n.h]} != ${box}`));
    }
    for (const [id, want] of Object.entries(c.expect_text || {})) {
      for (const [k, v] of Object.entries(want)) assert.equal(findNode(out, id).text[k], v);
    }
    for (const [id, want] of Object.entries(c.expect_asset || {})) assert.deepEqual(findNode(out, id).image_asset, want);
    for (const [id, box] of Object.entries(c.expect_slot_frame || {})) {
      const f = findNode(out, id).slot_frame;
      [f.x, f.y, f.w, f.h].forEach((v, k) => assert.ok(Math.abs(v - box[k]) < TOL, `${id} slot_frame: ${[f.x, f.y, f.w, f.h]} != ${box}`));
    }
    for (const id of c.expect_no_slot_frame || []) assert.equal(findNode(out, id).slot_frame, undefined);
    for (const id of c.expect_stale || []) assert.equal(findNode(out, id).stale, true);
  });
}

for (const c of GOLDEN.product_errors) {
  test(`golden product error: ${c.name}`, () => {
    assert.throws(() => applyOps(GOLDEN.product_base, c.ops), (e) => e instanceof OpError && e.message.includes(c.error));
  });
}
