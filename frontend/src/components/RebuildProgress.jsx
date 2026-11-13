import { useEffect, useRef, useState } from "react";
import { useSteppedProgress } from "../hooks/useSteppedProgress.js";
import { STEP_LABELS } from "../editor/exportMath.js";

const POLL_MS = 1500;

/** "Saving edits" bar for one Recently generated row: an export of the board is being built (Save Changes in the editor, or the row's
 *  download). Polls that export and eases 0 -> 100 % with the same step pacing as the export dialog; calls onFinished(status) once. */
export default function RebuildProgress({ row, estimates, onFinished }) {
  const { id } = row.export_active;
  const [status, setStatus] = useState(null);
  const finished = useRef(false);

  useEffect(() => {
    let stop = false;
    let timer;
    async function poll() {
      try {
        const r = await fetch(`/api/editor/${row.job_id}/${row.shop_id}/export/${id}`);
        if (r.ok) {
          const st = await r.json();
          if (stop) return;
          setStatus(st);
          if ((st.status === "done" || st.status === "failed") && !finished.current) {
            finished.current = true;
            setTimeout(() => onFinished?.(st), st.status === "done" ? 900 : 0);   // let the bar reach 100 % first
            return;
          }
        }
      } catch { /* server busy: next tick */ }
      if (!stop) timer = setTimeout(poll, POLL_MS);
    }
    poll();
    return () => { stop = true; clearTimeout(timer); };
  }, [row.job_id, row.shop_id, id]); // eslint-disable-line react-hooks/exhaustive-deps

  const done = status?.status === "done";
  const pct = Math.round(useSteppedProgress(status?.plan || [], status?.step, done, estimates));
  const label = done ? "Edits saved"
    : status?.status === "failed" ? "Rebuild failed"
    : !status || status.status === "queued" ? "Waiting to rebuild…"
    : STEP_LABELS[status.step] || "Rebuilding with your edits";

  return (
    <div className={`rg-rebuild ${done ? "done" : ""}`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}
      aria-label="Rebuilding the board's files with the saved edits" title="The CDR / PDF / PNG are being rebuilt with your editor changes">
      <div className="rg-rebuild-top">
        <span>{label}</span>
        <span className="rg-rebuild-pct">{pct}%</span>
      </div>
      <div className="rg-rebuild-track"><div className="rg-rebuild-fill" style={{ width: `${pct}%` }} /></div>
    </div>
  );
}
