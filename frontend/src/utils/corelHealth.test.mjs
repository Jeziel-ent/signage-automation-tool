import test from "node:test";
import assert from "node:assert/strict";
import { checkCorelConnection, describeHealth, HEALTH_URL } from "./corelHealth.js";

const INSTALL27 = { progid: "CorelDRAW.Application", version: "27.0.0.121" };
const INSTALL21 = { progid: "CorelDRAW.Application.21", version: "21.2.0.706" };

test("ready: names the version a job will use and the fallback", () => {
  const d = describeHealth({ ok: true, engine: "corel", selected: INSTALL27, installs: [INSTALL27, INSTALL21], low_memory: false, free_ram_gb: 3.1, min_free_ram_gb: 1.5 });
  assert.equal(d.state, "ok");
  assert.equal(d.title, "CorelDRAW Connected (v27.0)");
  assert.equal(d.detail, "Fallback: v21.2");
  assert.equal(d.memory, "Memory nominal");
  assert.equal(d.lowMemory, false);
  assert.equal(d.version, "27.0");
  assert.deepEqual(d.badges, [
    { label: "CorelDRAW", value: "v27.0 Active" }, { label: "COM", value: "Registered" },
    { label: "Fallback", value: "v21.2" }, { label: "Memory", value: "Nominal", warn: false },
  ]);
});

test("low memory is a warning, not a failure", () => {
  const d = describeHealth({ ok: true, engine: "corel", selected: INSTALL27, installs: [INSTALL27], low_memory: true, free_ram_gb: 1.2, min_free_ram_gb: 1.5 });
  assert.equal(d.state, "ok");
  assert.ok(d.lowMemory);
  assert.deepEqual(d.badges.at(-1), { label: "Memory", value: "1.2 GB free", warn: true });
  assert.match(d.memory, /1\.2 GB free/);
});

test("no install -> error with the server's reason", () => {
  const d = describeHealth({ ok: false, engine: "corel", installs: [], message: "No CorelDRAW installation found on the server." });
  assert.equal(d.state, "error");
  assert.equal(d.detail, "No CorelDRAW installation found on the server.");
  assert.ok(d.steps.length >= 2 && d.steps.some((s) => /Install CorelDRAW/.test(s)));
  assert.equal(d.pill, "CorelDRAW not found");
});

test("server down -> offline", () => {
  assert.equal(describeHealth({ unreachable: true }).state, "offline");
  assert.ok(describeHealth({ unreachable: true }).steps.some((s) => /backend/.test(s)));
  assert.equal(describeHealth({ unreachable: true }).pill, "Server unreachable");
  assert.equal(describeHealth(null).state, "offline");
});

test("mock engine is ready without CorelDRAW", () => {
  assert.equal(describeHealth({ ok: true, engine: "mock", installs: [] }).title, "Demo engine ready");
});

test("checkCorelConnection never throws and uses the proxied relative URL", async () => {
  const seen = [];
  const ok = await checkCorelConnection({ fetchImpl: async (u) => {
    seen.push(u);
    return { ok: true, json: async () => ({ ok: true }) };
  } });
  assert.deepEqual(ok, { ok: true });
  assert.deepEqual(seen, [HEALTH_URL]);
  assert.deepEqual(await checkCorelConnection({ fetchImpl: async () => { throw new TypeError("Failed to fetch"); } }), { unreachable: true });
  assert.deepEqual(await checkCorelConnection({ fetchImpl: async () => ({ ok: false, status: 502 }) }), { unreachable: true });
  assert.deepEqual(await checkCorelConnection({ fetchImpl: null }), { unreachable: true });
});
