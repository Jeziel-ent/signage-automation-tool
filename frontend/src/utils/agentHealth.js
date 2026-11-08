// The launch screen's check: is the Signage Agent running on THIS computer (and is a download offered if not)? It replaces the old
// "Connect CorelDRAW Engine" server check - CorelDRAW lives on the designer's PC, so the agent is what the website connects to.
// Same result shape as utils/corelHealth.js describeHealth(): { state: "ok" | "error" | "offline", title, detail, ... }.

import { probeAgent } from "./agentLocal.js";

export const CONFIG_URL = "/api/agent/config";

const corelText = (list) => (list?.length ? `CorelDRAW ${list.map((c) => c.major).join(" / ")}` : "CorelDRAW not found on this PC");

/**
 * `local`  the agent's /status (or null when none answers), `config`  GET /api/agent/config (or null when the server did not answer).
 * ok       an agent answers here: `paired`/`connected` say how far it is, `corel` its installs, `port` where to reach it.
 * error    no agent here: `download` says a build is on offer, `canContinue` that the site does not need an agent (its server has its
 *          own CorelDRAW), so the designer may go on without one.
 * offline  the website itself did not answer.
 */
export function describeAgent(local, config) {
  if (!config && !local) {
    return { state: "offline", title: "Website not reachable", detail: "The Signage Automation server did not respond. Check the connection and try again.",
      steps: [], download: false, canContinue: false };
  }
  if (local) {
    const corel = local.corel || [];
    const text = corelText(corel);
    const paired = !!local.paired;
    const connected = paired && !!local.connected;
    return {
      state: "ok", agent: true, port: local.port, paired, connected, corel, agentVersion: local.version,
      noCorel: corel.length === 0,
      title: connected ? `Agent connected · ${text}` : paired ? `Agent installed · ${text} · reaching the site...` : `Agent detected · ${text}`,
      detail: paired ? "" : "It connects to your account when you sign in.",
      steps: [], download: false, canContinue: true,
    };
  }
  const download = !!config?.download_available;
  return {
    state: "error", agent: false, download, latestVersion: config?.latest_version || "",
    canContinue: config ? !config.agent_required : false,
    title: "Signage Agent not found on this computer",
    detail: download
      ? "Download and install the agent so this website can use the CorelDRAW on your PC. Extract the zip, double-click SignageAgent.exe, then check again."
      : "No agent is published on this server yet - ask your administrator. Then check again.",
    steps: [],
  };
}

/** Looks for the agent and asks the site what it offers; never throws. */
export async function checkAgentConnection({ probe = probeAgent, fetchImpl = typeof fetch !== "undefined" ? fetch : null } = {}) {
  const configPromise = (async () => {
    try {
      const r = await fetchImpl(CONFIG_URL, { cache: "no-store" });
      return r.ok ? await r.json() : null;
    } catch {
      return null;
    }
  })();
  const [local, config] = await Promise.all([probe(), fetchImpl ? configPromise : null]);
  return { local, config };
}
