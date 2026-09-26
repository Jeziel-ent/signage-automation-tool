import test from "node:test";
import assert from "node:assert/strict";
import { googleFontsUrl, missingFonts, sceneFonts, serverFontUrl } from "./fontLoader.js";

const txt = (id, font) => ({ id, kind: "shape", type: "text", text: { content: "x", font } });

test("sceneFonts: unique families across layers and groups, first-seen order, case-insensitive, blanks skipped", () => {
  const scene = {
    layers: [
      { children: [txt("a", "Arial"), { id: "g", kind: "group", children: [txt("b", "Nirmala UI"), txt("c", "arial")] }] },
      { children: [txt("d", " Yu Gothic Medium "), txt("e", ""), txt("f", null), { id: "i", kind: "shape", type: "bitmap" }] },
    ],
  };
  assert.deepEqual(sceneFonts(scene), ["Arial", "Nirmala UI", "Yu Gothic Medium"]);
  assert.deepEqual(sceneFonts(null), []);
});

test("googleFontsUrl encodes the family with + for spaces", () => {
  assert.equal(googleFontsUrl("Noto Sans Tamil"), "https://fonts.googleapis.com/css2?family=Noto+Sans+Tamil:ital,wght@0,400;0,700;1,400;1,700&display=swap");
  assert.ok(googleFontsUrl("A&B").includes("family=A%26B:"));
});

test("serverFontUrl encodes the family as a query value", () => {
  assert.equal(serverFontUrl("Yu Gothic Medium"), "/api/fonts/file?family=Yu%20Gothic%20Medium");
});

test("missingFonts lists only the missing ones", () => {
  assert.deepEqual(missingFonts({ Arial: "local", X: "missing", Y: "google", Z: "missing" }), ["X", "Z"]);
});
