import { useEffect, useState } from "react";
import { CONVERT_STEPS } from "../pages/Automation.jsx";
import { useSteppedProgress } from "../hooks/useSteppedProgress.js";
import { fmt, toUnit } from "./units.js";

/**
 * Shown when the page W/H fields are changed. Two honest choices:
 *  - "Page only": just changes the page; objects stay where they are (a `page` op).
 *  - "Re-convert": runs the real layout engine (compute_layout, brand rules, tiling)
 *    on the ORIGINAL master at the new size, as a new shop on the same job, and opens it.
 * A scale-everything option is deliberately absent: it would be a second, worse layout
 * engine living in the editor.
 */
export default function PageResizeDialog({ current, next, unit, shop, jobId, onPageOnly, onCancel }) {
  const [phase, setPhase] = useState("choose"); // choose | converting | failed
  const [newShopId, setNewShopId] = useState(null);
  const [state, setState] = useState({ step: null, status: "queued" });
  const [error, setError] = useState("");
  const [estimates, setEstimates] = useState({});

  const label = (b) => `${fmt(b.w, unit)} × ${fmt(b.h, unit)} ${unit}`;
  const pct = useSteppedProgress(CONVERT_STEPS, state.step, state.status === "done", estimates);

  useEffect(() => {
    fetch("/api/v2/step-estimates").then((r) => r.json()).then(setEstimates).catch(() => {});
  }, []);

  useEffect(() => {
    if (phase !== "converting" || !newShopId) return undefined;
    const t = setInterval(async () => {
      try {
        const s = await (await fetch(`/api/v2/shops/${newShopId}/status`)).json();
        setState({ step: s.step, status: s.status });
        if (s.status === "done") {
          clearInterval(t);
          window.location.assign(`/editor/${jobId}/${newShopId}`);
        } else if (s.status === "failed") {
          clearInterval(t);
          setError(s.error || "conversion failed");
          setPhase("failed");
        }
      } catch {
        /* transient - keep polling */
      }
    }, 800);
    return () => clearInterval(t);
  }, [phase, newShopId, jobId]);

  async function reconvert() {
    setPhase("converting");
    setError("");
    try {
      const body = {
        name: shop.name,
        width: +toUnit(next.w, unit).toFixed(4),
        width_unit: unit,
        height: +toUnit(next.h, unit).toFixed(4),
        height_unit: unit,
        reference: shop.reference || null,
        // a re-convert keeps the shop's dual-master choice (the server picks by the NEW size's orientation)
        landscape_master_id: shop.landscape_master_id || null,
        portrait_master_id: shop.portrait_master_id || null,
        // and its picked master; if the new size has the other orientation the server uses that orientation's default
        master_id: shop.master_id || null,
      };
      const r = await fetch(`/api/v2/jobs/${jobId}/shops`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
      const created = await r.json();
      const c = await fetch(`/api/v2/shops/${created.id}/convert`, { method: "POST" });
      if (!c.ok) throw new Error((await c.json()).detail || `HTTP ${c.status}`);
      setNewShopId(created.id);
    } catch (e) {
      setError(e.message);
      setPhase("failed");
    }
  }

  return (
    <div className="ed-modal-back" role="dialog" aria-modal="true" aria-label="Change page size">
      <div className="ed-modal">
        <h2>Change page size</h2>
        <p>
          {label(current)} <strong>→</strong> {label(next)}
        </p>

        {phase === "choose" && (
          <>
            <div className="ed-choice">
              <button className="btn" onClick={reconvert} data-choice="reconvert">Re-convert at this size</button>
              <p className="ed-hint">
                Runs the layout engine (same rules as the Automation page: background stretch, tiling, brand
                panel sequence) on the original master and opens the result as a new board of this job. This
                board and its edits stay as they are and are <strong>not</strong> carried over.
              </p>
            </div>
            <div className="ed-choice">
              <button className="btn ghost" onClick={onPageOnly} data-choice="page-only">Change the page only</button>
              <p className="ed-hint">Objects keep their positions and sizes; only the page boundary moves. Undoable.</p>
            </div>
            <div className="ed-modal-actions">
              <button className="ed-btn" onClick={onCancel}>Cancel</button>
            </div>
          </>
        )}

        {phase === "converting" && (
          <>
            <div className="progress-bar"><div className="progress-fill" style={{ width: `${pct}%` }} /></div>
            <p className="ed-hint">Converting with CorelDRAW… then this tab opens the new board (its editor scene takes another 20-50 s to render).</p>
          </>
        )}

        {phase === "failed" && (
          <>
            <p className="err">{error}</p>
            <div className="ed-modal-actions">
              <button className="btn" onClick={reconvert}>Retry</button>
              <button className="ed-btn" onClick={onCancel}>Close</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
