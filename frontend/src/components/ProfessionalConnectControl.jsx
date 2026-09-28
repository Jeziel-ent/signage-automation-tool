import { AlertTriangle, ArrowRight, CheckCircle2, Link2, Loader2, RefreshCw } from "lucide-react";

/**
 * The launch screen's one control: a dark-glass panel that changes in place - no popups.
 *
 *   idle        "Connect CorelDRAW Engine"
 *   checking    spinner + "Establishing local host connection..."
 *   ok          green status line + "Start Automation"
 *   error/offline  the reason, "Retry connection" and "Continue without CorelDRAW"
 *
 * `health` is `describeHealth()`'s result (utils/corelHealth.js) or { state: "idle" | "checking" } - the parent runs the real
 * GET /api/corel/health check; this component only renders it.
 */
export default function ProfessionalConnectControl({ health, onConnect, onStart, disabled = false }) {
  const { state } = health;
  const failed = state === "error" || state === "offline";
  return (
    <div className="pcc">
      <div className="pcc-panel" aria-live="polite">
        {state === "idle" && (
          <button type="button" className="pcc-btn pcc-primary" onClick={onConnect} disabled={disabled} autoFocus>
            <Link2 size={16} aria-hidden="true" />
            <span>Connect CorelDRAW Engine</span>
          </button>
        )}

        {state === "checking" && (
          <div className="pcc-status-box" role="status">
            <Loader2 size={16} className="pcc-spin" aria-hidden="true" />
            <span>Establishing local host connection...</span>
          </div>
        )}

        {state === "ok" && (
          <div className="pcc-stack pcc-enter">
            <p className="pcc-status ok">
              <CheckCircle2 size={14} aria-hidden="true" />
              <span>{health.version ? `CorelDRAW ${health.version} engine connected` : health.title}</span>
            </p>
            {health.lowMemory && <p className="pcc-note warn">{health.memory}</p>}
            <button key="start" type="button" className="pcc-btn pcc-start" onClick={onStart} disabled={disabled} autoFocus>
              <span>Start Automation</span>
              <ArrowRight size={16} className="pcc-arrow" aria-hidden="true" />
            </button>
          </div>
        )}

        {failed && (
          <div className="pcc-stack pcc-enter">
            <p className="pcc-status err" role="alert">
              <AlertTriangle size={14} aria-hidden="true" />
              <span>{health.title}</span>
            </p>
            <p className="pcc-note">{health.detail}</p>
            <button key="retry" type="button" className="pcc-btn pcc-primary" onClick={onConnect} disabled={disabled} autoFocus>
              <RefreshCw size={15} aria-hidden="true" />
              <span>Retry connection</span>
            </button>
            <button type="button" className="pcc-link" onClick={onStart} disabled={disabled}>
              Continue without CorelDRAW
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
