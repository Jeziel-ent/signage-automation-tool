import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { Loader2, LogIn } from "lucide-react";
import { AuthContext } from "../context/AuthContext.jsx";
import { fetchMe, isSessionExpired, sendCredentials } from "../utils/authApi.js";
import "./AuthGate.css";

function LoginForm({ needsSetup, onDone, overlay }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body = needsSetup ? { email, password, name } : { email, password };
      const { user } = await sendCredentials(needsSetup ? "/api/auth/setup" : "/api/auth/login", body);
      onDone(user);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={"auth-screen" + (overlay ? " overlay" : "")} role="dialog" aria-modal="true" aria-label={needsSetup ? "Create the administrator account" : "Sign in"}>
      <form className="auth-card" onSubmit={submit}>
        <h1>{needsSetup ? "Create the administrator" : "Sign in"}</h1>
        <p className="auth-sub">
          {needsSetup ? "First start: this account manages the other users." : overlay ? "Your session ended. Sign in again - your work on this page is kept." : "Signage Automation"}
        </p>
        {needsSetup && (
          <label>Name
            <input type="text" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
          </label>
        )}
        <label>Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required autoFocus />
        </label>
        <label>Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
            autoComplete={needsSetup ? "new-password" : "current-password"} minLength={needsSetup ? 8 : undefined} required />
        </label>
        {error && <div className="auth-error" role="alert">{error}</div>}
        <button className="btn" type="submit" disabled={busy || !email || !password}>
          {busy ? <Loader2 size={16} className="spin" /> : <LogIn size={16} />} {needsSetup ? "Create account" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

/** Wraps the whole app. The sign-in itself lives on the launch screen (SplashScreen: Connect -> glass sign-in card -> "Start
 *  Automation"); until there is a session the Shell shows nothing but that screen, so no API call is made. The editor opens in its
 *  own tab without the launch screen, so THERE a plain sign-in form is shown instead. When a session ends later (a 401 from any
 *  /api call) the form appears OVER the page, which stays mounted, so queued work is not lost. */
export default function AuthGate({ children }) {
  const [state, setState] = useState({ loading: true, authEnabled: true, user: null, needsSetup: false });
  const [expired, setExpired] = useState(false);
  const signedInRef = useRef(false);
  const { pathname } = useLocation();

  useEffect(() => {
    let alive = true;
    fetchMe().then((me) => {
      if (!alive) return;
      if (!me) return setState({ loading: false, authEnabled: true, user: null, needsSetup: false });
      setState({ loading: false, authEnabled: me.auth_enabled, user: me.user, needsSetup: me.needs_setup });
      signedInRef.current = !!me.user;
    });
    return () => { alive = false; };
  }, []);

  // any later 401 means the session ended (only meaningful once somebody was signed in)
  useEffect(() => {
    const original = window.fetch;
    window.fetch = async (...args) => {
      const res = await original(...args);
      const url = typeof args[0] === "string" ? args[0] : args[0]?.url ?? "";
      if (signedInRef.current && isSessionExpired(url, res.status)) setExpired(true);
      return res;
    };
    return () => { window.fetch = original; };
  }, []);

  const signedIn = useCallback((user) => {
    signedInRef.current = true;
    setState((s) => ({ ...s, user, needsSetup: false }));
    setExpired(false);
  }, []);

  const login = useCallback(async (email, password, name) => {
    try {
      const needsSetup = state.needsSetup;
      const { user } = await sendCredentials(needsSetup ? "/api/auth/setup" : "/api/auth/login",
        needsSetup ? { email, password, name: name || "" } : { email, password });
      signedIn(user);
      return null;
    } catch (err) {
      return err.message || "Sign-in failed";
    }
  }, [state.needsSetup, signedIn]);

  const logout = useCallback(async () => {
    try { await fetch("/api/auth/logout", { method: "POST" }); } catch { /* offline: the cookie just expires */ }
    window.location.assign("/");
  }, []);

  const ctx = useMemo(() => ({
    user: state.user, authEnabled: state.authEnabled, needsSetup: state.needsSetup,
    isAdmin: !state.authEnabled || state.user?.role === "admin", login, logout,
  }), [state.user, state.authEnabled, state.needsSetup, login, logout]);

  if (state.loading) return <div className="auth-loading"><Loader2 className="spin" size={28} /></div>;
  const signedOut = state.authEnabled && !state.user;
  if (signedOut && pathname.startsWith("/editor/")) return <LoginForm needsSetup={state.needsSetup} onDone={signedIn} />;
  const sessionEnded = state.authEnabled && !!state.user && expired;
  return (
    <AuthContext.Provider value={ctx}>
      {children}
      {sessionEnded && <LoginForm needsSetup={false} onDone={signedIn} overlay />}
    </AuthContext.Provider>
  );
}
