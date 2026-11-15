import test from "node:test";
import assert from "node:assert/strict";

// a tiny DOM stand-in: the module only touches localStorage, document.documentElement.dataset and one <meta>
const store = new Map();
globalThis.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };
const meta = { c: "", setAttribute(_, v) { this.c = v; } };
globalThis.document = { documentElement: { dataset: {} }, querySelector: () => meta };

const { themeForTime, readChoice, resolveTheme, applyChoice, CHOICE_KEY } = await import("./theme.js");
const at = (h, m = 0) => new Date(2026, 10, 15, h, m);

test("themeForTime: light from 06:00 until 18:00, dark otherwise", () => {
  assert.equal(themeForTime(at(5, 59)), "dark");
  assert.equal(themeForTime(at(6, 0)), "light");
  assert.equal(themeForTime(at(12)), "light");
  assert.equal(themeForTime(at(17, 59)), "light");
  assert.equal(themeForTime(at(18, 0)), "dark");
  assert.equal(themeForTime(at(23)), "dark");
  assert.equal(themeForTime(at(0)), "dark");
});

test("readChoice defaults to auto and ignores junk", () => {
  assert.equal(readChoice(), "auto");
  store.set(CHOICE_KEY, "purple");
  assert.equal(readChoice(), "auto");
  store.set(CHOICE_KEY, "light");
  assert.equal(readChoice(), "light");
});

test("resolveTheme: auto follows the clock, an explicit pick wins", () => {
  assert.equal(resolveTheme("auto", at(9)), "light");
  assert.equal(resolveTheme("auto", at(21)), "dark");
  assert.equal(resolveTheme("dark", at(9)), "dark");
  assert.equal(resolveTheme("light", at(21)), "light");
});

test("applyChoice paints, saves an explicit pick and forgets it for auto", () => {
  store.clear();
  assert.equal(applyChoice("light", at(22)), "light");
  assert.equal(document.documentElement.dataset.theme, "light");
  assert.equal(meta.c, "#f5f5f4");
  assert.equal(store.get(CHOICE_KEY), "light");
  assert.equal(applyChoice("auto", at(22)), "dark");
  assert.equal(document.documentElement.dataset.theme, "dark");
  assert.equal(store.has(CHOICE_KEY), false);
  assert.equal(applyChoice("nope", at(8)), "light");   // junk = auto
  assert.equal(store.has(CHOICE_KEY), false);
});
