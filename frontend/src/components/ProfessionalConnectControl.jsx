import { useState } from "react";
import { AlertTriangle, ArrowRight, CheckCircle2, Download, Link2, Loader2, RefreshCw } from "lucide-react";
import SpecularButton from "./SpecularButton.jsx";
import GlassLogin from "./GlassLogin.jsx";

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
 * Not auto-focused: SplashScreen's Enter/Space handler presses the primary action.
 *
 *   idle          "Connect Agent"
 *   checking      spinner + "Looking for the Signage Agent on this computer..."
 *   ok            green status line (agent detected / connected, with its CorelDRAW) + "Start Automation" - or, when `login` is given
 *                 (a sign-in is required and nobody is signed in), the glass sign-in card whose submit button is "Start Automation"
 *   error         "agent not found": "Download agent" (when the site offers one), "Check again" and, only when the site does not
 *                 need an agent, "Continue without the agent" (which also asks for the sign-in when needed)
 *   offline       the site did not answer: "Check again"
 *
 * `health` is `describeAgent()`'s result (utils/agentHealth.js) or { state: "idle" | "checking" } - the parent runs the real check.
 */
// `login` = { onLogin(email, password, name) -> Promise<string | null>, needsSetup } while a sign-in is required, else undefined
export default function ProfessionalConnectControl({ health, onConnect, onStart, disabled = false, login }) {
  const { state } = health;
  const [loginAsked, setLoginAsked] = useState(false); // "Continue without the agent" pressed while a sign-in is required
  const failed = (state === "error" || state === "offline") && !(login && loginAsked);
  const card = login && <GlassLogin onLogin={login.onLogin} needsSetup={login.needsSetup} disabled={disabled} />;

  return (
    <div className="pcc" aria-live="polite">
      {state === "idle" && (
        <SpecularButton {...PRIMARY} className="pcc-primary" onClick={onConnect} disabled={disabled}>
          <Link2 size={20} aria-hidden="true" />
          <span>Connect Agent</span>
        </SpecularButton>
      )}

      {state === "checking" && (
        <div className="pcc-status-box" role="status">
          <Loader2 size={16} className="pcc-spin" aria-hidden="true" />
          <span>Looking for the Signage Agent on this computer...</span>
        </div>
      )}

      {state === "ok" && (
        <div className="pcc-stack pcc-enter">
          <p className="pcc-status ok">
            <CheckCircle2 size={14} aria-hidden="true" />
            <span>{health.title}</span>
          </p>
          {health.detail && <p className="pcc-note">{health.detail}</p>}
          {health.noCorel && <p className="pcc-note warn">CorelDRAW was not found on this computer - conversions will not run until it is installed.</p>}
          {login ? card : <SpecularButton key="start" {...PRIMARY} className="pcc-start" onClick={onStart} disabled={disabled}>
            <span>Start Automation</span>
            <ArrowRight size={18} className="pcc-arrow" aria-hidden="true" />
          </SpecularButton>}
        </div>
      )}

      {(state === "error" || state === "offline") && login && loginAsked && (
        <div className="pcc-stack pcc-enter">
          <p className="pcc-note warn">No agent on this computer - CorelDRAW will run on the server instead.</p>
          {card}
        </div>
      )}

      {failed && (
        <div className="pcc-stack pcc-enter">
          <p className="pcc-status err" role="alert">
            <AlertTriangle size={14} aria-hidden="true" />
            <span>{health.title}</span>
          </p>
          <p className="pcc-note">{health.detail}</p>
          {health.download && (
            <SpecularButton {...PRIMARY} className="pcc-primary" onClick={() => { window.location.assign("/api/agent/download"); }} disabled={disabled}>
              <Download size={18} aria-hidden="true" />
              <span>Download agent{health.latestVersion ? ` (v${health.latestVersion})` : ""}</span>
            </SpecularButton>
          )}
          <button type="button" className={health.download ? "pcc-link" : "pcc-primary-link"} onClick={onConnect} disabled={disabled}>
            <RefreshCw size={12} aria-hidden="true" /> Check again
          </button>
          {health.canContinue && (
            <button type="button" className="pcc-link" onClick={login ? () => setLoginAsked(true) : onStart} disabled={disabled}>
              Continue without the agent
            </button>
          )}
        </div>
      )}
    </div>
  );
}
