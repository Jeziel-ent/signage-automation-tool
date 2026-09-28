import { Component, useEffect, useRef, useState } from "react";
import { Canvas } from "@react-three/fiber";
import { useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { Activity, AlertOctagon, ArrowRight, Cpu, Power, Radio, RefreshCw, ServerOff, X } from "lucide-react";
import ConnectionVisual from "./ConnectionVisual.jsx";
import CityScene, { FLIGHT_S } from "./CityScene.jsx";
import { checkCorelConnection, describeHealth } from "../utils/corelHealth.js";

const MIN_CHECK_MS = 500; // keep the connecting state up long enough to register even when the server answers instantly
const FADE_MS = 320; // fade to the workspace once the camera reaches the billboard

const reducedMotion = () => typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
function webglAvailable() {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

/**
 * The launch screen's one control, a translucent HUD panel in the city's palette: a status bar (a pulsing "engine node" light and
 * the host this page was opened from), the connect button (dark red glass with a neon edge, a shimmer sweep and a stronger glow on
 * hover) and a key hint. It opens the connection modal; while that is open the button is disabled.
 */
function ConnectControl({ buttonRef, onConnect, busy, checking }) {
  const host = typeof window !== "undefined" ? window.location.hostname || "local" : "local";
  return (
    <div className="sp3-hud">
      <div className="sp3-hud-glow" aria-hidden="true" />
      <div className="sp3-hud-frame">
        {["tl", "tr", "bl", "br"].map((c) => <span key={c} className={"sp3-hud-corner " + c} aria-hidden="true" />)}
        <div className="sp3-hud-bar">
          <span className="sp3-hud-node">
            <span className="sp3-ping" aria-hidden="true"><span /><span /></span>
            Engine node
          </span>
          <span className="sp3-hud-host">
            <Cpu size={14} aria-hidden="true" />
            {host}
          </span>
        </div>
        <button ref={buttonRef} type="button" className="sp3-hud-btn" onClick={onConnect} disabled={busy} autoFocus>
          <span className="sp3-hud-shimmer" aria-hidden="true" />
          <Power size={16} className="sp3-hud-power" aria-hidden="true" />
          <span className="sp3-hud-label">{checking ? "Establishing handshake..." : "Initialize CorelDRAW connection"}</span>
          <Radio size={14} className="sp3-hud-radio" aria-hidden="true" />
        </button>
        <p className="sp3-hud-hint">
          Press <kbd>Enter</kbd> or click to connect
        </p>
      </div>
    </div>
  );
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
 * Launch screen: a 3D night street (CityScene - the Adinn billboard, smaller Adinn hoardings, traffic, an elevated metro), the platform title and ONE button, "Connect to CorelDRAW" - the
 * page itself never changes size or shows status. The button opens a centred connection modal over a blurred backdrop that runs the
 * readiness check (GET /api/corel/health, which never starts CorelDRAW) and holds every state: checking (halo spinner), success
 * (emerald pulse, version + detail chips, "Start Automation ->"), failure (reason, remediation steps, "Retry Connection", "Continue
 * without CorelDRAW"). The modal closes with its X or Esc, which also cancels a check in flight. Nothing runs or navigates by itself;
 * Enter (or Space) presses the primary button showing. Starting hides the page and the modal, flies the camera over the traffic into
 * the billboard (~2.4 s), fades out on contact, goes to "/" and calls `onStart`.
 */
export default function SplashScreen({ onStart }) {
  const navigate = useNavigate();
  const [gl] = useState(webglAvailable);
  const [open, setOpen] = useState(false);
  const [health, setHealth] = useState({ state: "idle" }); // idle -> checking -> ok | error | offline
  const [leaving, setLeaving] = useState(false); // the camera flight into the billboard
  const [arriving, setArriving] = useState(false); // the fade to the workspace
  const [still] = useState(reducedMotion);
  const finished = useRef(false);
  const checkSeq = useRef(0);
  const timer = useRef(null);
  const connectBtn = useRef(null);

  async function runCheck() {
    if (leaving) return;
    const seq = ++checkSeq.current;
    setHealth({ state: "checking" });
    const [result] = await Promise.all([checkCorelConnection(), new Promise((r) => setTimeout(r, MIN_CHECK_MS))]);
    if (seq === checkSeq.current) setHealth(describeHealth(result));
  }

  function openAndConnect() {
    setOpen(true);
    runCheck();
  }

  function close() {
    checkSeq.current += 1; // a check still in flight is ignored when it lands
    setOpen(false);
    setHealth({ state: "idle" });
    setTimeout(() => connectBtn.current?.focus(), 0);
  }

  function finish() {
    if (finished.current) return;
    finished.current = true;
    clearTimeout(timer.current);
    setArriving(true);
    timer.current = setTimeout(() => {
      navigate("/"); // the Automation workspace, whichever page the screen was opened over
      onStart();
    }, gl && !still ? FADE_MS : 0);
  }

  function start() {
    if (leaving) return; // a click and a key press can arrive in the same tick
    setLeaving(true);
    setOpen(false); // the modal leaves through AnimatePresence (its inline opacity would override a CSS fade)
    if (!gl || still) finish();
    else timer.current = setTimeout(finish, FLIGHT_S * 1000 + 1500); // the scene calls finish() on contact; this is the safety net
  }

  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape" && open && !leaving) {
        e.preventDefault();
        close();
        return;
      }
      if ((e.key !== "Enter" && e.key !== " ") || e.target.closest?.("button")) return; // a focused button handles its own Enter/Space
      e.preventDefault();
      if (!open) openAndConnect();
      else if (health.state === "ok") start();
      else if (health.state !== "checking") runCheck();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, health.state, leaving]); // eslint-disable-line react-hooks/exhaustive-deps

  const failed = health.state === "error" || health.state === "offline";
  const version = health.state === "ok" ? health.version : null;
  return (
    <motion.div
      className={"sp3-root" + (leaving ? " leaving" : "") + (arriving ? " arriving" : "")}
      aria-labelledby="sp3-title"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
    >
      <div className="sp3-stage">
        {gl && (
          <CanvasBoundary>
            <Canvas className="sp3-canvas" dpr={[1, 1.75]} camera={{ position: [0, 14, 22], fov: 42, near: 0.1, far: 220 }} gl={{ antialias: false, powerPreference: "high-performance" }}>
              <CityScene leaving={leaving} still={still} onArrive={finish} />
            </Canvas>
          </CanvasBoundary>
        )}
        <div className="sp3-scrim" aria-hidden="true" />
      </div>

      {/* the page itself: title + one button, the same height in every state */}
      <main className="sp3-content">
        <header className="sp3-header">
          <h1 id="sp3-title" className="sp3-title">Signage Automation Platform</h1>
          <p className="sp3-sub">Enterprise CorelDRAW dual-master processing engine</p>
        </header>
        <ConnectControl buttonRef={connectBtn} onConnect={openAndConnect} busy={open} checking={health.state === "checking"} />
      </main>

      <AnimatePresence>
        {open && (
          <motion.div
            key="modal"
            className="sp3-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          >
            <motion.div
              className="sp3-modal"
              role="dialog"
              aria-modal="true"
              aria-labelledby="sp3-modal-title"
              initial={{ opacity: 0, y: 12, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 8, scale: 0.98 }}
              transition={{ duration: 0.22, ease: "easeOut" }}
            >
              <button type="button" className="sp3-close" onClick={close} disabled={leaving} aria-label="Close">
                <X size={18} aria-hidden="true" />
              </button>

              <ConnectionVisual state={health.state} />
              {failed && <span className="sp3-pill" role="status">{health.pill}</span>}

              <div className="sp3-modal-body" aria-live="polite">
                {health.state === "checking" && (
                  <>
                    <h2 id="sp3-modal-title" className="sp3-modal-title">Initializing Corel Engine Bridge</h2>
                    <p className="sp3-modal-text sp3-activity"><Activity size={14} aria-hidden="true" /> Scanning local COM registrations &amp; CorelDRAW installs...</p>
                  </>
                )}

                {health.state === "ok" && (
                  <>
                    <h2 id="sp3-modal-title" className="sp3-modal-title ok">
                      {version ? "CorelDRAW Connected Successfully" : health.title}
                    </h2>
                    <p className="sp3-modal-text">{version ? `Version ${version} detected • Engine Active` : health.detail}</p>
                    <ul className="sp3-chips" aria-label="CorelDRAW details">
                      {health.badges.map((b) => (
                        <li key={b.label} className={"sp3-chip" + (b.warn ? " warn" : "")}>
                          <span className="sp3-chip-k">{b.label}</span>
                          <span className="sp3-chip-v">{b.value}</span>
                        </li>
                      ))}
                    </ul>
                    {health.lowMemory && <p className="sp3-modal-warn">{health.memory}</p>}
                    <button key="start" type="button" className="sp3-btn" onClick={start} disabled={leaving} autoFocus>
                      Start Automation <ArrowRight size={16} aria-hidden="true" />
                    </button>
                  </>
                )}

                {failed && (
                  <>
                    <h2 id="sp3-modal-title" className="sp3-modal-title err">Engine Handshake Failed</h2>
                    <div className="sp3-callout" role="alert">
                      {health.state === "offline" ? <ServerOff size={18} aria-hidden="true" /> : <AlertOctagon size={18} aria-hidden="true" />}
                      <div>
                        <p className="sp3-callout-title">{health.title}</p>
                        <p className="sp3-callout-text sp3-mono">{health.detail}</p>
                        <ol className="sp3-steps">
                          {health.steps.map((s) => <li key={s}>{s}</li>)}
                        </ol>
                      </div>
                    </div>
                    <button key="retry" type="button" className="sp3-btn" onClick={runCheck} autoFocus>
                      <RefreshCw size={16} aria-hidden="true" /> Retry Connection
                    </button>
                    <button type="button" className="sp3-link" onClick={start}>
                      Continue without CorelDRAW Engine
                    </button>
                  </>
                )}
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
