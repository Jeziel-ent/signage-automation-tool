import { useEffect, useRef, useState } from "react";
import { FileCheck2, Trash2, UploadCloud, X } from "lucide-react";
import { motion } from "framer-motion";
import { BACKDROP_MOTION, CARD_MOTION } from "./modalMotion.js";
import { ORIENTATIONS, mastersOf } from "../utils/masters.js";
import { fmtBytes } from "../utils/fileSize.js";
import { shopNameFromFileName } from "../utils/shopImport.js";
import { toTamil } from "../utils/tamilTranslit.js";
import "./ExportModal.css";
import "./MasterManagementModal.css";

const TITLE = { landscape: "Landscape", portrait: "Portrait" };
const UNITS = ["in", "ft", "cm", "mm"];

/**
 * "Manage Masters" (the brand bar's button and the Master Templates "+ Add Master"): every master template of the brand,
 * per orientation (any number of each), with Delete, and a form to upload a new one with a name, its orientation and an
 * optional default board size - POST /api/masters/upload, with a real upload progress bar (XMLHttpRequest, like
 * UploadDropzone). `masters` = the brand's { landscape, portrait }; `onAdded(master)` records a new one (and resets the
 * queue's conversions); `onDelete(master)` asks and deletes (resolves true when it did).
 */
export default function MasterManagementModal({ brand, masters, onAdded, onDelete, onClose }) {
  const [name, setName] = useState("");
  const [orientation, setOrientation] = useState("landscape");
  const [dims, setDims] = useState({ width: "", height: "", unit: "ft" });
  const [file, setFile] = useState(null);
  const [progress, setProgress] = useState(null); // null = idle, 0-100 while uploading
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState(null);
  const fileRef = useRef(null);
  const xhrRef = useRef(null);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && progress === null && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, progress]);
  useEffect(() => () => xhrRef.current?.abort(), []);

  const nextNumber = mastersOf(masters, orientation).length + 1;
  const hasDims = dims.width !== "" || dims.height !== "";
  const dimsValid = !hasDims || (+dims.width > 0 && +dims.height > 0);

  function pickFile(f) {
    setError("");
    if (f && !f.name.toLowerCase().endsWith(".cdr")) {
      setError("Please choose a .cdr file");
      return;
    }
    setFile(f || null);
  }

  function upload(e) {
    e.preventDefault();
    if (!file || !dimsValid || progress !== null) return;
    setError("");
    setProgress(0);
    const fd = new FormData();
    fd.append("master", file);
    fd.append("brand", brand);
    fd.append("orientation", orientation);
    fd.append("name", name.trim());
    if (hasDims) fd.append("dimensions_default", JSON.stringify({ width: +dims.width, height: +dims.height, unit: dims.unit }));
    // the shop name the master shows, from a designer-style file name (how conversion finds the text to replace)
    const shown = shopNameFromFileName(file.name);
    if (shown) {
      fd.append("master_shop_name", shown);
      const ta = toTamil(shown);
      if (ta) fd.append("master_shop_name_local", ta);
    }
    const xhr = new XMLHttpRequest();
    xhrRef.current = xhr;
    xhr.open("POST", "/api/masters/upload");
    xhr.upload.onprogress = (ev) => ev.lengthComputable && setProgress(Math.round((ev.loaded / ev.total) * 100));
    xhr.onload = () => {
      xhrRef.current = null;
      let body = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* handled below */
      }
      if (xhr.status >= 200 && xhr.status < 300 && body.id) {
        onAdded(body);
        setName("");
        setFile(null);
        setDims((d) => ({ ...d, width: "", height: "" }));
        if (fileRef.current) fileRef.current.value = "";
      } else {
        setError(body.detail || `Upload failed (${xhr.status})`);
      }
      setProgress(null);
    };
    xhr.onerror = () => {
      xhrRef.current = null;
      setError("Upload failed - network error");
      setProgress(null);
    };
    xhr.send(fd);
  }

  async function remove(m) {
    setBusyId(m.id);
    try {
      await onDelete(m);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <motion.div className="xm-back" {...BACKDROP_MOTION} onMouseDown={(e) => e.target === e.currentTarget && progress === null && onClose()}>
      <motion.div {...CARD_MOTION} className="xm mm" role="dialog" aria-modal="true" aria-labelledby="mm-title">
        <header className="xm-head">
          <div>
            <h2 id="mm-title">Manage Masters</h2>
            <p className="xm-sub">
              {brand} - {mastersOf(masters, "landscape").length} landscape, {mastersOf(masters, "portrait").length} portrait. Each queue
              row picks any master of its own orientation.
            </p>
          </div>
          <button className="xm-x" onClick={onClose} disabled={progress !== null} aria-label="Close" title="Close (Esc)">
            <X size={18} />
          </button>
        </header>

        <div className="xm-body">
          <form className="mm-form" onSubmit={upload}>
            <label className="xm-field">
              <span className="xm-field-label">Name</span>
              <input className="mm-input" value={name} maxLength={120} placeholder={`Master ${nextNumber}`}
                onChange={(e) => setName(e.target.value)} disabled={progress !== null} />
            </label>
            <label className="xm-field">
              <span className="xm-field-label">Orientation</span>
              <select className="xm-select" value={orientation} onChange={(e) => setOrientation(e.target.value)} disabled={progress !== null}>
                <option value="landscape">Landscape (W:H {"≥"} 1.25)</option>
                <option value="portrait">Portrait (W:H {"<"} 1.25, incl. square)</option>
              </select>
            </label>
            <div className="xm-field">
              <span className="xm-field-label">Default size (optional)</span>
              <div className="mm-dims">
                <input className="mm-input" type="number" min="0" step="any" placeholder="W" aria-label="Default width" value={dims.width}
                  onChange={(e) => setDims({ ...dims, width: e.target.value })} disabled={progress !== null} />
                <span>{"×"}</span>
                <input className="mm-input" type="number" min="0" step="any" placeholder="H" aria-label="Default height" value={dims.height}
                  onChange={(e) => setDims({ ...dims, height: e.target.value })} disabled={progress !== null} />
                <select className="xm-select" aria-label="Default size unit" value={dims.unit} onChange={(e) => setDims({ ...dims, unit: e.target.value })}
                  disabled={progress !== null}>
                  {UNITS.map((u) => <option key={u}>{u}</option>)}
                </select>
              </div>
            </div>
            <div className="xm-field mm-file-field">
              <span className="xm-field-label">Master file</span>
              <button type="button" className="xm-btn mm-file" onClick={() => fileRef.current?.click()} disabled={progress !== null}>
                <UploadCloud size={15} /> {file ? file.name : "Choose .cdr"}
                {file && <small>{fmtBytes(file.size)}</small>}
              </button>
              <input ref={fileRef} type="file" accept=".cdr" hidden onChange={(e) => pickFile(e.target.files[0])} />
            </div>
            <button className="xm-primary mm-submit" type="submit" disabled={!brand || !file || !dimsValid || progress !== null}>
              {progress === null ? "Upload master" : `Uploading ${progress}%`}
            </button>
            {progress !== null && (
              <div className="progress-bar mm-progress" aria-label="Upload progress">
                <div className="progress-fill" style={{ width: `${progress}%` }} />
              </div>
            )}
            {!dimsValid && <p className="xm-err mm-msg">Default size needs both a width and a height above 0 (or neither).</p>}
            {error && <p className="xm-err mm-msg">{error}</p>}
          </form>

          <div className="mm-lists">
            {ORIENTATIONS.map((o) => (
              <section key={o} className="mm-list" aria-label={`${TITLE[o]} masters`}>
                <h3>
                  {TITLE[o]} masters <span className="count-badge">{mastersOf(masters, o).length}</span>
                </h3>
                {mastersOf(masters, o).length === 0 && <p className="mm-empty">None yet - {o} boards fall back to the other orientation.</p>}
                <ul>
                  {mastersOf(masters, o).map((m, i) => (
                    <li key={m.id} className="mm-row" data-master-id={m.id}>
                      <div className="mm-thumb">
                        {m.preview_url ? <img src={m.preview_url} alt="" loading="lazy" /> : <FileCheck2 size={18} />}
                      </div>
                      <div className="mm-meta">
                        <strong title={m.name}>
                          {m.name}
                          {i === 0 && <span className="mc-default">default</span>}
                        </strong>
                        <small title={m.file_name}>
                          {m.file_name} {"·"} {fmtBytes(m.file_size)}
                          {m.dimensions_default && ` · ${m.dimensions_default.width} × ${m.dimensions_default.height} ${m.dimensions_default.unit}`}
                        </small>
                        <small>Added {new Date(m.created_at).toLocaleString()}</small>
                      </div>
                      <button className="icon-btn mm-del" onClick={() => remove(m)} disabled={busyId === m.id}
                        title={`Delete ${m.name}`} aria-label={`Delete ${m.name}`}>
                        <Trash2 size={15} />
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        </div>

        <footer className="xm-foot">
          <span className="xm-foot-hint">The first master of each orientation is its default. Deleting one keeps the boards already made from it.</span>
          <button className="xm-primary" onClick={onClose} disabled={progress !== null}>Done</button>
        </footer>
      </motion.div>
    </motion.div>
  );
}
