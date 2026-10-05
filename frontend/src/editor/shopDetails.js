// Pure helpers for the editor's Shop details panel (unit-tested in shopDetails.test.mjs).
//
// The board's shop-name text can't be found from the shop record: a v2 conversion keeps the MASTER's own shop-name text
// (see CLAUDE.md "Post-Phase-A refinements"), so a shop called "Shop 3" still reads e.g. "ஸ்ரீ கவி ஸ்டீல்ஸ்" on the board, and real
// masters are untagged. So: a `shopname` tag wins, then a text whose content matches the shop's name, and otherwise the designer
// picks the text object once (remembered per shop).
import { iterNodes } from "./ops.js";
import { mapSlots, SLOT_CONTACT } from "./product_engine.js";

const SHOPNAME_TAG = /^shopname/i;
const norm = (s) => (s || "").replace(/\s+/g, " ").trim().toLowerCase();

/** Every visible text object on the board, in Object Manager order: [{ id, content, name }]. */
export function textNodes(scene) {
  const out = [];
  for (const n of iterNodes(scene)) {
    if (n.text && n.visible !== false) out.push({ id: n.id, content: n.text.content ?? "", name: n.name || "" });
  }
  return out;
}

/** Ids of the contact-slot texts ("Phone No. ... / GST NO. ..."), as product_engine detects them. */
export function contactIds(scene) {
  return mapSlots(scene).slots.filter((s) => s.kind === SLOT_CONTACT).map((s) => s.nodeId);
}

/**
 * The board's shop-name text: `remembered` if it still exists, else a `shopname`-tagged text, else a text whose content equals or
 * contains the shop's name (or is contained in it). Contact texts are never picked. Returns { id, source } or null.
 */
export function findShopNameNode(scene, shopName, remembered = null) {
  const texts = textNodes(scene);
  const contacts = new Set(contactIds(scene));
  const pool = texts.filter((t) => !contacts.has(t.id));
  if (remembered && pool.some((t) => t.id === remembered)) return { id: remembered, source: "chosen" };
  const tagged = pool.find((t) => SHOPNAME_TAG.test(t.name));
  if (tagged) return { id: tagged.id, source: "tag" };
  const want = norm(shopName);
  if (want.length >= 3) {
    const exact = pool.find((t) => norm(t.content) === want);
    if (exact) return { id: exact.id, source: "match" };
    const partial = pool.find((t) => {
      const c = norm(t.content);
      return c.length >= 3 && (c.includes(want) || want.includes(c));
    });
    if (partial) return { id: partial.id, source: "match" };
  }
  return null;
}

/** Board text -> textarea value (CorelDRAW separates lines with \r). */
export const toFieldText = (content) => (content || "").replace(/\r\n?/g, "\n");

/** Textarea value -> board text, keeping the line separator the original text used (\r for CorelDRAW text). */
export function toBoardText(value, original) {
  const lines = (value || "").replace(/\r\n?/g, "\n");
  return /\r/.test(original || "") ? lines.replaceAll("\n", "\r") : lines;
}

/** One-line label for a text object in the picker. */
export function textLabel(t, max = 40) {
  const one = (t.content || "").replace(/[\r\n]+/g, " / ").trim() || "(empty text)";
  return one.length > max ? `${one.slice(0, max - 1)}…` : one;
}
