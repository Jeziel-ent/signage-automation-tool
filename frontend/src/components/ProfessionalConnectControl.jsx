import { AlertTriangle, ArrowRight, CheckCircle2, Link2, Loader2, RefreshCw } from "lucide-react";
import SpecularButton from "./SpecularButton.jsx";

// the launch screen's primary buttons: solid brand-red face (baseColor transparent + tintOpacity 1), a slow continuous
// shine, and a thin moving highlight streak as the only edge light - no static outline
const PRIMARY = {
  size: "lg",
  radius: 18,
  tint: "#dc2626",
  tintOpacity: 1,
  baseColor: "transparent",
  textColor: "#ffffff",
  lineColor: "#ffffff",
  thickness: 1,
  intensity: 1,
  shineSize: 20,
  shineFade: 30,
  speed: 0.35,
  autoAnimate: true,
};

/**
 * The launch screen's one control - no card around it: the standalone specular button, whose surroundings change in place.
 * Not auto-focused (that drew the focus ring round it on load): SplashScreen's Enter/Space handler presses the primary action.
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
    <div className="pcc" aria-live="polite">
      {state === "idle" && (
        <SpecularButton {...PRIMARY} className="pcc-primary" onClick={onConnect} disabled={disabled}>
          <Link2 size={20} aria-hidden="true" />
          <span>Connect CorelDRAW Engine</span>
        </SpecularButton>
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
          <SpecularButton key="start" {...PRIMARY} className="pcc-start" onClick={onStart} disabled={disabled}>
            <span>Start Automation</span>
            <ArrowRight size={18} className="pcc-arrow" aria-hidden="true" />
          </SpecularButton>
        </div>
      )}

      {failed && (
        <div className="pcc-stack pcc-enter">
          <p className="pcc-status err" role="alert">
            <AlertTriangle size={14} aria-hidden="true" />
            <span>{health.title}</span>
          </p>
          <p className="pcc-note">{health.detail}</p>
          <SpecularButton key="retry" {...PRIMARY} className="pcc-primary" onClick={onConnect} disabled={disabled}>
            <RefreshCw size={16} aria-hidden="true" />
            <span>Retry connection</span>
          </SpecularButton>
          <button type="button" className="pcc-link" onClick={onStart} disabled={disabled}>
            Continue without CorelDRAW
          </button>
        </div>
      )}
    </div>
  );
}
