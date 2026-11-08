import test from "node:test";
import assert from "node:assert/strict";
import { checkAgentConnection, describeAgent } from "./agentHealth.js";

const COREL = [{ progid: "CorelDRAW.Application.27", major: 27 }];

test("a paired, connected agent is ok and names its CorelDRAW", () => {
  const h = describeAgent({ paired: true, connected: true, corel: COREL, version: "0.1.0", port: 47890 }, { agent_required: true });
  assert.equal(h.state, "ok");
  assert.equal(h.connected, true);
  assert.equal(h.port, 47890);
  assert.match(h.title, /Agent connected · CorelDRAW 27/);
});

test("an agent that is not paired yet is ok and says it connects at sign-in", () => {
  const h = describeAgent({ paired: false, connected: false, corel: COREL, port: 47891 }, {});
  assert.equal(h.state, "ok");
  assert.equal(h.paired, false);
  assert.match(h.title, /Agent detected/);
  assert.match(h.detail, /sign in/);
});

test("a paired agent that cannot reach the site yet is still ok but not connected", () => {
  const h = describeAgent({ paired: true, connected: false, corel: COREL, port: 47890 }, {});
  assert.equal(h.state, "ok");
  assert.equal(h.connected, false);
  assert.match(h.title, /reaching the site/);
});

test("an agent on a PC without CorelDRAW is flagged", () => {
  const h = describeAgent({ paired: true, connected: true, corel: [], port: 47890 }, {});
  assert.equal(h.noCorel, true);
  assert.match(h.title, /CorelDRAW not found/);
});

test("no agent here: error with a download when one is published", () => {
  const h = describeAgent(null, { download_available: true, latest_version: "0.1.0", agent_required: true });
  assert.equal(h.state, "error");
  assert.equal(h.download, true);
  assert.equal(h.canContinue, false);   // the site needs an agent
  assert.match(h.detail, /Download/);
});

test("no agent here but the site does not need one: the designer may continue", () => {
  assert.equal(describeAgent(null, { download_available: true, agent_required: false }).canContinue, true);
});

test("no agent and nothing published says to ask the administrator", () => {
  const h = describeAgent(null, { download_available: false, agent_required: true });
  assert.equal(h.download, false);
  assert.match(h.detail, /administrator/);
});

test("neither the agent nor the site answers: offline", () => {
  assert.equal(describeAgent(null, null).state, "offline");
});

test("the site not answering but an agent present still reports the agent", () => {
  assert.equal(describeAgent({ paired: false, corel: COREL, port: 1 }, null).state, "ok");
});

test("checkAgentConnection combines the probe and the public config and never throws", async () => {
  const fetchImpl = async () => ({ ok: true, json: async () => ({ download_available: true }) });
  const r = await checkAgentConnection({ probe: async () => null, fetchImpl });
  assert.deepEqual(r, { local: null, config: { download_available: true } });
  const failing = await checkAgentConnection({ probe: async () => null, fetchImpl: async () => { throw new Error("down"); } });
  assert.deepEqual(failing, { local: null, config: null });
});
