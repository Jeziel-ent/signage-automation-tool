import { Component, Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import ProfessionalConnectControl from "./ProfessionalConnectControl.jsx";
import { FLIGHT_S } from "./cityTiming.js";
import { checkAgentConnection, describeAgent } from "../utils/agentHealth.js";
import { pairLocalAgent } from "../utils/agentLocal.js";
import { useAuth } from "../context/AuthContext.jsx";

// three.js + react-three-fiber are ~1 MB: only the canvas waits for them - the title and connect button render immediately.
// The download starts as soon as this module is evaluated, not when the component first renders.
const loadCity = () => import("./CityCanvas.jsx");
const cityPromise = typeof window !== "undefined" ? loadCity() : null;
const CityCanvas = lazy(() => cityPromise ?? loadCity());

const MIN_CHECK_MS = 700; // keep the connecting state up long enough to read, even when the server answers instantly
const FADE_MS = 320; // fade to the workspace once the camera reaches the billboard

const reducedMotion = () => typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
function webglAvailable() {
  try {
    const c = document.createElement("canvas");
    const ctx = c.getContext("webgl2") || c.getContext("webgl");
    ctx?.getExtension("WEBGL_lose_context")?.loseContext(); // free the probe: browsers cap live WebGL contexts
    return !!ctx;
  } catch {
    return false;
  }
}

class CanvasBoundary extends Component {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

/**
 * Launch screen: a 3D night street (CityScene - one Adinn hero billboard, traffic, an elevated metro), the platform title and
 * one dark-glass control that changes in place (ProfessionalConnectControl): Connect Agent -> looking for the Signage Agent on this
 * computer -> the sign-in card ("Start Automation"), or "agent not found" with a download. After the sign-in an agent that is not
 * connected to the account yet is paired automatically. The check is the agent's local status endpoint (utils/agentHealth.js). Nothing runs or navigates by itself; Enter (or Space) presses whichever primary action is showing. Starting fades
 * the control, flies the camera over the traffic into the billboard (~2.4 s), fades out on contact, goes to "/" and calls
 * `onStart`.
 */
export default function SplashScreen({ onStart, goHome = true }) {
  const navigate = useNavigate();
  const { user, authEnabled, needsSetup, login } = useAuth();
  const needsLogin = authEnabled && !user; // the sign-in card replaces the Start button until somebody is signed in
  const [gl] = useState(webglAvailable);
  const [health, setHealth] = useState({ state: "idle" }); // idle -> checking -> ok | error | offline (utils/agentHealth.js)
  const [leaving, setLeaving] = useState(false); // the camera flight into the billboard
  const [arriving, setArriving] = useState(false); // the fade to the workspace
  const [still] = useState(reducedMotion);
  const [cityReady, setCityReady] = useState(false); // the canvas fades in once its first frames (shader compile) are done
  // height of the title + control block overlaid on the bottom of the full-screen canvas: the camera frames the city above it
  const [inset, setInset] = useState(0);
  const contentRef = useRef(null);
  useEffect(() => {
    const el = contentRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    // setInset bails out on an equal value, so only a real height change re-renders the canvas (re-framing the camera)
    const ro = new ResizeObserver(() => setInset(Math.round(el.getBoundingClientRect().height)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const finished = useRef(false);
  const signedInHere = useRef(false);  // somebody signed in on this screen (not: was already signed in)
  const userRef = useRef(user);        // the account as it is when the fly-in ends (login updates it after onLogin started)
  userRef.current = user;
  const checkSeq = useRef(0);
  const timer = useRef(null);

  async function runCheck() {
    if (leaving || health.state === "checking") return;
    const seq = ++checkSeq.current;
    setHealth({ state: "checking" });
    const [result] = await Promise.all([checkAgentConnection(), new Promise((r) => setTimeout(r, MIN_CHECK_MS))]);
    if (seq === checkSeq.current) setHealth(describeAgent(result.local, result.config));
  }

  function finish() {
    if (finished.current) return;
    finished.current = true;
    clearTimeout(timer.current);
    setArriving(true);
    timer.current = setTimeout(() => {
      onStart();
      // an administrator who just signed in lands on the admin monitor; everybody else on the Automation workspace (when reopened) or
      // where the address already points (a page load keeps it)
      if (signedInHere.current && userRef.current?.role === "admin") navigate("/admin");
      else if (goHome) navigate("/");
    }, still ? 0 : FADE_MS);
  }

  // stable handlers for the (memoised) canvas: fresh closures each render would re-render the whole WebGL tree on every
  // state change of this screen. finish() only touches refs and setters, so calling the latest one through a ref is safe.
  const finishRef = useRef(finish);
  finishRef.current = finish;
  const onArrive = useCallback(() => finishRef.current(), []);
  const onCityReady = useCallback(() => setCityReady(true), []);

  function start() {
    if (leaving) return; // a click and a key press can arrive in the same tick
    setLeaving(true);
    if (!gl || still || !cityReady) finish(); // no visible city yet: no flight to wait for
    else timer.current = setTimeout(finish, FLIGHT_S * 1000 + 1500); // the scene calls finish() on contact; this is the safety net
  }

  // the card's submit button is "Start Automation": signed in -> the fly-in starts; refused -> the warning stays in the card
  async function onLogin(email, password, name) {
    const problem = await login(email, password, name);
    if (problem) return problem;
    signedInHere.current = true;
    await pairDetectedAgent();
    start();
    return null;
  }

  // a detected agent that is not connected to an account yet joins the account that just signed in (the page hands it a one-time code);
  // a failure here never blocks the sign-in: the Agent page and its banner offer "Connect this PC" again
  async function pairDetectedAgent() {
    if (!health.agent || health.paired || !health.port) return;
    try {
      const r = await fetch("/api/agent/pairing-code", { method: "POST" });
      if (r.ok) await pairLocalAgent(health.port, (await r.json()).code);
    } catch {
      /* offered again on the Agent page */
    }
  }

  async function startSignedIn() {
    await pairDetectedAgent();
    start();
  }

  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    const onKey = (e) => {
      if ((e.key !== "Enter" && e.key !== " ") || e.target.closest?.("button, input, textarea")) return; // a focused button / field handles its own keys
      e.preventDefault();
      if (health.state === "ok") { if (!needsLogin) startSignedIn(); } // with a sign-in required only the card's own submit starts
      else runCheck(); // idle, or retry after a failure (ignored while a check is running)
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [health.state, leaving, cityReady, needsLogin]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <motion.div
      className={"sp3-root" + (leaving ? " leaving" : "")}
      style={{ "--sp3-inset": `${inset}px` }}
      aria-labelledby="sp3-title"
      // no entrance fade: the screen is opaque from its first paint. A 0 -> 1 fade showed the workspace through it and was
      // replayed a moment later (seen as a black blink, like a video restarting)
      initial={false}
      // the fade to the workspace is driven here: framer's inline opacity would override a CSS class doing it
      animate={{ opacity: arriving ? 0 : 1 }}
      // after the fly-in the screen has already faded to 0: leave at once, or the invisible screen would keep covering
      // (and swallowing clicks on) the workspace for another 0.3 s
      exit={{ opacity: 0, transition: { duration: arriving ? 0 : 0.3 } }}
      transition={{ duration: arriving ? FADE_MS / 1000 : 0.3, ease: "easeOut" }}
    >
      <div className="sp3-stage">
        {/* painted at frame 0, before the 3D bundle and its shader compile: a night-sky gradient with a red horizon glow,
            so the screen is never plain black; the city fades in over it (its canvas is opaque once shown) */}
        <div className="sp3-backdrop-2d" aria-hidden="true">
          <div className="sp3-horizon-glow" />
        </div>
        {gl && (
          <div className={"sp3-preloader" + (cityReady ? " done" : "")} role="status" aria-live="polite">
            <span className="sp3-preloader-spin" aria-hidden="true" />
            <span className="sp3-preloader-text">{cityReady ? "Ready" : "Loading..."}</span>
          </div>
        )}
        {gl && (
          <CanvasBoundary>
            <Suspense fallback={null}>
              <div className={"sp3-canvas-wrap" + (cityReady ? " ready" : "")}>
                <CityCanvas leaving={leaving} still={still} onArrive={onArrive} onReady={onCityReady} inset={inset} />
              </div>
            </Suspense>
          </CanvasBoundary>
        )}
        <div className="sp3-scrim" aria-hidden="true" />
      </div>

      {/* the page itself: title + one control, the same height in every state */}
      <main className="sp3-content" ref={contentRef}>
        <header className="sp3-header">
          <h1 id="sp3-title" className="sp3-title">Signage Automation Platform</h1>
          <p className="sp3-sub">Signage automation for CorelDRAW</p>
        </header>
        <ProfessionalConnectControl health={health} onConnect={runCheck} onStart={startSignedIn} disabled={leaving}
          login={needsLogin ? { onLogin, needsSetup } : undefined} />
      </main>
    </motion.div>
  );
}
