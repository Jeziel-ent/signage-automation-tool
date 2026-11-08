import test from "node:test";
import assert from "node:assert/strict";
import { actionLabel, describeEntry, entryDetails, storageRows } from "./serverView.js";

test("an entry reads as a sentence", () => {
  assert.equal(describeEntry({ user_email: "boss@x.com", action: "login" }), "boss@x.com signed in");
  assert.equal(describeEntry({ user_email: "boss@x.com", action: "master_deleted", target: "Hangyo / m.cdr" }), "boss@x.com deleted the master Hangyo / m.cdr");
});

test("a failed sign-in names the email that was tried, not the (anonymous) caller", () => {
  assert.equal(describeEntry({ user_email: "anonymous", action: "login_failed", target: "dee@x.com" }), "dee@x.com failed to sign in");
  assert.match(describeEntry({ user_email: "anonymous", action: "login_locked", target: "dee@x.com" }), /locked out/);
});

test("an unknown action still reads", () => {
  assert.equal(actionLabel("some_new_thing"), "some new thing");
  assert.equal(describeEntry({ user_email: "", action: "x_y" }), "someone x y");
});

test("details list what changed without ever showing a password", () => {
  assert.equal(entryDetails({ detail: { changed: ["password", "role"], role: "admin" } }), "changed: password, role · role: admin");
  assert.equal(entryDetails({ detail: { active: false } }), "disabled");
  assert.equal(entryDetails({ detail: { fields: ["master_name"], was: "old.cdr" } }), "fields: master_name · was old.cdr");
  assert.equal(entryDetails({}), "");
});

test("storage rows are biggest first, skip empty categories and carry their share", () => {
  const rows = storageRows({ masters: 100, boards: 300, scenes: 0, exports: 600 }, 1000);
  assert.deepEqual(rows.map((r) => r.key), ["exports", "boards", "masters"]);
  assert.equal(rows[0].share, 0.6);
  assert.equal(rows[0].label, "Exports");
  assert.deepEqual(storageRows(undefined, 0), []);
});
