import { Component, Suspense, lazy, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import ProfessionalConnectControl from "./ProfessionalConnectControl.jsx";
import { FLIGHT_S } from "./cityTiming.js";
import { checkCorelConnection, describeHealth } from "../utils/corelHealth.js";

// three.js + react-three-fiber are ~1 MB: only the canvas waits for them - the title and connect button render immediately.
// The download starts as soon as this module is evaluated, not when the component first renders.
const loadCity = () => import("./CityCanvas.jsx");
const cityPromise = typeof window !== "undefined" ? loadCity() : null;
const CityCanvas = lazy(() => cityPromise || loadCity());

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
 * one dark-glass control that changes in place (ProfessionalConnectControl): Connect -> checking -> "Start Automation", or the
 * failure reason with Retry / Continue without CorelDRAW. The check is the real GET /api/corel/health (it never starts
 * CorelDRAW). Nothing runs or navigates by itself; Enter (or Space) presses whichever primary action is showing. Starting fades
 * the control, flies the camera over the traffic into the billboard (~2.4 s), fades out on contact, goes to "/" and calls
 * `onStart`.
 */
export default function SplashScreen({ onStart }) {
  const navigate = useNavigate();
  const [gl] = useState(webglAvailable);
  const [health, setHealth] = useState({ state: "idle" }); // idle -> checking -> ok | error | offline
  const [leaving, setLeaving] = useState(false); // the camera flight into the billboard
  const [arriving, setArriving] = useState(false); // the fade to the workspace
  const [still] = useState(reducedMotion);
  const [cityReady, setCityReady] = useState(false); // the canvas fades in once its first frames (shader compile) are done
  const finished = useRef(false);
  const checkSeq = useRef(0);
  const timer = useRef(null);

  async function runCheck() {
    if (leaving || health.state === "checking") return;
    const seq = ++checkSeq.current;
    setHealth({ state: "checking" });
    const [result] = await Promise.all([checkCorelConnection(), new Promise((r) => setTimeout(r, MIN_CHECK_MS))]);
    if (seq === checkSeq.current) setHealth(describeHealth(result));
  }

  function finish() {
    if (finished.current) return;
    finished.current = true;
    clearTimeout(timer.current);
    setArriving(true);
    timer.current = setTimeout(() => {
      navigate("/"); // the Automation workspace, whichever page the screen was opened over
      onStart();
    }, still ? 0 : FADE_MS);
  }

  function start() {
    if (leaving) return; // a click and a key press can arrive in the same tick
    setLeaving(true);
    if (!gl || still || !cityReady) finish(); // no visible city yet: no flight to wait for
    else timer.current = setTimeout(finish, FLIGHT_S * 1000 + 1500); // the scene calls finish() on contact; this is the safety net
  }

  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    const onKey = (e) => {
      if ((e.key !== "Enter" && e.key !== " ") || e.target.closest?.("button")) return; // a focused button handles its own Enter/Space
      e.preventDefault();
      if (health.state === "ok") start();
      else runCheck(); // idle, or retry after a failure (ignored while a check is running)
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [health.state, leaving, cityReady]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <motion.div
      className={"sp3-root" + (leaving ? " leaving" : "")}
      aria-labelledby="sp3-title"
      // no entrance fade: the screen is opaque from its first paint. A 0 -> 1 fade showed the workspace through it and was
      // replayed a moment later (seen as a black blink, like a video restarting)
      initial={false}
      // the fade to the workspace is driven here: framer's inline opacity would override a CSS class doing it
      animate={{ opacity: arriving ? 0 : 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: arriving ? FADE_MS / 1000 : 0.3, ease: "easeOut" }}
    >
      <div className="sp3-stage">
        {gl && (
          <CanvasBoundary>
            <Suspense fallback={null}>
              <div className={"sp3-canvas-wrap" + (cityReady ? " ready" : "")}>
                <CityCanvas leaving={leaving} still={still} onArrive={finish} onReady={() => setCityReady(true)} />
              </div>
            </Suspense>
          </CanvasBoundary>
        )}
        <div className="sp3-scrim" aria-hidden="true" />
      </div>

      {/* the page itself: title + one control, the same height in every state */}
      <main className="sp3-content">
        <header className="sp3-header">
          <h1 id="sp3-title" className="sp3-title">Signage Automation Platform</h1>
          <p className="sp3-sub">Enterprise CorelDRAW dual-master processing engine</p>
        </header>
        <ProfessionalConnectControl health={health} onConnect={runCheck} onStart={start} disabled={leaving} />
      </main>
    </motion.div>
  );
}
