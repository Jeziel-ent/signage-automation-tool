import test from "node:test";
import assert from "node:assert/strict";
import { galleryImages } from "./gallery.js";

const shop = { id: "s1", job_id: "j1" };

test("only the newest finished export is shown (PNG preferred), with the cache-buster", () => {
  const status = { files: { preview: "76 - 6 X 6 Feet - Nonlit - KALKEE.png" }, completed_at: 100 };
  const exports = [
    { export_id: "e1", status: "done", files: { png: "a.png", pdf: "a.pdf" }, completed_at: 200 },
    { export_id: "e2", status: "done", files: { jpeg: "b.jpeg", cdr: "b.cdr" }, completed_at: 300 },
    { export_id: "e3", status: "failed", files: { png: "c.png" }, completed_at: 400 },
    { export_id: "e4", status: "running", files: null },
  ];
  const imgs = galleryImages(shop, status, exports, 123);
  assert.deepEqual(imgs.map((i) => i.key), ["e2-jpeg"]);
  assert.equal(imgs[0].url, "/api/editor/j1/s1/exports/e2/files/b.jpeg?v=123");
  assert.equal(imgs[0].label, "Edited version");
  const both = galleryImages(shop, status, [{ export_id: "e9", status: "done", files: { jpeg: "z.jpg", png: "z.png" }, completed_at: 500 }], 5);
  assert.deepEqual(both.map((i) => i.key), ["e9-png"]);
});

test("a board that was never exported shows the conversion preview", () => {
  const status = { files: { preview: "76 - 6 X 6 Feet - Nonlit - KALKEE.png" }, completed_at: 100 };
  const imgs = galleryImages(shop, status, [{ export_id: "e3", status: "failed", files: { png: "c.png" } }], 123);
  assert.deepEqual(imgs.map((i) => i.key), ["conversion"]);
  assert.equal(imgs[0].url, "/api/v2/shops/s1/files/76%20-%206%20X%206%20Feet%20-%20Nonlit%20-%20KALKEE.png?v=123");
});

test("no preview and no exports: nothing to show", () => {
  assert.deepEqual(galleryImages(shop, { files: null }, [], 1), []);
});

test("queue gallery search: English or Tamil name, or board type", async () => {
  const { galleryItems } = await import("./gallery.js");
  const shops = [
    { id: "1", name: "Sri Sai Cafe", shop_name_local: "ஸ்ரீ சாய் கஃபே", board_type: "Double Side GSB" },
    { id: "2", name: "Jothi Maligai", shop_name_local: "ஜோதி மளிகை", board_type: "Nonlit" },
  ];
  assert.deepEqual(galleryItems(shops, "").map((s) => s.id), ["1", "2"]);
  assert.deepEqual(galleryItems(shops, "jothi").map((s) => s.id), ["2"]);
  assert.deepEqual(galleryItems(shops, "சாய்").map((s) => s.id), ["1"]);
  assert.deepEqual(galleryItems(shops, "GSB").map((s) => s.id), ["1"]);
  assert.deepEqual(galleryItems(shops, "xyz"), []);
});
