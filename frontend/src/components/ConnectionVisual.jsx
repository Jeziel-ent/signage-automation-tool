import { useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { AlertCircle, Cpu } from "lucide-react";

// What the readiness check actually does (GET /api/corel/health), shown one after another while it runs.
const SCAN_STEPS = ["Scanning COM registrations...", "Locating CorelDRAW executables...", "Checking free memory..."];
const NODES = 6; // particles that converge on success
const SIGNALS = [
  { angle: -35, delay: 0 },
  { angle: 150, delay: 0.5 },
  { angle: 250, delay: 1 },
];

const onOrbit = (deg, r) => ({ x: Math.cos((deg * Math.PI) / 180) * r, y: Math.sin((deg * Math.PI) / 180) * r });

/** CAD-style precision ring: a thin circle with 60 tick marks (every 5th longer), rotating slowly. */
function PrecisionRing() {
  const ticks = Array.from({ length: 60 }, (_, i) => {
    const a = (i * 6 * Math.PI) / 180;
    const inner = i % 5 === 0 ? 57 : 61;
    return <line key={i} x1={70 + Math.cos(a) * inner} y1={70 + Math.sin(a) * inner} x2={70 + Math.cos(a) * 65} y2={70 + Math.sin(a) * 65} className={i % 5 === 0 ? "major" : undefined} />;
  });
  return (
    <svg className="cv-precision" viewBox="0 0 140 140">
      <circle cx="70" cy="70" r="67" />
      <g>{ticks}</g>
    </svg>
  );
}

function ScanTicker({ still }) {
  const [i, setI] = useState(0);
  useEffect(() => {
    if (still) return undefined;
    const t = setInterval(() => setI((n) => (n + 1) % SCAN_STEPS.length), 900);
    return () => clearInterval(t);
  }, [still]);
  return (
    <p className="cv-ticker" aria-hidden="true">
      <span className="cv-ticker-dot" />
      <AnimatePresence mode="wait">
        <motion.span key={i} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.18 }}>
          {SCAN_STEPS[i]}
        </motion.span>
      </AnimatePresence>
    </p>
  );
}

/**
 * The connection modal's animated visual, by check state:
 *   checking - industrial vector-engine scan: a slow CAD precision ring with 60 tick marks, two counter-rotating rings with metallic
 *              gradient borders on a tilted 3D plane with a red glow, a faint sweep, three satellite signal nodes pinging, a chip core
 *              with a breathing electric glow, and a ticker of the check's real steps;
 *   ok       - lock-in: six nodes converge from the orbit, an emerald shield flips in on a spring (rotateY), its outline and a checkmark
 *              draw themselves (SVG pathLength), and emerald ripple waves keep expanding behind an emerald aura;
 *   error / offline - a red core with a short shake.
 * Reduced motion: no loops, no particles - the final frame of each state.
 */
export default function ConnectionVisual({ state }) {
  const still = useReducedMotion();
  const failed = state === "error" || state === "offline";
  return (
    <div className="cv-wrap">
      <div className="cv-stage" aria-hidden="true">
        <AnimatePresence mode="wait">
          {state === "checking" && (
            <motion.div key="checking" className={"cv-layer cv-checking" + (still ? " still" : "")} initial={{ opacity: 0, scale: 0.85 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.9 }} transition={{ duration: 0.25 }}>
              <PrecisionRing />
              <span className="cv-sweep" />
              <span className="cv-orbit-plane">
                <span className="cv-metal cv-metal-a" />
                <span className="cv-metal cv-metal-b" />
              </span>
              <span className="cv-core-glow" />
              {SIGNALS.map((s) => {
                const p = onOrbit(s.angle, 56);
                return <span key={s.angle} className="cv-signal" style={{ transform: `translate(${p.x}px, ${p.y}px)`, animationDelay: `${s.delay}s` }} />;
              })}
              <span className="cv-core cv-core-scan">
                <Cpu size={24} />
              </span>
            </motion.div>
          )}

          {state === "ok" && (
            <motion.div key="ok" className="cv-layer cv-ok" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }} style={{ perspective: 500 }}>
              {!still && Array.from({ length: NODES }, (_, i) => {
                const p = onOrbit((360 / NODES) * i - 90, 58);
                return (
                  <motion.span
                    key={i}
                    className="cv-node"
                    initial={{ x: p.x, y: p.y, opacity: 0, scale: 1 }}
                    animate={{ x: 0, y: 0, opacity: [0, 1, 1, 0], scale: [1, 1, 0.6, 0.2] }}
                    transition={{ duration: 0.5, ease: "easeIn", times: [0, 0.15, 0.8, 1] }}
                  />
                );
              })}
              <motion.span
                className="cv-aura"
                initial={{ opacity: 0, scale: 0.4 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={still ? { duration: 0 } : { delay: 0.4, type: "spring", stiffness: 160, damping: 14 }}
              />
              {!still && (
                <>
                  <span className="cv-ripple" />
                  <span className="cv-ripple r2" />
                </>
              )}
              <motion.span
                className="cv-shield"
                initial={still ? false : { scale: 0.5, rotateY: 90, opacity: 0 }}
                animate={{ scale: 1, rotateY: 0, opacity: 1 }}
                transition={{ delay: 0.36, type: "spring", stiffness: 180, damping: 14 }}
              >
                <svg viewBox="0 0 64 72" className="cv-shield-svg">
                  <defs>
                    <linearGradient id="cv-shield-fill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#10b981" />
                      <stop offset="100%" stopColor="#065f46" />
                    </linearGradient>
                  </defs>
                  <path d="M32 3 L58 12 V33 C58 50 47 62 32 69 C17 62 6 50 6 33 V12 Z" fill="url(#cv-shield-fill)" />
                  <motion.path
                    d="M32 3 L58 12 V33 C58 50 47 62 32 69 C17 62 6 50 6 33 V12 Z"
                    fill="none"
                    stroke="#6ee7b7"
                    strokeWidth={2}
                    strokeLinejoin="round"
                    initial={{ pathLength: still ? 1 : 0 }}
                    animate={{ pathLength: 1 }}
                    transition={still ? { duration: 0 } : { delay: 0.45, duration: 0.5, ease: "easeInOut" }}
                  />
                  <motion.path
                    d="M20 36 L28.5 44.5 L45 27"
                    fill="none"
                    stroke="#ffffff"
                    strokeWidth={5}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    initial={{ pathLength: still ? 1 : 0 }}
                    animate={{ pathLength: 1 }}
                    transition={still ? { duration: 0 } : { delay: 0.7, duration: 0.4, ease: "easeInOut" }}
                  />
                </svg>
              </motion.span>
            </motion.div>
          )}

          {failed && (
            <motion.div key="failed" className="cv-layer" initial={{ opacity: 0, scale: 0.85 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}>
              <motion.span className="cv-core cv-core-err" animate={still ? {} : { x: [0, -6, 6, -4, 4, 0] }} transition={{ delay: 0.15, duration: 0.45 }}>
                <AlertCircle size={28} />
              </motion.span>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      {state === "checking" && <ScanTicker still={still} />}
    </div>
  );
}
