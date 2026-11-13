import test from "node:test";
import assert from "node:assert/strict";
import { byBrand, byDay, dayOf, filesTotals } from "./adminFiles.js";

const at = (y, m, d, h = 12) => new Date(y, m - 1, d, h).getTime() / 1000;

test("boards are grouped by the local day they were made, newest first", () => {
  const groups = byDay([
    { shop_id: "a", made_at: at(2026, 11, 12, 9), bytes: 10 },
    { shop_id: "b", made_at: at(2026, 11, 13, 8), bytes: 5 },
    { shop_id: "c", made_at: at(2026, 11, 12, 18), bytes: 20 },
  ]);
  assert.deepEqual(groups.map((g) => g.day), ["2026-11-13", "2026-11-12"]);
  assert.deepEqual(groups[1].items.map((b) => b.shop_id), ["c", "a"]);
  assert.equal(groups[1].bytes, 30);
  assert.match(dayOf(at(2026, 11, 12)).label, /2026/);
});

test("masters are grouped by brand A-Z with their sizes", () => {
  const g = byBrand([{ brand: "Hangyo", bytes: 3 }, { brand: "Agni", bytes: 1 }, { brand: "Hangyo", bytes: 4 }]);
  assert.deepEqual(g.map((x) => [x.brand, x.items.length, x.bytes]), [["Agni", 1, 1], ["Hangyo", 2, 7]]);
});

test("totals per category and overall", () => {
  const t = filesTotals({ masters: [{ bytes: 10 }], boards: [{ bytes: 5 }, { bytes: 5 }], backups: [], orphans: [{ bytes: 1 }] });
  assert.deepEqual([t.masters.n, t.boards.n, t.boards.bytes, t.all], [1, 2, 10, 21]);
  assert.equal(filesTotals(null).all, 0);
});
