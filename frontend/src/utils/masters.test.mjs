import test from "node:test";
import assert from "node:assert/strict";
import { boardOrientation, fallbackWarning, groupMasters, masterCount, masterFallback, masterLabel, mastersOf, primaryMaster, rowMasterId, rowMasterIds } from "./masters.js";

const L = (id, name = id) => ({ id, name, orientation: "landscape", brand: "dalmia" });
const P = (id, name = id) => ({ id, name, orientation: "portrait", brand: "dalmia" });
const M = (land = [], port = []) => ({ landscape: land.map((x) => L(x)), portrait: port.map((x) => P(x)) });

test("orientation follows the server's 1.25 rule (square and near-square boards are portrait)", () => {
  assert.equal(boardOrientation({ width: 11, height: 6 }), "landscape");
  assert.equal(boardOrientation({ width: 125, height: 100 }), "landscape");
  assert.equal(boardOrientation({ width: 124, height: 100 }), "portrait");
  assert.equal(boardOrientation({ width: 6, height: 6 }), "portrait");
  assert.equal(boardOrientation({ width: 60, height: 75 }), "portrait");
  assert.equal(boardOrientation({ width: "", height: 4 }), null);
});

test("the registry list is grouped per brand and orientation, keeping the server's order", () => {
  const list = [L("L1"), P("P1"), { ...P("X"), brand: "adinn" }, L("L2"), P("P2"), P("P3")];
  const g = groupMasters(list, "dalmia");
  assert.deepEqual(g.landscape.map((m) => m.id), ["L1", "L2"]);
  assert.deepEqual(g.portrait.map((m) => m.id), ["P1", "P2", "P3"]);
  assert.equal(masterCount(g), 5);
  assert.equal(masterCount(groupMasters(list, "nobody")), 0);
});

test("a 3 x 6 ft row lists every portrait master, a 16 x 3 ft row every landscape master", () => {
  const m = M(["L1", "L2", "L3"], ["P1", "P2", "P3"]);
  assert.deepEqual(mastersOf(m, boardOrientation({ width: 3, height: 6 })).map((x) => x.id), ["P1", "P2", "P3"]);
  assert.deepEqual(mastersOf(m, boardOrientation({ width: 16, height: 3 })).map((x) => x.id), ["L1", "L2", "L3"]);
  assert.equal(masterLabel(P("P2", "Master 2 - High Density")), "P: Master 2 - High Density");
  assert.equal(masterLabel(L("L1", "Master 1")), "L: Master 1");
});

test("a row's pick is sent as master_id, with each orientation's default alongside", () => {
  const m = M(["L1", "L2", "L3"], ["P1", "P2", "P3"]);
  assert.deepEqual(rowMasterIds({ width: 3, height: 6, master_id: "P3" }, m), { master_id: "P3", landscape_master_id: "L1", portrait_master_id: "P1" });
  assert.deepEqual(rowMasterIds({ width: 16, height: 3, master_id: "L2" }, m), { master_id: "L2", landscape_master_id: "L1", portrait_master_id: "P1" });
  // no pick yet: the orientation's default
  assert.equal(rowMasterId({ width: 16, height: 3 }, m), "L1");
});

test("a pick that no longer fits the row falls back to its orientation's default", () => {
  const m = M(["L1", "L2"], ["P1", "P2"]);
  // picked as landscape, then resized to a portrait board
  assert.equal(rowMasterId({ width: 3, height: 6, master_id: "L2" }, m), "P1");
  // the picked master was deleted
  assert.equal(rowMasterId({ width: 3, height: 6, master_id: "P9" }, m), "P1");
  // no master of the row's orientation, or no size yet
  assert.equal(rowMasterId({ width: 3, height: 6 }, M(["L1"])), null);
  assert.equal(rowMasterId({ width: "", height: 6, master_id: "P1" }, m), null);
  assert.deepEqual(rowMasterIds({ width: 12, height: 4 }, M([], ["P1"])), { master_id: null, landscape_master_id: null, portrait_master_id: "P1" });
});

test("the master new shops are saved on", () => {
  assert.equal(primaryMaster(M(["L1", "L2"], ["P1"])).id, "L1");
  assert.equal(primaryMaster(M([], ["P2", "P3"])).id, "P2");
  assert.equal(primaryMaster(M()), null);
  assert.equal(primaryMaster(undefined), null);
});

test("a row whose orientation has no master is flagged before converting", () => {
  const onlyLandscape = M(["L1"]);
  assert.equal(masterFallback({ width: 3, height: 6 }, onlyLandscape), "landscape");
  assert.equal(masterFallback({ width: 16, height: 3 }, onlyLandscape), null);
  assert.equal(masterFallback({ width: 3, height: 6 }, M(["L1"], ["P1"])), null);
  assert.equal(masterFallback({ width: 3, height: 6 }, M()), null);
  const w = fallbackWarning([{ name: "Eriyur Bekary", width: 3, height: 6, unit: "ft" }, { name: "A", width: 16, height: 3, unit: "ft" }], onlyLandscape);
  assert.match(w, /No portrait master/);
  assert.match(w, /Eriyur Bekary \(3 × 6 ft\)/);
  assert.doesNotMatch(w, /\bA \(16/);
  assert.equal(fallbackWarning([{ name: "A", width: 16, height: 3 }], onlyLandscape), null);
});

test("resolveMasterId: a sheet cell names an uploaded master by name, label or file name", async () => {
  const { resolveMasterId } = await import("./masters.js");
  const masters = {
    landscape: [{ id: "a", name: "Master 7", orientation: "landscape", file_name: "6 X 3.cdr" }, { id: "b", name: "Master 8", orientation: "landscape", file_name: "10 X 3.cdr" }],
    portrait: [{ id: "c", name: "Master 4", orientation: "portrait", file_name: "4 X 8.cdr" }],
  };
  assert.equal(resolveMasterId("Master 7", masters), "a");
  assert.equal(resolveMasterId("master8", masters), "b");
  assert.equal(resolveMasterId("L: Master 7", masters), "a");
  assert.equal(resolveMasterId("6 X 3.cdr", masters), "a");
  assert.equal(resolveMasterId("4 x 8", masters), "c");
  assert.equal(resolveMasterId("Master 7 - Promotional", masters), "a");
  assert.equal(resolveMasterId("Master 9", masters), null);
  assert.equal(resolveMasterId("", masters), null);
});

test("Auto master: only a master the user picked is sent as master_id (the server routes the rest)", async () => {
  const { pickedMasterId, rowMasterIdsAuto } = await import("./masters.js");
  const masters = M(["a", "b"], ["p"]);
  const row = { width: 10, height: 3 };
  assert.equal(pickedMasterId(row, masters), null);                       // nothing picked = Auto
  assert.equal(pickedMasterId({ ...row, master_id: "b" }, masters), "b");
  assert.equal(pickedMasterId({ ...row, master_id: "p" }, masters), null); // a portrait master is not valid for a landscape row
  assert.deepEqual(rowMasterIdsAuto(row, masters), { master_id: null, landscape_master_id: "a", portrait_master_id: "p" });
  assert.equal(rowMasterIdsAuto({ ...row, master_id: "b" }, masters).master_id, "b");
  assert.equal(rowMasterIds(row, masters).master_id, "a");                // the existing helper is unchanged
});
