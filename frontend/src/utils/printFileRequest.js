// POST /api/print-file/generate with real progress: XHR (fetch cannot report upload progress). Stages: "uploading" (0-100 %) ->
// "rendering" (the server is drawing the sheet; no percentage exists) -> "downloading" -> resolves {blob, disposition}.
export function postPrintFile(form, { onStage = () => {}, onUpload = () => {} } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/print-file/generate");
    xhr.responseType = "blob";
    onStage("uploading");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onUpload(Math.round((e.loaded / e.total) * 100)); };
    xhr.upload.onload = () => { onUpload(100); onStage("rendering"); };
    xhr.onprogress = () => onStage("downloading");
    xhr.onerror = () => reject(new Error("could not reach the server (is the backend running, and restarted after the last update?)"));
    xhr.onabort = () => reject(new Error("cancelled"));
    xhr.onload = async () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve({ blob: xhr.response, disposition: xhr.getResponseHeader("Content-Disposition") });
        return;
      }
      let detail = `HTTP ${xhr.status}`;
      try {
        const b = JSON.parse(await xhr.response.text());
        detail = typeof b.detail === "string" ? b.detail : JSON.stringify(b.detail);
      } catch { /* not json */ }
      reject(new Error(detail));
    };
    xhr.send(form);
  });
}
