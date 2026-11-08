import test from "node:test";
import assert from "node:assert/strict";
import { isSessionExpired, readError } from "./authApi.js";

test("a 401 from an API call means the session ended", () => {
  assert.equal(isSessionExpired("/api/masters", 401), true);
  assert.equal(isSessionExpired("http://x/api/v2/recent", 401), true);
});

test("a wrong password (401 on login) and other statuses are not a session end", () => {
  assert.equal(isSessionExpired("/api/auth/login", 401), false);
  assert.equal(isSessionExpired("/api/masters", 403), false);
  assert.equal(isSessionExpired("/assets/app.js", 401), false);
});

test("readError takes the server's message, else the fallback", async () => {
  assert.equal(await readError({ json: async () => ({ detail: "wrong email or password" }) }, "x"), "wrong email or password");
  assert.equal(await readError({ json: async () => ({ detail: [{ msg: "enter a valid email address" }] }) }, "x"), "enter a valid email address");
  assert.equal(await readError({ json: async () => { throw new Error("no json"); } }, "fallback"), "fallback");
});
