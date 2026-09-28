import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, Download, FileArchive, FileCode, FileText, Image as ImageIcon, Loader2, RotateCw, X } from "lucide-react";
import { useSteppedProgress } from "../hooks/useSteppedProgress.js";
import { STEP_LABELS, fmtBytes, missingFonts } from "../editor/exportMath.js";
import {
  CDR_VERSIONS, DPI_CHOICES, EXPORT_FORMATS, PADDING_CHOICES, PDF_DPI_CHOICES,
  buildExportOptions, defaultSettings, rasterPreview,
} from "../utils/exportOptions.js";
import { motion } from "framer-motion";
import { BACKDROP_MOTION, CARD_MOTION } from "./modalMotion.js";
import "./ExportModal.css";

const ICON = { pdf: FileText, jpeg: ImageIcon, cdr: FileCode, png: ImageIcon };
const MM_PER_IN = 25.4;

/**
 * "Export Signage Files" for one converted shop, opened from its row in the Shops queue. It prepares the board (the
 * editor's scene, built once by CorelDRAW if the board was never opened in the editor), then asks CorelDRAW to replay the
 * shop's saved edits and write each chosen format with the options on its card. Every file comes from CorelDRAW.
 */
export default function ExportModal({ jobId, shopId, shopName, onClose }) {
  const [phase, setPhase] = useState("loading"); // loading | choose | running | done | failed
  const [prep, setPrep] = useState(null); // {pct, step} while the scene builds
  const [loadError, setLoadError] = useState(null); // {status, detail}
  const [loadTry, setLoadTry] = useState(0);
  const [board, setBoard] = useState(null); // {scene, ops}
  const [fonts, setFonts] = useState(null);
  const [picked, setPicked] = useState({ pdf: true, jpeg: false, cdr: true, png: false });
  const [settings, setSettings] = useState(null);
  const [running, setRunning] = useState(null); // {formats, export_id, plan}
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [estimates, setEstimates] = useState({});
  const autoDownloaded = useRef(false);

  const base = `/api/editor/${jobId}/${shopId}`;

  // ----------------------------------------------------------- prepare the board
  useEffect(() => {
    let cancelled = false;
    let timer;
    async function go(retry) {
      try {
        const r = await fetch(`${base}/scene${retry ? "?retry=1" : ""}`);
        if (cancelled) return;
        if (r.status === 202) {
          const b = await r.json();
          setPrep({ pct: b.progress_pct || 0, step: b.step });
          timer = setTimeout(() => go(false), 800);
          return;
        }
        if (!r.ok) {
          let detail = "";
          try { detail = (await r.json()).detail; } catch { /* not json */ }
          setLoadError({ status: r.status, detail: detail || `HTTP ${r.status}` });
          return;
        }
        const { ops } = await r.json();
        // The page size and edited text AFTER the saved edits - what CorelDRAW will actually export.
        const rep = await fetch(`${base}/replayed`);
        if (cancelled) return;
        if (!rep.ok) {
          let detail = "";
          try { detail = (await rep.json()).detail; } catch { /* not json */ }
          setLoadError({ status: rep.status, detail: detail || `HTTP ${rep.status}` });
          return;
        }
        const scene = await rep.json();
        if (cancelled) return;
        setBoard({ scene, ops: (ops || []).length });
        setSettings(defaultSettings(scene.page.width, scene.page.height));
        setPhase("choose");
      } catch (e) {
        if (!cancelled) setLoadError({ status: 0, detail: e.message });
      }
    }
    setLoadError(null);
    setPhase("loading");
    go(loadTry > 0);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [base, loadTry]);

  useEffect(() => {
    fetch("/api/fonts").then((r) => r.json()).then(setFonts).catch(() => {});
    fetch("/api/editor/export-estimates").then((r) => r.json()).then(setEstimates).catch(() => {});
  }, []);

  // --------------------------------------------------------------- close on Esc
  const closable = phase !== "running";
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && closable) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closable, onClose]);

  // ------------------------------------------------------------------- validity
  const page = board && board.scene.page;
  const previews = useMemo(() => {
    if (!page || !settings) return {};
    return {
      jpeg: rasterPreview(page.width, page.height, settings.jpeg.dpi, 0),
      png: rasterPreview(page.width, page.height, settings.png.dpi, settings.png.padding_mm),
    };
  }, [page, settings]);
  const invalid = (k) => (k === "jpeg" || k === "png") && !!(previews[k] && previews[k].error);
  const chosen = EXPORT_FORMATS.map((f) => f.key).filter((k) => picked[k]);
  const fontIssues = useMemo(() => (board ? missingFonts(board.scene, fonts) : []), [board, fonts]);

  // --------------------------------------------------------------------- export
  const start = useCallback(async (formats) => {
    setError("");
    autoDownloaded.current = false;
    try {
      const r = await fetch(`${base}/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ formats, options: buildExportOptions(settings) }),
      });
      const body = await r.json();
      if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
      setRunning({ formats, ...body });
      setStatus({ step: "launch", status: "queued" });
      setPhase("running");
    } catch (e) {
      setError(e.message);
      setPhase("failed");
    }
  }, [base, settings]);

  useEffect(() => {
    if (phase !== "running" || !running) return undefined;
    const t = setInterval(async () => {
      try {
        const s = await (await fetch(`${base}/export/${running.export_id}`)).json();
        setStatus(s);
        if (s.status === "done") { clearInterval(t); setPhase("done"); }
        else if (s.status === "failed") { clearInterval(t); setError(s.error || "The export failed"); setPhase("failed"); }
      } catch { /* transient - keep polling */ }
    }, 700);
    return () => clearInterval(t);
  }, [phase, running, base]);

  const pct = useSteppedProgress(running ? running.plan : [], status && status.step, status && status.status === "done", estimates);

  const fileUrl = (name) => `${base}/exports/${running.export_id}/files/${encodeURIComponent(name)}`;
  const zipUrl = running ? `${base}/exports/${running.export_id}/zip` : "";

  // One file downloads as itself, several as the server-built .zip - started automatically once, the links stay.
  useEffect(() => {
    if (phase !== "done" || !status || !status.files || autoDownloaded.current) return;
    autoDownloaded.current = true;
    const names = Object.values(status.files);
    download(names.length === 1 ? fileUrl(names[0]) : zipUrl, names.length === 1 ? names[0] : "");
  }, [phase, status]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (fmt, patch) => setSettings((s) => ({ ...s, [fmt]: { ...s[fmt], ...patch } }));

  return (
    <motion.div className="xm-back" {...BACKDROP_MOTION} onMouseDown={(e) => { if (e.target === e.currentTarget && closable) onClose(); }}>
      <motion.div {...CARD_MOTION} className="xm" role="dialog" aria-modal="true" aria-labelledby="xm-title">
        <header className="xm-head">
          <div>
            <h2 id="xm-title">Export Signage Files</h2>
            <p className="xm-sub">
              {shopName}
              {page ? ` · ${fmtIn(page.width)} × ${fmtIn(page.height)} in` : ""}
              {board ? ` · ${board.ops ? `${board.ops} saved edit${board.ops === 1 ? "" : "s"} included` : "no editor edits"}` : ""}
            </p>
          </div>
          {closable && (
            <button className="xm-x" onClick={onClose} aria-label="Close" title="Close (Esc)">
              <X size={18} />
            </button>
          )}
        </header>

        {phase === "loading" && (
          <div className="xm-body xm-center">
            {loadError ? (
              <>
                <p className="xm-err">
                  {loadError.status === 409 ? "This shop has not been converted yet." : `Could not prepare the board: ${loadError.detail}`}
                </p>
                {loadError.status !== 409 && (
                  <button className="xm-btn" onClick={() => setLoadTry((n) => n + 1)}><RotateCw size={14} /> Retry</button>
                )}
              </>
            ) : (
              <>
                <Loader2 className="xm-spin" size={26} />
                <p>{prep ? `Preparing the board in CorelDRAW (first export or edit of this shop) - ${prep.pct}%` : "Loading the board..."}</p>
                {prep && <div className="progress-bar small xm-prep"><div className="progress-fill" style={{ width: `${prep.pct}%` }} /></div>}
              </>
            )}
          </div>
        )}

        {phase === "choose" && settings && (
          <>
            <div className="xm-body">
              <div className="xm-cards">
                {EXPORT_FORMATS.map(({ key, label, hint }) => {
                  const Icon = ICON[key];
                  return (
                    <section key={key} className={`xm-card${picked[key] ? " on" : ""}`}>
                      <div className="xm-card-head">
                        <label className="xm-pick">
                          <input type="checkbox" checked={picked[key]} onChange={(e) => setPicked({ ...picked, [key]: e.target.checked })} />
                          <span className={`xm-ico ${key}`}><Icon size={18} /></span>
                          <span>
                            <strong>{label}</strong>
                            <small>{hint}</small>
                          </span>
                        </label>
                        <button className="xm-card-dl" disabled={invalid(key)} onClick={() => start([key])} title={`Download only the ${label}`}>
                          <Download size={14} /> {label}
                        </button>
                      </div>
                      <div className="xm-opts">
                        {key === "pdf" && (
                          <>
                            <Field label="Preset">
                              <Seg value={settings.pdf.color_mode} onChange={(v) => set("pdf", { color_mode: v })}
                                options={[["cmyk", "Print-Ready CMYK"], ["rgb", "Digital Web RGB"]]} />
                            </Field>
                            <Field label="Image resolution">
                              <Seg value={settings.pdf.bitmap_dpi} onChange={(v) => set("pdf", { bitmap_dpi: v })}
                                options={PDF_DPI_CHOICES.map((d) => [d, `${d} DPI`])} />
                            </Field>
                            <Toggle checked={settings.pdf.crop_marks} onChange={(v) => set("pdf", { crop_marks: v })}>Crop marks</Toggle>
                            <Toggle checked={settings.pdf.bleed} onChange={(v) => set("pdf", { bleed: v })}>Include bleed</Toggle>
                            <Toggle checked={settings.pdf.curves} onChange={(v) => set("pdf", { curves: v })}>Text as curves (printer needs no fonts)</Toggle>
                          </>
                        )}
                        {key === "jpeg" && (
                          <>
                            <Field label="Colour mode">
                              <Seg value={settings.jpeg.color} onChange={(v) => set("jpeg", { color: v })} options={[["rgb", "RGB"], ["cmyk", "CMYK"]]} />
                            </Field>
                            <Field label="Resolution">
                              <Seg value={settings.jpeg.dpi} onChange={(v) => set("jpeg", { dpi: v })}
                                options={DPI_CHOICES.map((d) => [d, `${d} DPI`, !!rasterPreview(page.width, page.height, d, 0).error])} />
                            </Field>
                            <SizeLine preview={previews.jpeg} />
                            <p className="xm-note">
                              Quality: CorelDRAW&apos;s own JPEG compression. Its automation interface has no quality setting (tested), and the file
                              is never re-encoded outside CorelDRAW.
                            </p>
                          </>
                        )}
                        {key === "cdr" && (
                          <>
                            <Field label="Version">
                              <select className="xm-select" value={settings.cdr.version} onChange={(e) => set("cdr", { version: +e.target.value })}>
                                {CDR_VERSIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                              </select>
                            </Field>
                            <Field label="Text">
                              <Seg value={settings.cdr.text} onChange={(v) => set("cdr", { text: v })}
                                options={[["editable", "Editable text"], ["curves", "Text as curves"]]} />
                            </Field>
                          </>
                        )}
                        {key === "png" && (
                          <>
                            <Toggle checked={settings.png.transparent} onChange={(v) => set("png", { transparent: v })}>Transparent background</Toggle>
                            <Field label="Resolution">
                              <Seg value={settings.png.dpi} onChange={(v) => set("png", { dpi: v })}
                                options={DPI_CHOICES.map((d) => [d, `${d} DPI`, !!rasterPreview(page.width, page.height, d, settings.png.padding_mm).error])} />
                            </Field>
                            <Field label="Padding">
                              <Seg value={settings.png.padding_mm} onChange={(v) => set("png", { padding_mm: v })}
                                options={PADDING_CHOICES.map((p) => [p, p ? `${p} mm` : "None"])} />
                            </Field>
                            <SizeLine preview={previews.png} />
                          </>
                        )}
                      </div>
                    </section>
                  );
                })}
              </div>

              {fontIssues.length > 0 && (
                <div className="xm-warn">
                  {fontIssues.map((f) => `"${f.font}"`).join(", ")} {fontIssues.length === 1 ? "is" : "are"} used by edited text but not installed on this
                  machine - CorelDRAW will substitute another font.
                </div>
              )}
            </div>
            <footer className="xm-foot">
              <span className="xm-foot-hint">Files are written by CorelDRAW from the converted board; nothing is overwritten.</span>
              <button className="xm-btn" onClick={onClose}>Cancel</button>
              <button className="xm-primary" disabled={!chosen.length || chosen.some(invalid)} onClick={() => start(chosen)}>
                {chosen.length > 1 ? <FileArchive size={15} /> : <Download size={15} />}
                {chosen.length > 1 ? `Download Selected (${chosen.length}) .zip` : chosen.length ? `Download ${EXPORT_FORMATS.find((f) => f.key === chosen[0]).label}` : "Select a format"}
              </button>
            </footer>
          </>
        )}

        {phase === "running" && (
          <div className="xm-body">
            <div className="progress-bar"><div className="progress-fill" style={{ width: `${pct}%` }} /></div>
            <div className="xm-progress-line">
              <span>
                {status && status.step ? STEP_LABELS[status.step] || status.step : "Queued"}
                {status && status.sub ? ` (${status.sub.done} of ${status.sub.total})` : "..."}
              </span>
              <span>{pct}%</span>
            </div>
            <p className="xm-note">CorelDRAW is writing {running.formats.map(labelOf).join(", ")}. Large boards can take a minute.</p>
          </div>
        )}

        {phase === "failed" && (
          <>
            <div className="xm-body"><p className="xm-err">{error}</p></div>
            <footer className="xm-foot">
              <button className="xm-btn" onClick={onClose}>Close</button>
              <button className="xm-primary" onClick={() => setPhase(board ? "choose" : "loading")}>Back to options</button>
            </footer>
          </>
        )}

        {phase === "done" && status && (
          <>
            <div className="xm-body">
              {status.report && status.report.mock && <div className="xm-warn">Mock engine: placeholder files, not a CorelDRAW export.</div>}
              {status.report && status.report.verification && !status.report.verification.ok && (
                <div className="xm-warn">The exported file differs from the editor in {status.report.verification.mismatches.length} place(s).</div>
              )}
              {status.report && (status.report.warnings || []).filter((w) => !w.startsWith("Mock") && !w.startsWith("The exported document differs")).map((w, i) => (
                <div className="xm-warn" key={i}>{w}</div>
              ))}
              <p className="xm-ok"><CheckCircle2 size={15} /> Ready - your download should start automatically.</p>
              <div className="xm-files">
                {Object.entries(status.files).map(([kind, name]) => {
                  const Icon = ICON[kind] || FileText;
                  const meta = [
                    status.report && status.report.file_bytes && status.report.file_bytes[kind] ? fmtBytes(status.report.file_bytes[kind]) : "",
                    status.report && status.report.pixels && status.report.pixels[kind] ? `${status.report.pixels[kind][0]} × ${status.report.pixels[kind][1]} px` : "",
                  ].filter(Boolean);
                  return (
                    <div className="xm-file" key={kind}>
                      <span className={`xm-ico ${kind}`}><Icon size={16} /></span>
                      <span className="xm-file-name">{labelOf(kind)}<small>{meta.join(" · ")}</small></span>
                      <a className="xm-card-dl" href={fileUrl(name)} download={name}><Download size={14} /> Download</a>
                    </div>
                  );
                })}
              </div>
            </div>
            <footer className="xm-foot">
              {Object.keys(status.files).length > 1 && (
                <a className="xm-btn" href={zipUrl} download><FileArchive size={14} /> Download All (.zip)</a>
              )}
              <span className="xm-foot-hint" />
              <button className="xm-btn" onClick={() => { setPhase("choose"); setRunning(null); setStatus(null); }}>
                <RotateCw size={14} /> Export more
              </button>
              <button className="xm-primary" onClick={onClose}><CheckCircle2 size={15} /> Done</button>
            </footer>
          </>
        )}
      </motion.div>
    </motion.div>
  );
}

const labelOf = (k) => (EXPORT_FORMATS.find((f) => f.key === k) || { label: k.toUpperCase() }).label;
const fmtIn = (mm) => String(Math.round((mm / MM_PER_IN) * 100) / 100);

function download(href, name) {
  const a = document.createElement("a");
  a.href = href;
  a.download = name || "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function Field({ label, children }) {
  return (
    <div className="xm-field">
      <span className="xm-field-label">{label}</span>
      {children}
    </div>
  );
}

// Segmented buttons: [value, label, disabled?][]
function Seg({ value, options, onChange }) {
  return (
    <div className="xm-seg" role="radiogroup">
      {options.map(([v, l, disabled]) => (
        <button
          key={v}
          type="button"
          role="radio"
          aria-checked={v === value}
          className={v === value ? "on" : ""}
          disabled={disabled}
          title={disabled ? "Too large for this board (over 20000 px or 200 MP)" : undefined}
          onClick={() => onChange(v)}
        >
          {l}
        </button>
      ))}
    </div>
  );
}

function Toggle({ checked, onChange, children }) {
  return (
    <label className="xm-toggle">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="xm-switch" aria-hidden="true" />
      {children}
    </label>
  );
}

function SizeLine({ preview }) {
  if (!preview) return null;
  return <div className={preview.error ? "xm-size err" : "xm-size"}>{preview.error || `${preview.w_px} × ${preview.h_px} px · ${preview.megapixels} MP`}</div>;
}
