import { useEffect, useRef, useState } from "react";
import { UploadCloud, X } from "lucide-react";
import { motion } from "framer-motion";
import { BACKDROP_MOTION, CARD_MOTION } from "./modalMotion.js";
import { fmtBytes } from "../utils/fileSize.js";
import "./ExportModal.css";
import "./MasterManagementModal.css";

const UNITS = ["in", "ft", "cm", "mm"];

/**
 * "Edit master": rename a master, change its orientation or default board size (PATCH /api/masters/{id}) and/or replace its .cdr
 * with a new version (PUT /api/masters/{id}/file, real upload progress). Used on the Masters page and on the Automation page.
 * `onSaved(master, { fileReplaced })` gets the master as the server now has it.
 */
function saveLabelFor(progress, saving) {
  if (progress !== null) return `Uploading ${progress}%`;
  return saving ? "Saving..." : "Save";
}

export default function MasterEditModal({ master, onSaved, onClose }) {
  const d = master.dimensions_default;
  const [name, setName] = useState(master.name);
  const [orientation, setOrientation] = useState(master.orientation);
  const [dims, setDims] = useState({ width: d ? String(d.width) : "", height: d ? String(d.height) : "", unit: d?.unit || "ft" });
  const [file, setFile] = useState(null);
  const [progress, setProgress] = useState(null); // null = idle, 0-100 while the file uploads
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef(null);
  const xhrRef = useRef(null);
  const busy = saving || progress !== null;

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, busy]);
  useEffect(() => () => xhrRef.current?.abort(), []);

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

  function replaceFile() {
    return new Promise((resolve, reject) => {
      const fd = new FormData();
      fd.append("master", file);
      const xhr = new XMLHttpRequest();
      xhrRef.current = xhr;
      xhr.open("PUT", `/api/masters/${encodeURIComponent(master.id)}/file`);
      xhr.upload.onprogress = (ev) => ev.lengthComputable && setProgress(Math.round((ev.loaded / ev.total) * 100));
      xhr.onload = () => {
        xhrRef.current = null;
        let body = {};
        try {
          body = JSON.parse(xhr.responseText);
        } catch {
          /* handled below */
        }
        if (xhr.status >= 200 && xhr.status < 300 && body.id) resolve(body);
        else reject(new Error(body.detail || `Upload failed (${xhr.status})`));
      };
      xhr.onerror = () => {
        xhrRef.current = null;
        reject(new Error("Upload failed - network error"));
      };
      setProgress(0);
      xhr.send(fd);
    });
  }

  async function save(e) {
    e.preventDefault();
    if (busy || !dimsValid) return;
    setError("");
    setSaving(true);
    try {
      const r = await fetch(`/api/masters/${encodeURIComponent(master.id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          orientation,
          dimensions_default: hasDims ? { width: +dims.width, height: +dims.height, unit: dims.unit } : null,
        }),
      });
      let updated = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(updated.detail || `Could not save (HTTP ${r.status})`);
      if (file) updated = await replaceFile();
      onSaved(updated, { fileReplaced: !!file, orientationChanged: orientation !== master.orientation });
      onClose();
    } catch (err) {
      setError(err.message || "Could not save the master");
    } finally {
      setSaving(false);
      setProgress(null);
    }
  }

  return (
    <motion.div className="xm-back" {...BACKDROP_MOTION} onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <motion.div {...CARD_MOTION} className="xm mm" role="dialog" aria-modal="true" aria-labelledby="me-title">
        <header className="xm-head">
          <div>
            <h2 id="me-title">Edit master</h2>
            <p className="xm-sub">{master.brand} - {master.file_name}</p>
          </div>
          <button className="xm-x" onClick={onClose} disabled={busy} aria-label="Close" title="Close (Esc)">
            <X size={18} />
          </button>
        </header>
        <form className="xm-body mm-form" onSubmit={save}>
          <label className="xm-field">
            <span className="xm-field-label">Name</span>
            <input className="mm-input" value={name} maxLength={120} placeholder="Automatic (Master 1, Master 2 ...)"
              onChange={(e) => setName(e.target.value)} disabled={busy} />
          </label>
          <label className="xm-field">
            <span className="xm-field-label">Orientation</span>
            <select className="xm-select" value={orientation} onChange={(e) => setOrientation(e.target.value)} disabled={busy}>
              <option value="landscape">Landscape (W:H {"≥"} 1.25)</option>
              <option value="portrait">Portrait (W:H {"<"} 1.25, incl. square)</option>
            </select>
          </label>
          <div className="xm-field">
            <span className="xm-field-label">Default size (optional)</span>
            <div className="mm-dims">
              <input className="mm-input" type="number" min="0" step="any" placeholder="W" aria-label="Default width" value={dims.width}
                onChange={(e) => setDims({ ...dims, width: e.target.value })} disabled={busy} />
              <span>{"×"}</span>
              <input className="mm-input" type="number" min="0" step="any" placeholder="H" aria-label="Default height" value={dims.height}
                onChange={(e) => setDims({ ...dims, height: e.target.value })} disabled={busy} />
              <select className="xm-select" aria-label="Default size unit" value={dims.unit} onChange={(e) => setDims({ ...dims, unit: e.target.value })} disabled={busy}>
                {UNITS.map((u) => <option key={u}>{u}</option>)}
              </select>
            </div>
          </div>
          <div className="xm-field mm-file-field">
            <span className="xm-field-label">Replace the file (optional)</span>
            <button type="button" className="xm-btn mm-file" onClick={() => fileRef.current?.click()} disabled={busy}>
              <UploadCloud size={15} /> {file ? file.name : `Keep ${master.file_name}`}
              {file && <small>{fmtBytes(file.size)}</small>}
            </button>
            <input ref={fileRef} type="file" accept=".cdr" hidden onChange={(e) => pickFile(e.target.files[0])} />
          </div>
          {progress !== null && (
            <div className="progress-bar mm-progress" aria-label="Upload progress">
              <div className="progress-fill" style={{ width: `${progress}%` }} />
            </div>
          )}
          {!dimsValid && <p className="xm-err mm-msg">Default size needs both a width and a height above 0 (or neither).</p>}
          {error && <p className="xm-err mm-msg">{error}</p>}
          <footer className="xm-foot">
            <span className="xm-foot-hint">Boards already made from this master stay as they are.</span>
            <button type="button" className="xm-btn" onClick={onClose} disabled={busy}>Cancel</button>
            <button className="xm-primary" type="submit" disabled={busy || !dimsValid}>
              {saveLabelFor(progress, saving)}
            </button>
          </footer>
        </form>
      </motion.div>
    </motion.div>
  );
}
