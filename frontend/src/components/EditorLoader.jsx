import { useEffect, useRef, useState } from "react";
import { Maximize2, Terminal } from "lucide-react";
import DesignerWorkstation from "./DesignerWorkstation.jsx";
import { STAGES, stageOf } from "../utils/loadStages.js";

const FADE_MS = 250;
const MIN_RATE = 45; // % per second: the least the bar moves toward the real value, so small steps still glide
const CATCH_UP = 14; // per second: the bar also closes this fraction of the remaining gap each second, so it never lags far behind.
// (A fixed 45 %/s alone held every editor open for >= 2.2 s after the scene was ready - measured: workspace painted at 0.4-2.0 s,
// loader gone only at 3.0-3.4 s. Now the bar reaches a finished load in ~0.3-0.5 s.)

/** Fallback stage line by displayed percentage, used only when EditorPage sends no status of its own. */
export function stageMessage(pct) {
  if (pct <= 25) return "Initializing the editor workspace...";
  if (pct <= 50) return "Loading master template artwork...";
  if (pct <= 85) return "Loading vector object layers...";
  return "Finalizing signage layout for the editor...";
}

/**
 * Loader for the editor tab, driven by the editor's REAL load state: `progress` (0-100) and `statusText` come from EditorPage
 * (server scene-build progress, per-image preloading, first paint); the bar eases toward it (MIN_RATE / CATCH_UP). The visual is
 * `DesignerWorkstation`: a designer at a PC running CorelDRAW whose artwork keeps resizing and re-flowing on its own loop (independent
 * of progress), with live W/H readouts. The telemetry line is EditorPage's own status
 * ("CorelDRAW is rendering objects 110/138", "Loading object images 88/138", ...) and falls back to `stageMessage(pct)`; the footer
 * names the real load stage. When progress is 100 and the bar is (nearly) full the loader fades (250 ms) and calls `onDone`.
 */
export default function EditorLoader({ progress = 0, statusText = "", onDone }) {
  const [pct, setPct] = useState(0);
  const [fading, setFading] = useState(false);
  const targetRef = useRef(progress);
  targetRef.current = progress;
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  useEffect(() => {
    let raf;
    let last = performance.now();
    const tick = (now) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      setPct((p) => Math.max(p, Math.min(targetRef.current, p + Math.max(MIN_RATE, (targetRef.current - p) * CATCH_UP) * dt)));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  const finish = progress >= 100 && pct >= 97; // the last few % are not worth waiting for - the fade covers them
  useEffect(() => {
    if (!finish) return undefined;
    const t1 = setTimeout(() => setFading(true), 80); // a beat on the full bar
    const t2 = setTimeout(() => onDoneRef.current?.(), 80 + FADE_MS);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, [finish]);

  const shown = Math.round(pct);
  const stage = stageOf(pct);
  const message = statusText && statusText !== "Ready" ? statusText : stageMessage(shown);
  return (
    <div className={"el3-root" + (fading ? " fading" : "")} role="status" aria-live="polite" aria-label="Loading the editor">
      <div className="el3-ambient" aria-hidden="true" />

      <div className="el3-tag">
        <span className="el3-tag-dot" aria-hidden="true" />
        <span>Adinn Automation Editor</span>
      </div>

      <div className="el3-scene">
        <DesignerWorkstation />
      </div>

      <div className="el3-telemetry">
        <p className="el3-msg" key={stage}>
          <Terminal size={14} aria-hidden="true" /> {message}
        </p>
        <div className="el3-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={shown}>
          <div className="el3-fill" style={{ width: `${pct}%` }} />
        </div>
        <div className="el3-foot">
          <span className="el3-stages">
            {STAGES.map((s) => (
              <span key={s} className={s === stage ? "on" : STAGES.indexOf(s) < STAGES.indexOf(stage) ? "done" : undefined}>
                {s}
              </span>
            ))}
          </span>
          <span className="el3-pct">{shown}%</span>
          <span className="el3-canvas-tag">
            <Maximize2 size={11} aria-hidden="true" /> Auto-resize loop
          </span>
        </div>
      </div>
    </div>
  );
}
