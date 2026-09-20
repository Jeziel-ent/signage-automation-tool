import { useRef, useState } from "react";

/**
 * Click-or-drag .cdr upload with a REAL progress bar driven by the
 * browser's own upload progress event (XMLHttpRequest.upload.onprogress) -
 * not a fake timer. `fetch()` doesn't expose upload progress reliably
 * across browsers, which is why this uses XHR directly. Works for large
 * files (e.g. 300MB) since the browser streams the multipart body and
 * reports real bytes-sent/bytes-total as it goes.
 */
export default function UploadDropzone({ brand, onUploaded, disabled }) {
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

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/v2/upload");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) setProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        setProgress(100);
        try {
          onUploaded(JSON.parse(xhr.responseText), file.name);
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
    <div>
      <div
        className={`dropzone${dragOver ? " drag" : ""}`}
        onClick={() => !disabled && inputRef.current?.click()}
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
            <div className="dropzone-icon">&#8679;</div>
            <div>Click or drag a master .cdr file here</div>
            <div className="dropzone-hint">Up to 300 MB</div>
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
