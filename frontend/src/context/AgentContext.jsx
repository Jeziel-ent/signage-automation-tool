import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { agentPhase, pairLocalAgent, probeAgent, unpairLocalAgent } from "../utils/agentLocal.js";
import { readError } from "../utils/authApi.js";

/** { phase, local, agents, config, busy, error, connect(), disconnect(id), refresh() } - see utils/agentLocal.js agentPhase for the phases. */
export const AgentContext = createContext({
  phase: "unknown", local: null, agents: [], config: null, busy: false, error: "", connect: async () => {}, disconnect: async () => {}, refresh: async () => {},
});
export const useAgent = () => useContext(AgentContext);

const FAST_MS = 3000;   // while the designer still has to act (install, connect): notice a new agent quickly
const SLOW_MS = 10000;

/** Polls the agent on this computer and the site's list of the user's agents, and offers "connect this PC". Mounted once in the Shell. */
export function AgentProvider({ children }) {
  const [local, setLocal] = useState(null);
  const [agents, setAgents] = useState([]);
  const [config, setConfig] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    const [l, a] = await Promise.all([
      probeAgent(),
      fetch("/api/agents").then((r) => (r.ok ? r.json() : { agents: [] })).catch(() => ({ agents: [] })),
    ]);
    if (!alive.current) return;
    setLocal(l);
    setAgents(a.agents || []);
    setLoaded(true);
  }, []);

  useEffect(() => {
    alive.current = true;
    fetch("/api/agent/config").then((r) => (r.ok ? r.json() : null)).then((c) => alive.current && setConfig(c)).catch(() => {});
    return () => { alive.current = false; };
  }, []);

  const phase = loaded ? agentPhase(local, agents) : "unknown";
  const needsAction = phase !== "connected" && phase !== "remote_online" && phase !== "unknown";
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, needsAction ? FAST_MS : SLOW_MS);
    return () => clearInterval(timer);
  }, [refresh, needsAction]);

  const connect = useCallback(async () => {
    if (!local) return;
    setBusy(true);
    setError("");
    try {
      const r = await fetch("/api/agent/pairing-code", { method: "POST" });
      if (!r.ok) throw new Error(await readError(r, "Could not get a pairing code"));
      const { code } = await r.json();
      await pairLocalAgent(local.port, code);
      await refresh();
    } catch (e) {
      setError(e.message || "Could not connect the agent");
    } finally {
      setBusy(false);
    }
  }, [local, refresh]);

  const disconnect = useCallback(async (id) => {
    setBusy(true);
    setError("");
    try {
      const r = await fetch(`/api/agents/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await readError(r, "Could not disconnect the agent"));
      if (local) await unpairLocalAgent(local.port).catch(() => {});
      await refresh();
    } catch (e) {
      setError(e.message || "Could not disconnect the agent");
    } finally {
      setBusy(false);
    }
  }, [local, refresh]);

  const value = useMemo(() => ({ phase, local, agents, config, busy, error, connect, disconnect, refresh }),
    [phase, local, agents, config, busy, error, connect, disconnect, refresh]);
  return <AgentContext.Provider value={value}>{children}</AgentContext.Provider>;
}
