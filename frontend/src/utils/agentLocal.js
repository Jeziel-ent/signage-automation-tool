// Talking to the Signage Agent installed on THIS computer: it listens on 127.0.0.1 (ports 47890-47899), answers GET /status and takes
// POST /pair {code}. The agent only grants CORS access to the site it was downloaded from, so a different page just sees "not found".

export const AGENT_PORTS = [47890, 47891, 47892, 47893, 47894, 47895, 47896, 47897, 47898, 47899];
const PROBE_TIMEOUT_MS = 1500;

async function fetchJson(url, init = {}, timeoutMs = PROBE_TIMEOUT_MS) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { ...init, signal: ctl.signal, cache: "no-store" });
    return { ok: r.ok, status: r.status, body: await r.json().catch(() => ({})) };
  } finally {
    clearTimeout(timer);
  }
}

/** The local agent's status ({agent, version, paired, connected, corel, ...}) plus its `port`, or null when nothing answers. */
export async function probeAgent(ports = AGENT_PORTS) {
  const tries = ports.map(async (port) => {
    const r = await fetchJson(`http://127.0.0.1:${port}/status`);
    if (!r.ok || r.body?.agent !== "adinn-signage-agent") throw new Error("not the agent");
    return { ...r.body, port };
  });
  try {
    return await Promise.any(tries);
  } catch {
    return null;
  }
}

/** Hand the pairing code to the local agent. Resolves {ok:true,name} or throws Error(message). */
export async function pairLocalAgent(port, code) {
  let r;
  try {
    r = await fetchJson(`http://127.0.0.1:${port}/pair`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) }, 20000);
  } catch {
    throw new Error("The agent on this computer did not answer. Is it running?");
  }
  if (!r.ok) throw new Error(r.body?.detail || `The agent refused (${r.status})`);
  return r.body;
}

export async function unpairLocalAgent(port) {
  await fetchJson(`http://127.0.0.1:${port}/unpair`, { method: "POST" });
}

/**
 * What to tell the designer, from what the page can see.
 *   local   the local agent's status or null          servers  this user's agents from /api/agents
 *   -> "connected"   the agent on THIS PC is paired and reaching the site
 *      "needs_pair"  installed and running here, not connected to this account yet
 *      "reconnecting" paired here but not reaching the site right now
 *      "not_running" no agent answers here, though this account has agents (another PC, or it is not running)
 *      "not_installed" no agent here and none on this account
 *      "remote_online" the browser cannot see a local agent (blocked) but one of the account's agents is online
 */
export function agentPhase(local, servers) {
  if (local) {
    if (!local.paired) return "needs_pair";
    return local.connected ? "connected" : "reconnecting";
  }
  const mine = servers || [];
  if (mine.some((a) => a.online)) return "remote_online";
  return mine.length ? "not_running" : "not_installed";
}

export const PHASE_NEEDS_ACTION = new Set(["not_installed", "not_running", "needs_pair"]);
