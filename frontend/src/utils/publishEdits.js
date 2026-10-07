// "Save Changes" makes the saved edits the board's new files: the server queues one export of them (POST .../publish). These helpers talk
// to it and word what the Automation tab tells the designer.

/** Ask the server to rebuild the board's files from its saved edits. Never throws: {status, export_id} or {status: "error", error}. */
export async function publishEdits(jobId, shopId, fetchImpl = fetch) {
  try {
    const r = await fetchImpl(`/api/editor/${jobId}/${shopId}/publish`, { method: "POST" });
    if (!r.ok) {
      let detail = `HTTP ${r.status}`;
      try {
        const b = await r.json();
        detail = typeof b.detail === "string" ? b.detail : JSON.stringify(b.detail);
      } catch { /* not json */ }
      return { status: "error", error: detail };
    }
    return await r.json();
  } catch (e) {
    return { status: "error", error: e.message };
  }
}

/** The line shown in the Automation tab right after a save. */
export function savedNotice(shopName, publish) {
  const name = `"${shopName}"`;
  switch (publish?.status) {
    case "building":
      return `Edits to ${name} saved. Its CDR / PDF / JPG are being rebuilt with the edits now (about a minute) - then the downloads and the ZIP carry the new version.`;
    case "ready":
      return `Edits to ${name} saved. Its files already carry these edits - downloads and the ZIP are up to date.`;
    case "unchanged":
      return `Changes to ${name} saved (no edits left, so its original files stay as they are).`;
    case "error":
      return `Edits to ${name} saved, but its new files could not be started: ${publish.error}. Use the download button on its row to export them.`;
    default:
      return `Edits to ${name} saved. Use the download button on its row to export the files.`;
  }
}

/** The line shown when the rebuild finished (or failed). */
export function finishedNotice(shopName, status, error) {
  return status === "done"
    ? `"${shopName}" is rebuilt with your edits - its downloads and the ZIP now carry the new version.`
    : `Rebuilding "${shopName}" with your edits failed: ${error || "unknown error"}. Use the download button on its row to try again.`;
}

/** Poll an export until it finishes; resolves with its final status object. `sleep` is injectable for tests. */
export async function waitForExport(jobId, shopId, exportId, { fetchImpl = fetch, sleep = (ms) => new Promise((r) => setTimeout(r, ms)), every = 3000, limit = 200 } = {}) {
  for (let i = 0; i < limit; i += 1) {
    try {
      const r = await fetchImpl(`/api/editor/${jobId}/${shopId}/export/${exportId}`);
      if (r.ok) {
        const st = await r.json();
        if (st.status === "done" || st.status === "failed") return st;
      }
    } catch { /* server busy: try again */ }
    await sleep(every);
  }
  return { status: "failed", error: "timed out waiting for the export" };
}
