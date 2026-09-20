import { useEffect, useMemo, useState } from "react";
import { useSteppedProgress } from "../hooks/useSteppedProgress.js";
import { STEP_LABELS, fmtBytes, missingFonts, resolveRaster } from "./exportMath.js";
import { fmt } from "./units.js";

const FORMATS = [
  ["cdr", "CDR", "CorelDRAW file with your edits applied"],
  ["pdf", "PDF", "Print-ready document"],
  ["png", "PNG", "Image, optional transparent background"],
  ["jpeg", "JPEG", "Image (white background)"],
];
const SIZE_PRESETS = [
  ["max_px:1600", "Preview - 1600 px wide"],
  ["max_px:4000", "Standard - 4000 px wide"],
  ["max_px:8000", "Large - 8000 px wide"],
  ["dpi:72", "72 dpi"],
  ["dpi:150", "150 dpi"],
  ["dpi:300", "300 dpi"],
];

/**
 * "Save and Generate": pick formats + quality options, then a real progress bar while the
 * backend replays the saved edits through CorelDRAW and exports. Every file comes from
 * CorelDRAW; this dialog only asks for it and downloads the result.
 */
export default function ExportDialog({ jobId, shopId, scene, opsCount, fonts, flush, onClose }) {
  const [phase, setPhase] = useState("choose"); // choose | running | done | failed
  const [formats, setFormats] = useState({ cdr: true, pdf: true, png: true, jpeg: false });
  const [pdf, setPdf] = useState({ color_mode: "native", text: "embed", bitmap_dpi: 200 });
  const [size, setSize] = useState("max_px:4000");
  const [customDpi, setCustomDpi] = useState("");
  const [png, setPng] = useState({ png_background: "transparent", antialias: true });
  const [error, setError] = useState("");
  const [job, setJob] = useState(null); // {export_id, plan}
  const [status, setStatus] = useState(null);
  const [estimates, setEstimates] = useState({});
  const [showMismatches, setShowMismatches] = useState(false);

  const chosen = FORMATS.map(([k]) => k).filter((k) => formats[k]);
  const wantsRaster = formats.png || formats.jpeg;
  const raster = useMemo(() => {
    if (size === "custom") return { mode: "dpi", dpi: parseFloat(customDpi) || 0, ...png };
    const [mode, val] = size.split(":");
    return mode === "max_px" ? { mode, max_px: +val, ...png } : { mode, dpi: +val, ...png };
  }, [size, customDpi, png]);
  const preview = useMemo(() => resolveRaster(scene.page.width, scene.page.height, raster), [scene.page.width, scene.page.height, raster]);
  const fontIssues = useMemo(() => missingFonts(scene, fonts), [scene, fonts]);
  const blocked = !chosen.length || (wantsRaster && preview.error);

  const pct = useSteppedProgress(job ? job.plan : [], status && status.step, status && status.status === "done", estimates);

  useEffect(() => {
    fetch("/api/editor/export-estimates").then((r) => r.json()).then(setEstimates).catch(() => {});
  }, []);

  useEffect(() => {
    if (phase !== "running" || !job) return undefined;
    const t = setInterval(async () => {
      try {
        const s = await (await fetch(`/api/editor/${jobId}/${shopId}/export/${job.export_id}`)).json();
        setStatus(s);
        if (s.status === "done") { clearInterval(t); setPhase("done"); }
        else if (s.status === "failed") { clearInterval(t); setError(s.error || "The export failed"); setPhase("failed"); }
      } catch { /* transient - keep polling */ }
    }, 700);
    return () => clearInterval(t);
  }, [phase, job, jobId, shopId]);

  async function start() {
    setError("");
    if (!(await flush())) { setError("Your edits could not be saved, so nothing was exported."); setPhase("failed"); return; }
    try {
      const r = await fetch(`/api/editor/${jobId}/${shopId}/export`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ formats: chosen, options: { pdf, raster } }),
      });
      const body = await r.json();
      if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
      setJob(body);
      setStatus({ step: "launch", status: "queued" });
      setPhase("running");
    } catch (e) {
      setError(e.message);
      setPhase("failed");
    }
  }

  const fileUrl = (name) => `/api/editor/${jobId}/${shopId}/exports/${job.export_id}/files/${encodeURIComponent(name)}`;
  const label = status && status.status === "done" ? "Finished" : status && status.step ? STEP_LABELS[status.step] || status.step : "Queued";
  const closable = phase !== "running";

  return (
    <div className="ed-modal-back" role="dialog" aria-modal="true" aria-label="Save and Generate">
      <div className="ed-modal ed-export">
        <div className="ed-export-head">
          <h2>Save and Generate</h2>
          {closable && <button className="ed-btn" onClick={onClose} aria-label="Close">Close</button>}
        </div>

        {phase === "choose" && (
          <>
            <p className="ed-hint" style={{ marginTop: 0 }}>
              {opsCount ? `${opsCount} edit${opsCount === 1 ? "" : "s"} will be applied to the converted CDR in CorelDRAW, then exported.` : "No edits - the converted board is exported as it is."}{" "}
              Files are written by CorelDRAW itself; your original converted file is never overwritten.
            </p>

            <div className="ed-formats">
              {FORMATS.map(([k, name, hint]) => (
                <label key={k} className={`ed-format${formats[k] ? " on" : ""}`}>
                  <input type="checkbox" checked={formats[k]} onChange={(e) => setFormats({ ...formats, [k]: e.target.checked })} />
                  <strong>{name}</strong>
                  <span>{hint}</span>
                </label>
              ))}
            </div>

            {formats.pdf && (
              <fieldset className="ed-opts">
                <legend>PDF</legend>
                <label>Colour
                  <select value={pdf.color_mode} onChange={(e) => setPdf({ ...pdf, color_mode: e.target.value })}>
                    <option value="native">Keep the file's colours</option>
                    <option value="rgb">RGB</option>
                    <option value="cmyk">CMYK</option>
                  </select>
                </label>
                <label>Text
                  <select value={pdf.text} onChange={(e) => setPdf({ ...pdf, text: e.target.value })}>
                    <option value="embed">Embed fonts</option>
                    <option value="curves">Convert text to curves (no font needed by the printer)</option>
                  </select>
                </label>
                <label>Image resolution
                  <select value={pdf.bitmap_dpi} onChange={(e) => setPdf({ ...pdf, bitmap_dpi: +e.target.value })}>
                    {[72, 100, 150, 200, 300, 600].map((d) => <option key={d} value={d}>{d} dpi</option>)}
                  </select>
                </label>
              </fieldset>
            )}

            {wantsRaster && (
              <fieldset className="ed-opts">
                <legend>{formats.png && formats.jpeg ? "PNG and JPEG" : formats.png ? "PNG" : "JPEG"}</legend>
                <label>Size
                  <select value={size} onChange={(e) => setSize(e.target.value)}>
                    {SIZE_PRESETS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    <option value="custom">Custom dpi…</option>
                  </select>
                </label>
                {size === "custom" && (
                  <label>dpi
                    <input value={customDpi} inputMode="decimal" placeholder="e.g. 200" onChange={(e) => setCustomDpi(e.target.value)} />
                  </label>
                )}
                {formats.png && (
                  <label>PNG background
                    <select value={png.png_background} onChange={(e) => setPng({ ...png, png_background: e.target.value })}>
                      <option value="transparent">Transparent</option>
                      <option value="white">White</option>
                    </select>
                  </label>
                )}
                <label className="ed-check"><input type="checkbox" checked={png.antialias} onChange={(e) => setPng({ ...png, antialias: e.target.checked })} /> Smooth edges (anti-aliasing)</label>
                <div className={preview.error ? "ed-warn ed-warn-error" : "ed-hint"}>
                  {preview.error || `${preview.w_px} × ${preview.h_px} px (${preview.megapixels} MP) from a ${fmt(scene.page.width, "in")} × ${fmt(scene.page.height, "in")} in page`}
                </div>
                {formats.jpeg && <div className="ed-hint">JPEG compression is CorelDRAW's own default - its automation interface does not expose a quality setting, and the file is never re-encoded elsewhere.</div>}
              </fieldset>
            )}

            {fontIssues.length > 0 && (
              <div className="ed-warn">
                {fontIssues.map((f) => `"${f.font}" (${f.count} edited text object${f.count === 1 ? "" : "s"})`).join(", ")}{" "}
                {fontIssues.length === 1 ? "is" : "are"} not installed on this machine, so CorelDRAW will substitute another font. Install it or pick an installed font first.
              </div>
            )}

            <div className="ed-modal-actions">
              <button className="ed-btn" onClick={onClose}>Cancel</button>
              <button className="btn" disabled={blocked} onClick={start}>Generate {chosen.length ? chosen.map((c) => c.toUpperCase()).join(" + ") : ""}</button>
            </div>
          </>
        )}

        {phase === "running" && (
          <>
            <div className="progress-bar"><div className="progress-fill" style={{ width: `${pct}%` }} /></div>
            <div className="ed-progress-line">
              <span>{label}{status && status.sub ? ` (${status.sub.done} of ${status.sub.total})` : "…"}</span>
              <span>{pct}%</span>
            </div>
            <p className="ed-hint">CorelDRAW is working on the file. Keep this tab open; it can take a minute for large boards.</p>
          </>
        )}

        {phase === "failed" && (
          <>
            <p className="err">{error}</p>
            <div className="ed-modal-actions">
              <button className="ed-btn" onClick={onClose}>Close</button>
              <button className="btn" onClick={() => setPhase("choose")}>Back to options</button>
            </div>
          </>
        )}

        {phase === "done" && status && (
          <>
            {status.report && status.report.mock && <div className="ed-warn">Mock engine: these are placeholder files, not a CorelDRAW export.</div>}
            {status.report && status.report.verification && (
              status.report.verification.ok ? (
                <div className="ed-ok">
                  {status.report.verification.compared
                    ? `Verified: all ${status.report.verification.compared} objects in the exported file match your edits.`
                    : "No edits to verify."}
                </div>
              ) : (
                <div className="ed-warn">
                  The exported file differs from the editor in {status.report.verification.mismatches.length} place(s).{" "}
                  <button className="ed-link" onClick={() => setShowMismatches(!showMismatches)}>{showMismatches ? "Hide" : "Show"} details</button>
                  {showMismatches && <ul>{status.report.verification.mismatches.map((m, i) => <li key={i}>{m}</li>)}</ul>}
                </div>
              )
            )}
            {status.report && status.report.warnings.filter((w) => !w.startsWith("The exported document differs") && !w.startsWith("Mock")).map((w, i) => (
              <div className="ed-warn" key={i}>{w}</div>
            ))}

            <div className="ed-results">
              {Object.entries(status.files).map(([kind, name]) => (
                <div className="ed-result" key={kind}>
                  <div>
                    <strong>{kind.toUpperCase()}</strong>{" "}
                    <span className="ed-hint">
                      {status.report.file_bytes && status.report.file_bytes[kind] ? fmtBytes(status.report.file_bytes[kind]) : ""}
                      {status.report.pixels && status.report.pixels[kind] ? ` · ${status.report.pixels[kind][0]} × ${status.report.pixels[kind][1]} px` : ""}
                    </span>
                  </div>
                  <a className="btn small" href={fileUrl(name)} download={name}>Download</a>
                </div>
              ))}
            </div>
            {(status.files.png || status.files.jpeg) && (
              <img className="ed-result-preview" alt="Exported preview" src={fileUrl(status.files.png || status.files.jpeg)} />
            )}
            <div className="ed-modal-actions">
              <button className="ed-btn" onClick={() => { setPhase("choose"); setJob(null); setStatus(null); }}>Export again</button>
              <button className="btn" onClick={onClose}>Done</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
