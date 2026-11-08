import { useState } from "react";
import { NavLink } from "react-router-dom";
import { Download, Link2, Loader2, X } from "lucide-react";
import { useAgent } from "../context/AgentContext.jsx";
import { PHASE_NEEDS_ACTION } from "../utils/agentLocal.js";
import "./Agent.css";

const DISMISS_KEY = "signage.agentBannerDismissed";
function dismissedBefore() {
  try {
    return sessionStorage.getItem(DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}

/** Strip across the top of the workspace: recommends installing the agent (or connecting this PC) when the site needs one and this
 *  computer has none connected. Hidden once connected; "x" hides it for this browser session. The Agent page always has the details. */
export default function AgentBanner() {
  const { phase, config, busy, connect, error } = useAgent();
  const [hidden, setHidden] = useState(dismissedBefore);
  if (hidden || !config?.agent_required || !PHASE_NEEDS_ACTION.has(phase)) return null;

  const hide = () => {
    setHidden(true);
    try { sessionStorage.setItem(DISMISS_KEY, "1"); } catch { /* storage blocked: it only comes back on reload */ }
  };

  return (
    <div className="agent-banner" role="status">
      {phase === "needs_pair" ? (
        <>
          <span><b>Signage Agent detected on this computer.</b> Connect it to your account to use CorelDRAW from the website.</span>
          <button type="button" className="btn small" onClick={connect} disabled={busy}>
            {busy ? <Loader2 size={14} className="spin" /> : <Link2 size={14} />} Connect this PC
          </button>
        </>
      ) : (
        <>
          <span>
            <b>{phase === "not_running" ? "The Signage Agent is not running on this computer." : "Install the Signage Agent."}</b>{" "}
            It connects the website to the CorelDRAW on this PC{config.download_available ? "" : " (not published on this server yet)"}.
          </span>
          {config.download_available && (
            <a className="btn small" href="/api/agent/download" download>
              <Download size={14} /> Download agent
            </a>
          )}
          <NavLink className="btn small ghost" to="/agent">How it works</NavLink>
        </>
      )}
      {error && <span className="agent-banner-err">{error}</span>}
      <button type="button" className="agent-banner-x" onClick={hide} aria-label="Hide this message"><X size={15} /></button>
    </div>
  );
}
