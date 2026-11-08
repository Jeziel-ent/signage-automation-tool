import { useState } from "react";
import { ArrowRight, Loader2, Lock, TriangleAlert, User } from "lucide-react";
import SpecularButton from "./SpecularButton.jsx";
import "./GlassLogin.css";

const BUTTON = {
  size: "lg", radius: 22, tint: "#dc2626", tintOpacity: 1, baseColor: "transparent", textColor: "#ffffff",
  lineColor: "#ffffff", thickness: 1, intensity: 1, shineSize: 20, shineFade: 30, speed: 0.35, autoAnimate: true,
};

/**
 * The launch screen's sign-in card (frosted glass over the city). The submit button IS "Start Automation": it signs in and, when
 * the server accepts the account, `onLogin` starts the fly-in. A wrong email or password (or a locked / disabled account) shows
 * the server's message as a warning inside the card and nothing starts.
 *
 *   onLogin(email, password, name) -> Promise<string | null>   null = signed in
 *   needsSetup                      first start: no account yet, so the card creates the administrator
 */
export default function GlassLogin({ onLogin, needsSetup = false, disabled = false }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [warning, setWarning] = useState("");

  async function submit(e) {
    e.preventDefault();
    if (busy || disabled) return;
    setBusy(true);
    setWarning("");
    const problem = await onLogin(email.trim(), password, name.trim());
    if (problem) {
      setWarning(problem);
      setPassword("");
      setBusy(false);
    }
    // on success the splash takes over (fly-in); the card is replaced, so no state to reset
  }

  return (
    <form className="gl-card" onSubmit={submit} aria-label={needsSetup ? "Create the administrator account" : "Sign in"} noValidate>
      <h2 className="gl-title">{needsSetup ? "Create administrator" : "Login"}</h2>
      {needsSetup && (
        <label className="gl-field">
          <span className="gl-sr">Name</span>
          <input type="text" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
          <User size={16} aria-hidden="true" />
        </label>
      )}
      <label className="gl-field">
        <span className="gl-sr">Email</span>
        <input type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)}
          autoComplete="username" autoFocus required />
        <User size={16} aria-hidden="true" />
      </label>
      <label className="gl-field">
        <span className="gl-sr">Password</span>
        <input type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)}
          autoComplete={needsSetup ? "new-password" : "current-password"} required />
        <Lock size={16} aria-hidden="true" />
      </label>
      {warning && (
        <p className="gl-warning" role="alert">
          <TriangleAlert size={15} aria-hidden="true" />
          <span>{warning}</span>
        </p>
      )}
      <SpecularButton {...BUTTON} type="submit" className="gl-submit pcc-start" disabled={busy || disabled || !email || !password}>
        {busy ? <Loader2 size={18} className="pcc-spin" aria-hidden="true" /> : null}
        <span>{needsSetup ? "Create account & start" : "Start Automation"}</span>
        {!busy && <ArrowRight size={18} className="pcc-arrow" aria-hidden="true" />}
      </SpecularButton>
      <p className="gl-hint">{needsSetup ? "First start: this account manages the others." : "Accounts are created by your administrator."}</p>
    </form>
  );
}
