import test from "node:test";
import assert from "node:assert/strict";
import { finishedNotice, publishEdits, savedNotice, waitForExport } from "./publishEdits.js";

const reply = (status, body) => async () => ({ ok: status < 400, status, json: async () => body });

test("publishEdits returns the server's answer", async () => {
  const seen = [];
  const f = async (url, init) => { seen.push([url, init.method]); return { ok: true, status: 200, json: async () => ({ status: "building", export_id: "e1" }) }; };
  assert.deepEqual(await publishEdits("j", "s", f), { status: "building", export_id: "e1" });
  assert.deepEqual(seen, [["/api/editor/j/s/publish", "POST"]]);
});

test("publishEdits never throws: a refusal or a dead server becomes an error result", async () => {
  assert.deepEqual(await publishEdits("j", "s", reply(503, { detail: "not enough free RAM" })), { status: "error", error: "not enough free RAM" });
  assert.equal((await publishEdits("j", "s", async () => { throw new Error("offline"); })).error, "offline");
});

test("savedNotice words each outcome", () => {
  assert.match(savedNotice("A", { status: "building" }), /being rebuilt/);
  assert.match(savedNotice("A", { status: "ready" }), /up to date/);
  assert.match(savedNotice("A", { status: "unchanged" }), /original files/);
  assert.match(savedNotice("A", { status: "error", error: "no RAM" }), /could not be started: no RAM/);
  assert.match(savedNotice("A", undefined), /download button/);
});

test("finishedNotice", () => {
  assert.match(finishedNotice("A", "done"), /rebuilt with your edits/);
  assert.match(finishedNotice("A", "failed", "boom"), /failed: boom/);
});

test("waitForExport polls until the export is done", async () => {
  const answers = [{ status: "queued" }, { status: "running" }, { status: "done" }];
  const f = async () => ({ ok: true, json: async () => answers.shift() });
  const st = await waitForExport("j", "s", "e", { fetchImpl: f, sleep: async () => {} });
  assert.equal(st.status, "done");
  assert.equal(answers.length, 0);
});

test("waitForExport gives up", async () => {
  const f = async () => ({ ok: true, json: async () => ({ status: "running" }) });
  const st = await waitForExport("j", "s", "e", { fetchImpl: f, sleep: async () => {}, limit: 3 });
  assert.equal(st.status, "failed");
});
