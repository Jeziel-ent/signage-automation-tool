import test from "node:test";
import assert from "node:assert/strict";
import { boardOrientation, mastersOf, primaryMaster, rowMasterIds, rowMasterSlot } from "./masters.js";

const M = (ids) => Object.fromEntries(Object.entries(ids).map(([k, id]) => [k, id ? { id } : null]));

test("orientation follows the server's 1.25 rule (square and near-square boards are portrait)", () => {
  assert.equal(boardOrientation({ width: 11, height: 6 }), "landscape");
  assert.equal(boardOrientation({ width: 125, height: 100 }), "landscape");
  assert.equal(boardOrientation({ width: 124, height: 100 }), "portrait");
  assert.equal(boardOrientation({ width: 6, height: 6 }), "portrait");
  assert.equal(boardOrientation({ width: 60, height: 75 }), "portrait");
  assert.equal(boardOrientation({ width: "", height: 4 }), null);
});

test("one master per orientation: auto-assigned; two: the row's choice", () => {
  const one = M({ landscape_1: "L1", portrait_1: "P1" });
  const two = M({ landscape_1: "L1", landscape_2: "L2", portrait_1: "P1" });
  const wide = { width: 12, height: 4 };
  assert.equal(mastersOf(one, "landscape").length, 1);
  assert.deepEqual(rowMasterIds(wide, one), { landscape_master_id: "L1", portrait_master_id: "P1" });
  assert.deepEqual(rowMasterIds({ ...wide, master_slot: 2 }, two), { landscape_master_id: "L2", portrait_master_id: "P1" });
  assert.deepEqual(rowMasterIds(wide, two), { landscape_master_id: "L1", portrait_master_id: "P1" });
  // a portrait row's choice does not touch the landscape id
  const p2 = M({ landscape_1: "L1", portrait_1: "P1", portrait_2: "P2" });
  assert.deepEqual(rowMasterIds({ width: 6, height: 6, master_slot: 2 }, p2), { landscape_master_id: "L1", portrait_master_id: "P2" });
});

test("a choice of a master that is not there falls back to the one that is", () => {
  assert.equal(rowMasterSlot({ width: 12, height: 4, master_slot: 2 }, M({ landscape_1: "L1" })), 1);
  assert.equal(rowMasterSlot({ width: 12, height: 4 }, M({ landscape_2: "L2" })), 2);
  assert.deepEqual(rowMasterIds({ width: 12, height: 4 }, M({ portrait_1: "P1" })), { landscape_master_id: null, portrait_master_id: "P1" });
  assert.equal(primaryMaster(M({ portrait_2: "P2" })).id, "P2");
  assert.equal(primaryMaster(M({})), null);
});

test("a row whose orientation has no master is flagged before converting", async () => {
  const { masterFallback, fallbackWarning } = await import("./masters.js");
  const onlyLandscape = M({ landscape_1: "L1" });
  assert.equal(masterFallback({ width: 3, height: 6 }, onlyLandscape), "landscape");
  assert.equal(masterFallback({ width: 16, height: 3 }, onlyLandscape), null);
  assert.equal(masterFallback({ width: 3, height: 6 }, M({ landscape_1: "L1", portrait_1: "P1" })), null);
  assert.equal(masterFallback({ width: 3, height: 6 }, M({})), null);
  const w = fallbackWarning([{ name: "Eriyur Bekary", width: 3, height: 6, unit: "ft" }, { name: "A", width: 16, height: 3, unit: "ft" }], onlyLandscape);
  assert.match(w, /No portrait master/);
  assert.match(w, /Eriyur Bekary \(3 × 6 ft\)/);
  assert.doesNotMatch(w, /\bA \(16/);
  assert.equal(fallbackWarning([{ name: "A", width: 16, height: 3 }], onlyLandscape), null);
});
