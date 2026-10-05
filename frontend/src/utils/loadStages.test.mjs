import test from "node:test";
import assert from "node:assert/strict";
import { collectImageUrls, mapBuild, mapPreload, preloadImages, sceneOf, stageOf } from "./loadStages.js";

test("stages follow the 0-35 / 35-75 / 75-100 bands", () => {
  assert.deepEqual([0, 34, 35, 74, 75, 100].map(stageOf), ["BUILD", "BUILD", "TRANSFORM", "TRANSFORM", "PAINT", "PAINT"]);
});

test("the 3D narrative switches at 40 and 80", () => {
  assert.deepEqual([0, 39, 40, 79, 80, 100].map(sceneOf), ["hammer", "hammer", "panel", "panel", "paint", "paint"]);
});

test("server build progress fills 0-35; image preloading fills 35-75", () => {
  assert.equal(mapBuild(0), 0);
  assert.equal(mapBuild(100), 35);
  assert.equal(mapBuild(150), 35);
  assert.equal(mapPreload(0, 10), 35);
  assert.equal(mapPreload(5, 10), 55);
  assert.equal(mapPreload(10, 10), 75);
  assert.equal(mapPreload(0, 0), 75);          // a scene with no images is simply past that band
});

test("image urls are collected from leaves, PowerClips, nested groups and the page render, de-duplicated", () => {
  const scene = {
    page_image: { file: "page.png" },
    layers: [{ children: [
      { image: { file: "a.svg" } },
      { kind: "group", children: [{ image: { file: "b.png" } }, { image: { file: "a.svg" } }] },
      { kind: "powerclip", image: { file: "pc.png" }, children: [{ image: { file: "c.png" } }] },
      { kind: "shape" },
    ] }],
  };
  assert.deepEqual(collectImageUrls(scene, "/api/x/asset/").sort((a, b) => a.localeCompare(b)),
    ["/api/x/asset/a.svg", "/api/x/asset/b.png", "/api/x/asset/c.png", "/api/x/asset/page.png", "/api/x/asset/pc.png"]);
  assert.deepEqual(collectImageUrls({ layers: [] }, "/x/"), []);
});

class FakeImg {
  set src(u) { setTimeout(() => (u.includes("bad") ? this.onerror() : this.onload()), 1); }
}

test("preloading reports real progress, counts failures as settled, and resolves when all settle", async () => {
  const seen = [];
  await preloadImages(["/a", "/bad", "/c"], (l, t) => seen.push([l, t]), { Img: FakeImg });
  assert.deepEqual(seen.map((s) => s[0]), [1, 2, 3]);
  assert.ok(seen.every((s) => s[1] === 3));
});

test("an empty list resolves immediately with 0/0; a stuck request cannot hold the loader past the timeout", async () => {
  const seen = [];
  await preloadImages([], (l, t) => seen.push([l, t]), { Img: FakeImg });
  assert.deepEqual(seen, [[0, 0]]);
  class Stuck { set src(_) {} }
  const t0 = Date.now();
  await preloadImages(["/never"], null, { Img: Stuck, timeoutMs: 60 });
  assert.ok(Date.now() - t0 < 1000);
});
