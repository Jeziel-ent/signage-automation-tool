import { CheckCircle2, CircleDashed, Download, Link2, Loader2, MonitorCheck, RefreshCw, Unplug } from "lucide-react";
import { useAgent } from "../context/AgentContext.jsx";
import { fmtBytes } from "../utils/fileSize.js";
import "../components/Agent.css";

const HEADLINE = {
  unknown: ["Checking this computer...", "neutral"],
  connected: ["Agent connected", "ok"],
  reconnecting: ["Agent installed - reaching the site...", "warn"],
  needs_pair: ["Agent detected - not connected to your account yet", "warn"],
  not_running: ["The agent is not running on this computer", "bad"],
  not_installed: ["The agent is not installed on this computer", "bad"],
  remote_online: ["An agent of yours is online", "ok"],
};

function corelLabel(list) {
  if (!list?.length) return "CorelDRAW not found";
  return list.map((c) => `CorelDRAW ${c.major}${c.version ? ` (${c.version})` : ""}`).join(", ");
}

function ago(epochSeconds) {
  if (!epochSeconds) return "never";
  const s = Math.max(0, Math.round(Date.now() / 1000 - epochSeconds));
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  return new Date(epochSeconds * 1000).toLocaleString();
}

/** The agent page: where this computer stands, the three steps, and the PCs connected to this account. */
export default function AgentPage() {
  const { phase, local, agents, config, busy, error, connect, disconnect, refresh } = useAgent();
  const [title, tone] = HEADLINE[phase] || HEADLINE.unknown;
  const installed = phase === "connected" || phase === "reconnecting" || phase === "needs_pair";
  const canDownload = !!config?.download_available;

  return (
    <div className="agent-page">
      <h2>Signage Agent</h2>
      <p className="agent-lead">
        A small Windows program that connects this website to the CorelDRAW installed on a designer PC. It makes outbound connections only,
        starts with Windows and updates itself.
      </p>

      <section className={"ws-card agent-status " + tone} aria-live="polite">
        <div className="agent-status-head">
          {phase === "unknown" ? <Loader2 size={20} className="spin" /> : tone === "ok" ? <CheckCircle2 size={20} /> : <CircleDashed size={20} />}
          <strong>{title}</strong>
          <button type="button" className="icon-btn" onClick={refresh} title="Check again" aria-label="Check again"><RefreshCw size={15} /></button>
        </div>
        {local && (
          <p className="agent-meta">
            Version {local.version} · {corelLabel(local.corel)}
            {local.name ? ` · connected as "${local.name}"` : ""}
            {local.last_error ? ` · ${local.last_error}` : ""}
          </p>
        )}
        {phase === "needs_pair" && (
          <button type="button" className="btn" onClick={connect} disabled={busy}>
            {busy ? <Loader2 size={16} className="spin" /> : <Link2 size={16} />} Connect this PC
          </button>
        )}
        {error && <p className="agent-err" role="alert">{error}</p>}
      </section>

      <ol className="agent-steps">
        <li className={installed || phase === "remote_online" ? "done" : ""}>
          <div>
            <b>1. Download</b>
            <p>{canDownload ? `Version ${config.latest_version}${config.size ? `, ${fmtBytes(config.size)}` : ""}. The zip already knows this website.` : "No agent has been published on this server yet - ask your administrator."}</p>
          </div>
          {canDownload && <a className="btn small" href="/api/agent/download" download><Download size={14} /> Download agent</a>}
        </li>
        <li className={installed ? "done" : ""}>
          <div>
            <b>2. Extract and run</b>
            <p>Extract the whole zip into a folder and double-click <code>SignageAgent.exe</code>. It installs itself for your Windows user (no administrator
              rights) and starts with Windows. Windows may show a SmartScreen warning the first time: choose "More info" then "Run anyway".</p>
          </div>
        </li>
        <li className={phase === "connected" ? "done" : ""}>
          <div>
            <b>3. Connect</b>
            <p>This page finds the agent by itself and offers <i>Connect this PC</i>. Your browser may ask to allow this site to reach devices on your
              local network - allow it, the agent lives on this computer.</p>
          </div>
        </li>
      </ol>

      <section className="ws-card agent-list">
        <h3><MonitorCheck size={17} /> PCs connected to your account</h3>
        {agents.length === 0 ? <p className="agent-empty">None yet.</p> : (
          <table className="agent-table">
            <thead><tr><th>PC</th><th>Status</th><th>CorelDRAW</th><th>Agent</th><th /></tr></thead>
            <tbody>
              {agents.map((a) => (
                <tr key={a.id}>
                  <td>{a.name}</td>
                  <td><span className={"agent-dot " + (a.online ? "on" : "off")} />{a.online ? "online" : `offline, seen ${ago(a.last_seen)}`}</td>
                  <td>{corelLabel(a.corel)}</td>
                  <td>{a.version || "?"}{a.update_available ? " (updating)" : ""}</td>
                  <td>
                    <button type="button" className="btn ghost small" onClick={() => disconnect(a.id)} disabled={busy}><Unplug size={14} /> Disconnect</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
