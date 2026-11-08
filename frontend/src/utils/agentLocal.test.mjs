import test from "node:test";
import assert from "node:assert/strict";
import { agentPhase, probeAgent, PHASE_NEEDS_ACTION } from "./agentLocal.js";

test("nothing local and nothing on the account: not installed", () => {
  assert.equal(agentPhase(null, []), "not_installed");
  assert.equal(agentPhase(null, undefined), "not_installed");
});

test("agents on the account but none answering here: not running", () => {
  assert.equal(agentPhase(null, [{ online: false }]), "not_running");
});

test("the browser cannot see a local agent but one is online on the account", () => {
  assert.equal(agentPhase(null, [{ online: false }, { online: true }]), "remote_online");
});

test("a local agent that is not paired asks to be connected", () => {
  assert.equal(agentPhase({ paired: false, connected: false }, []), "needs_pair");
});

test("a paired local agent is connected or reconnecting", () => {
  assert.equal(agentPhase({ paired: true, connected: true }, []), "connected");
  assert.equal(agentPhase({ paired: true, connected: false }, []), "reconnecting");
});

test("only the phases the designer can act on raise the banner", () => {
  for (const p of ["not_installed", "not_running", "needs_pair"]) assert.ok(PHASE_NEEDS_ACTION.has(p));
  for (const p of ["connected", "reconnecting", "remote_online"]) assert.ok(!PHASE_NEEDS_ACTION.has(p));
});

test("probeAgent answers null when no port answers", async () => {
  const saved = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("connection refused"); };
  try {
    assert.equal(await probeAgent([1, 2]), null);
  } finally {
    globalThis.fetch = saved;
  }
});

test("probeAgent picks the port that answers with the agent signature and ignores other services", async () => {
  const saved = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes(":2/")) return { ok: true, status: 200, json: async () => ({ agent: "adinn-signage-agent", version: "1.0.0" }) };
    return { ok: true, status: 200, json: async () => ({ hello: "some other local service" }) };
  };
  try {
    const r = await probeAgent([1, 2, 3]);
    assert.equal(r.port, 2);
    assert.equal(r.version, "1.0.0");
  } finally {
    globalThis.fetch = saved;
  }
});
