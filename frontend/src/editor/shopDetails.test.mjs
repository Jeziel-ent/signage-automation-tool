import test from "node:test";
import assert from "node:assert/strict";
import { contactIds, findShopNameNode, textLabel, textNodes, toBoardText, toFieldText } from "./shopDetails.js";

const txt = (id, content, extra = {}) => ({ id, kind: "shape", type: "text", name: "", x: 10, y: 10, w: 50, h: 10, visible: true, text: { content, font: "Arial", size_pt: 90 }, ...extra });

// modelled on a real converted dalmia board: contact, Tamil shop name, "authorized dealer" footer, all untagged
const scene = (children) => ({ version: 3, page: { width: 3048, height: 1219 }, layers: [{ id: "L1", name: "Layer 1", visible: true, locked: false, children }] });
const REAL = scene([
  txt("s8", "Phone No. 70103 76961\rGST NO. 33AFBFS1844J1Z1"),
  txt("s7", "ஸ்ரீ கவி ஸ்டீல்ஸ்"),
  txt("s6", "அங்கீகரிக்கப்பட்ட \rவியாபாரி"),
  { id: "g1", kind: "group", name: "", x: 0, y: 0, w: 100, h: 100, visible: true, children: [txt("s9", "AL MADEENA POOJA STORE")] },
  txt("s10", "hidden text", { visible: false }),
]);

test("textNodes lists visible text objects, including those inside groups", () => {
  assert.deepEqual(textNodes(REAL).map((t) => t.id), ["s8", "s7", "s6", "s9"]);
});

test("contactIds finds the Phone/GST text", () => {
  assert.deepEqual(contactIds(REAL), ["s8"]);
});

test("no tag and no content match -> null (a v2 board keeps the master's own shop name), never a guess", () => {
  assert.equal(findShopNameNode(REAL, "Shop 3"), null);
});

test("content match: exact, case/space-insensitive, and partial either way", () => {
  assert.deepEqual(findShopNameNode(REAL, "al madeena  pooja store"), { id: "s9", source: "match" });
  assert.deepEqual(findShopNameNode(REAL, "AL MADEENA POOJA STORE (TVL)"), { id: "s9", source: "match" });
  assert.deepEqual(findShopNameNode(REAL, "Madeena Pooja"), { id: "s9", source: "match" });
});

test("a too-short shop name does not match everything", () => {
  assert.equal(findShopNameNode(REAL, "A"), null);
});

test("the shopname tag wins over a content match; the remembered choice wins over both", () => {
  const s = scene([txt("a", "AL MADEENA POOJA STORE"), txt("b", "Other", { name: "shopname_main" })]);
  assert.deepEqual(findShopNameNode(s, "AL MADEENA POOJA STORE"), { id: "b", source: "tag" });
  assert.deepEqual(findShopNameNode(s, "AL MADEENA POOJA STORE", "a"), { id: "a", source: "chosen" });
});

test("a remembered id that no longer exists (or is the contact text) is ignored", () => {
  assert.equal(findShopNameNode(REAL, "Shop 3", "gone"), null);
  assert.equal(findShopNameNode(REAL, "Shop 3", "s8"), null);
});

test("the contact text is never picked as the shop name, even when it matches", () => {
  const s = scene([txt("c", "Phone No. 123\rGST NO. X")]);
  assert.equal(findShopNameNode(s, "Phone No. 123"), null);
});

test("line breaks: CorelDRAW \\r <-> textarea \\n, keeping the original separator", () => {
  assert.equal(toFieldText("a\rb"), "a\nb");
  assert.equal(toBoardText("a\nb", "x\ry"), "a\rb");
  assert.equal(toBoardText("a\nb", "single line"), "a\nb");
});

test("textLabel flattens lines and truncates", () => {
  assert.equal(textLabel({ content: "Phone No. 1\rGST NO. 2" }), "Phone No. 1 / GST NO. 2");
  assert.equal(textLabel({ content: "" }), "(empty text)");
  assert.equal(textLabel({ content: "x".repeat(50) }, 10), "xxxxxxxxx…");
});
