import { useRef, useState } from "react";
import { shopNameFromFileName } from "../utils/shopImport.js";
import { toTamil } from "../utils/tamilTranslit.js";
import { UploadCloud } from "lucide-react";

/**
 * Click-or-drag .cdr upload with a REAL progress bar driven by the
 * browser's own upload progress event (XMLHttpRequest.upload.onprogress) -
 * not a fake timer. `fetch()` doesn't expose upload progress reliably
 * across browsers, which is why this uses XHR directly. Works for large
 * files (e.g. 300MB) since the browser streams the multipart body and
 * reports real bytes-sent/bytes-total as it goes.
 */
export default function UploadDropzone({ brand, onUploaded, disabled, orientation = "landscape", label = "master", compact = false, card = null, strip = null }) {
  const inputRef = useRef(null);
  const [dragOver, setDragOver] = useState(false);
  const [progress, setProgress] = useState(null); // null = idle; 0-100 while uploading
  const [fileName, setFileName] = useState("");
  const [error, setError] = useState("");

  function upload(file) {
    if (!file || disabled) return;
    if (!file.name.toLowerCase().endsWith(".cdr")) {
      setError("Please choose a .cdr file");
      return;
    }
    setError("");
    setFileName(file.name);
    setProgress(0);

    const fd = new FormData();
    fd.append("master", file);
    fd.append("brand", brand || "");
    fd.append("orientation", orientation);
    // the shop name the master shows, from a designer-style file name, in English and (transliterated) Tamil: how the
    // server finds the text to replace - a master that writes its name only in Tamil has no English line to match
    const masterName = shopNameFromFileName(file.name);
    if (masterName) {
      fd.append("master_shop_name", masterName);
      const ta = toTamil(masterName);
      if (ta) fd.append("master_shop_name_local", ta);
    }

    const xhr = new XMLHttpRequest();
    // registers the master (GET /api/masters lists it); the response is that master: {id, name, orientation, preview_url, ...}
    xhr.open("POST", "/api/masters/upload");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) setProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        setProgress(100);
        try {
          onUploaded(JSON.parse(xhr.responseText), file.name, file.size);
        } catch {
          setError("Upload succeeded but the response was malformed");
        }
      } else {
        let detail = `Upload failed (${xhr.status})`;
        try {
          detail = JSON.parse(xhr.responseText).detail || detail;
        } catch {
          /* ignore */
        }
        setError(detail);
        setProgress(null);
      }
    };
    xhr.onerror = () => {
      setError("Upload failed - network error");
      setProgress(null);
    };
    xhr.send(fd);
  }

  return (
    <div className={card ? "mc-root" : strip ? "mt-root" : undefined}>
      <div // NOSONAR a native <button> cannot hold this block content (the hidden input, the progress bar)
        data-orientation={orientation}
        className={strip ? `mt-drop${strip.empty ? " empty" : ""}${dragOver ? " drag" : ""}`
          : card ? `master-card${dragOver ? " drag" : ""}` : `dropzone${dragOver ? " drag" : ""}${compact ? " compact" : ""}`}
        role="button"
        tabIndex={disabled ? -1 : 0}
        onClick={() => !disabled && inputRef.current?.click()}
        onKeyDown={(e) => { if (!disabled && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); inputRef.current?.click(); } }}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (!disabled) upload(e.dataTransfer.files[0]);
        }}
        style={disabled ? { opacity: 0.5, cursor: "not-allowed" } : undefined}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".cdr"
          hidden
          onChange={(e) => upload(e.target.files[0])}
        />
        {progress === null ? (
          <>
            {strip ? (
              <>
                <UploadCloud size={strip.empty ? 22 : 16} className="mt-drop-icon" />
                <span className="mt-drop-text">{strip.text}</span>
                <span className="mt-drop-browse">or browse</span>
              </>
            ) : card ? (
              <>
                <div className={`mc-icon ${card.tone || ""}`}>
                  <UploadCloud size={24} />
                </div>
                <h4 className="mc-title">{card.title}</h4>
                <p className="mc-help">{card.help}</p>
                <span className="mc-browse">{card.browse}</span>
              </>
            ) : (
              <>
                <UploadCloud className="dropzone-icon" size={compact ? 26 : 32} />
                <div>Click or drag the {label} .cdr file here</div>
                <div className="dropzone-hint">Up to 300 MB</div>
              </>
            )}
          </>
        ) : (
          <div className="upload-progress">
            <div className="upload-filename">{fileName}</div>
            <div className="progress-bar">
              <div className="progress-fill" style={{ width: `${progress}%` }} />
            </div>
            <div className="progress-pct">{progress}%</div>
          </div>
        )}
      </div>
      {error && <div className="err">{error}</div>}
    </div>
  );
}
