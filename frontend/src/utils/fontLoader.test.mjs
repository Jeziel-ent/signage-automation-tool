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

test("fontCandidates: the exact name, then its base family with the weight/style its trailing words mean", async () => {
  const { fontCandidates } = await import("./fontLoader.js");
  assert.deepEqual(fontCandidates("AvantGarde-Demi"), [
    { family: "AvantGarde-Demi", weight: 400, style: "normal" },
    { family: "AvantGarde", weight: 600, style: "normal" },
    { family: "Avant Garde", weight: 600, style: "normal" },          // camel case split: Fontsource's "avant-garde"
  ]);
  assert.deepEqual(fontCandidates("Copperplate Gothic Bold")[1], { family: "Copperplate Gothic", weight: 700, style: "normal" });
  assert.deepEqual(fontCandidates("Roboto Semi Bold Italic")[1], { family: "Roboto", weight: 600, style: "italic" });
  assert.deepEqual(fontCandidates("Open Sans ExtraBold")[1], { family: "Open Sans", weight: 800, style: "normal" });
  assert.deepEqual(fontCandidates("Arial"), [{ family: "Arial", weight: 400, style: "normal" }]);
  assert.deepEqual(fontCandidates("Bold"), [{ family: "Bold", weight: 400, style: "normal" }]);   // a lone style word stays the name
  assert.deepEqual(fontCandidates(""), []);
});

test("fontsourceId / googleFaceUrl", async () => {
  const { fontsourceId, googleFaceUrl } = await import("./fontLoader.js");
  assert.equal(fontsourceId("Open Sans"), "open-sans");
  assert.equal(fontsourceId(" Avant  Garde "), "avant-garde");
  assert.equal(googleFaceUrl("Open Sans", 600, "italic"), "https://fonts.googleapis.com/css2?family=Open+Sans:ital,wght@1,600&display=swap");
});

test("parseGoogleCss keeps the latin / latin-ext / tamil faces with their unicode ranges", async () => {
  const { parseGoogleCss } = await import("./fontLoader.js");
  const css = `/* cyrillic */
@font-face { font-family: 'Roboto'; src: url(https://fonts.gstatic.com/c.woff2) format('woff2'); unicode-range: U+0301, U+0400-045F; }
/* latin */
@font-face { font-family: 'Roboto'; font-weight: 700; src: url(https://fonts.gstatic.com/l.woff2) format('woff2'); unicode-range: U+0000-00FF, U+0131; }`;
  assert.deepEqual(parseGoogleCss(css), [{ subset: "latin", url: "https://fonts.gstatic.com/l.woff2", unicodeRange: "U+0000-00FF, U+0131" }]);
  assert.deepEqual(parseGoogleCss(""), []);
});

test("pickFontsourceFiles: nearest weight, the style if it exists, default subset plus Tamil", async () => {
  const { pickFontsourceFiles } = await import("./fontLoader.js");
  const url = (w, st, sub) => ({ url: { woff2: `https://cdn.jsdelivr.net/fontsource/fonts/x@latest/${sub}-${w}-${st}.woff2` } });
  const meta = {
    defSubset: "latin",
    unicodeRange: { latin: "U+0000-00FF", tamil: "U+0B80-0BFF" },
    variants: {
      400: { normal: { latin: url(400, "normal", "latin"), tamil: url(400, "normal", "tamil") } },
      700: { normal: { latin: url(700, "normal", "latin") } },
    },
  };
  const semi = pickFontsourceFiles(meta, 600, "italic");                // no 600 and no italic: 700 (100 away) beats 400 (200 away)
  assert.deepEqual([semi[0].weight, semi[0].style, semi.length], [700, "normal", 1]);
  const got = pickFontsourceFiles(meta, 400, "normal");
  assert.deepEqual(got.map((f) => f.subset), ["latin", "tamil"]);       // Tamil too, for Tamil board text
  assert.equal(got[1].unicodeRange, "U+0B80-0BFF");
  const bold = pickFontsourceFiles(meta, 700, "normal");
  assert.deepEqual(bold.map((f) => f.url), ["https://cdn.jsdelivr.net/fontsource/fonts/x@latest/latin-700-normal.woff2"]);
  assert.deepEqual(pickFontsourceFiles(null, 400, "normal"), []);
});
