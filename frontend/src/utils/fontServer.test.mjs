import test from "node:test";
import assert from "node:assert/strict";
import { fetchServerFont } from "./fontLoader.js";

const ok = (bytes) => ({ ok: true, status: 200, arrayBuffer: async () => bytes });
const notFound = (pending) => ({ ok: false, status: 404, headers: { get: (k) => (pending && k === "X-Font-Pending" ? "1" : null) } });
const noSleep = async () => {};

test("a font the site has is returned at once", async () => {
  assert.equal(await fetchServerFont("/f", { fetchImpl: async () => ok("BYTES"), sleep: noSleep }), "BYTES");
});

test("a font still being fetched from the PC is asked for again until it arrives", async () => {
  const answers = [notFound(true), notFound(true), ok("BYTES")];
  let waits = 0;
  const data = await fetchServerFont("/f", { fetchImpl: async () => answers.shift(), sleep: async () => { waits += 1; } });
  assert.equal(data, "BYTES");
  assert.equal(waits, 2);
});

test("a plain 404 means there is no such font: no waiting", async () => {
  let calls = 0;
  const data = await fetchServerFont("/f", { fetchImpl: async () => { calls += 1; return notFound(false); }, sleep: noSleep });
  assert.equal(data, null);
  assert.equal(calls, 1);
});

test("it gives up after the allowed number of tries", async () => {
  let calls = 0;
  const data = await fetchServerFont("/f", { fetchImpl: async () => { calls += 1; return notFound(true); }, sleep: noSleep, tries: 4 });
  assert.equal(data, null);
  assert.equal(calls, 4);
});

test("a network failure ends the wait", async () => {
  assert.equal(await fetchServerFont("/f", { fetchImpl: async () => { throw new Error("offline"); }, sleep: noSleep }), null);
});
