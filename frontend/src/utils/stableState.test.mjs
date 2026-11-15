import test from "node:test";
import assert from "node:assert/strict";
import { keepIfSame } from "./stableState.js";

test("same content keeps the old reference, so React does not re-render", () => {
  const prev = { port: 47890, version: "0.3.8", corel: [27] };
  assert.equal(keepIfSame(prev, { port: 47890, version: "0.3.8", corel: [27] }), prev);
  const list = [{ id: "a", online: true }];
  assert.equal(keepIfSame(list, [{ id: "a", online: true }]), list);
  assert.equal(keepIfSame(null, null), null);
  assert.equal(keepIfSame([], []).length, 0);
});

test("a changed value is taken", () => {
  const prev = { port: 47890, version: "0.3.7" };
  const next = { port: 47890, version: "0.3.8" };
  assert.equal(keepIfSame(prev, next), next);
  assert.deepEqual(keepIfSame(null, { port: 1 }), { port: 1 });
  const agents = [{ id: "a", online: true }];
  assert.deepEqual(keepIfSame(agents, [{ id: "a", online: false }]), [{ id: "a", online: false }]);
});
