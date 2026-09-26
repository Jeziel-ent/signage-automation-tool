import { Component, Suspense, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Environment, Lightformer, RoundedBox, Stars, useTexture } from "@react-three/drei";
import * as THREE from "three";
import { useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { Activity, AlertOctagon, ArrowRight, RefreshCw, Server, ServerOff, X } from "lucide-react";
import ConnectionVisual from "./ConnectionVisual.jsx";
import logoImg from "../assets/logo.jpeg";
import { checkCorelConnection, describeHealth } from "../utils/corelHealth.js";

const BG = "#0B1120"; // deep slate, matches the overlay's #0F172A family
const MIN_CHECK_MS = 500; // keep the connecting state up long enough to register even when the server answers instantly
const LEAVE_MS = 420; // camera push-in + fade before the workspace is shown

const reducedMotion = () => typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
function webglAvailable() {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

// ---- the billboard: silver outer rim, dark chrome frame, lit logo face, two long poles running off the bottom of the view
const FACE_W = 4.2;
const FACE_H = 2.1;

function Billboard() {
  const logo = useTexture(logoImg);
  const { gl } = useThree();
  useLayoutEffect(() => {
    // logo.jpeg is 1600x1440 with wide white margins: show only the logo's own box (plus a little air), unstretched at ~2:1
    logo.colorSpace = THREE.SRGBColorSpace;
    logo.generateMipmaps = false;
    logo.minFilter = THREE.LinearFilter;
    logo.anisotropy = gl.capabilities.getMaxAnisotropy();
    logo.repeat.set(0.85, 0.46);
    logo.offset.set(0.075, 0.3);
    logo.needsUpdate = true;
  }, [logo, gl]);

  return (
    <group>
      {/* outer light-silver rim, then the dark chrome frame on top of it */}
      <RoundedBox args={[FACE_W + 0.5, FACE_H + 0.5, 0.18]} radius={0.08} smoothness={4} position={[0, 0, -0.02]}>
        <meshStandardMaterial color="#D9DBE1" metalness={0.9} roughness={0.22} envMapIntensity={1.2} />
      </RoundedBox>
      <RoundedBox args={[FACE_W + 0.3, FACE_H + 0.3, 0.26]} radius={0.06} smoothness={4}>
        <meshStandardMaterial color="#23252D" metalness={0.9} roughness={0.18} envMapIntensity={1.4} />
      </RoundedBox>
      {/* the sign face: evenly lit, matte (so studio lights never wash the logo out), slightly self-lit like a backlit panel */}
      <mesh position={[0, 0, 0.135]}>
        <planeGeometry args={[FACE_W, FACE_H]} />
        <meshStandardMaterial map={logo} emissive="#ffffff" emissiveMap={logo} emissiveIntensity={0.8} roughness={0.95} metalness={0} envMapIntensity={0.1} />
      </mesh>
      {/* poles: tops tucked into the frame, 6 units long so they leave the viewport */}
      {[-1.7, 1.7].map((x) => (
        <mesh key={x} position={[x, -4.2, -0.2]}>
          <cylinderGeometry args={[0.11, 0.11, 6, 32]} />
          <meshStandardMaterial color="#2E3039" metalness={0.85} roughness={0.25} envMapIntensity={1.1} />
        </mesh>
      ))}
    </group>
  );
}

/** Slow sway + float and pointer parallax; on `leaving` the camera pushes in a little while the overlay fades. */
// distance at which the whole board (with a margin) fits the canvas width, at the camera's vertical fov
function fitDistance(aspect, fovDeg) {
  const halfW = (FACE_W + 0.9) / 2;
  const tanH = Math.tan((fovDeg * Math.PI) / 360) * aspect; // tan of half the horizontal fov
  return Math.max(7.4, (halfW / tanH) * 1.12);
}

function Rig({ leaving, still, children }) {
  const group = useRef();
  const { camera, pointer, size } = useThree();
  useFrame((state, dt) => {
    const t = state.clock.elapsedTime;
    const k = 1 - Math.exp(-dt * 3); // frame-rate independent easing
    if (group.current) {
      const targetY = still || leaving ? 0 : Math.sin(t * 0.35) * 0.14 + pointer.x * 0.12;
      const targetX = still || leaving ? 0 : pointer.y * -0.05;
      group.current.rotation.y += (targetY - group.current.rotation.y) * k;
      group.current.rotation.x += (targetX - group.current.rotation.x) * k;
      group.current.position.y = 0.45 + (still ? 0 : Math.sin(t * 0.6) * 0.05);
    }
    const base = fitDistance(size.width / Math.max(1, size.height), camera.fov);
    const tz = leaving ? base * 0.76 : base;
    camera.position.z += (tz - camera.position.z) * (1 - Math.exp(-dt * (leaving ? 6 : 3)));
    if (!still) camera.position.x += (pointer.x * 0.25 - camera.position.x) * k;
    camera.lookAt(0, 0.2, 0);
  });
  return <group ref={group}>{children}</group>;
}

function Scene({ leaving }) {
  const still = reducedMotion();
  return (
    <>
      <color attach="background" args={[BG]} />
      <fog attach="fog" args={[BG, 10, 24]} />
      {/* neutral studio lighting - no coloured rim or glow */}
      <ambientLight intensity={0.35} />
      <spotLight position={[4, 6, 7]} angle={0.5} penumbra={0.8} intensity={140} color="#ffffff" />
      <pointLight position={[-3, -1, 4]} intensity={30} color="#dbe4ff" distance={14} />
      <directionalLight position={[-2, 3, -4]} intensity={1.2} color="#cbd5e1" />
      <Environment resolution={256} frames={1}>
        <Lightformer form="rect" intensity={2.4} position={[0, 5, 3]} scale={[12, 2, 1]} color="#ffffff" />
        <Lightformer form="rect" intensity={1.4} position={[-6, 1, 1]} scale={[2, 7, 1]} color="#e2e8f0" />
        <Lightformer form="rect" intensity={1.0} position={[6, 0, -2]} scale={[2, 6, 1]} color="#cbd5e1" />
      </Environment>
      <Suspense fallback={null}>
        <Rig leaving={leaving} still={still}>
          <Billboard />
        </Rig>
      </Suspense>
      <Stars radius={40} depth={30} count={still ? 400 : 900} factor={2} saturation={0} fade speed={still ? 0 : 0.25} />
    </>
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
 * Launch screen: the 3D Adinn billboard (WebGL via react-three-fiber), the platform title and ONE button, "Connect to CorelDRAW" - the
 * page itself never changes size or shows status. The button opens a centred connection modal over a blurred backdrop that runs the
 * readiness check (GET /api/corel/health, which never starts CorelDRAW) and holds every state: checking (halo spinner), success
 * (emerald pulse, version + detail chips, "Start Automation ->"), failure (reason, remediation steps, "Retry Connection", "Continue
 * without CorelDRAW"). The modal closes with its X or Esc, which also cancels a check in flight. Nothing runs or navigates by itself;
 * Enter presses the primary button showing. Starting pushes the camera in and fades (~0.4 s), goes to "/" and calls `onStart`.
 */
export default function SplashScreen({ onStart }) {
  const navigate = useNavigate();
  const [gl] = useState(webglAvailable);
  const [open, setOpen] = useState(false);
  const [health, setHealth] = useState({ state: "idle" }); // idle -> checking -> ok | error | offline
  const [leaving, setLeaving] = useState(false);
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

  function start() {
    if (leaving) return; // a click and a key press can arrive in the same tick
    setLeaving(true);
    timer.current = setTimeout(() => {
      navigate("/"); // the Automation workspace, whichever page the screen was opened over
      onStart();
    }, gl && !reducedMotion() ? LEAVE_MS : 0);
  }

  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape" && open && !leaving) {
        e.preventDefault();
        close();
        return;
      }
      if (e.key !== "Enter" || e.target.closest?.("button")) return; // a focused button handles its own Enter
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
      className={"sp3-root" + (leaving ? " leaving" : "")}
      aria-labelledby="sp3-title"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
    >
      <div className="sp3-stage">
        {gl && (
          <CanvasBoundary>
            <Canvas className="sp3-canvas" dpr={[1, 2]} camera={{ position: [0, 0.4, 7.4], fov: 42, near: 0.1 }} gl={{ antialias: true, powerPreference: "high-performance" }}>
              <Scene leaving={leaving} />
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
        <div className="sp3-cta">
          <button ref={connectBtn} type="button" className="sp3-btn" onClick={openAndConnect} disabled={open} autoFocus>
            <Server size={16} aria-hidden="true" /> Connect to CorelDRAW
          </button>
        </div>
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
