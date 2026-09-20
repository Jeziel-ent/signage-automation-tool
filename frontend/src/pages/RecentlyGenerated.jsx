import { useEffect, useMemo, useState } from "react";

const KIND_LABELS = { cdr: "CDR", pdf: "PDF", report: "Report" };
const STATUS_LABELS = { new: "Not converted", queued: "Queued", converting: "Converting", done: "Done", failed: "Failed" };

function fileLabel(kind, filename) {
  if (KIND_LABELS[kind]) return KIND_LABELS[kind];
  const ext = (filename.split(".").pop() || "").toUpperCase(); // "preview" -> PNG or SVG
  return ext || kind;
}

function fmtDate(epochSeconds) {
  if (!epochSeconds) return "—";
  return new Date(epochSeconds * 1000).toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

const fileUrl = (row, filename) => `/api/v2/shops/${row.shop_id}/files/${encodeURIComponent(filename)}`;
const isImage = (filename) => /\.(png|svg|jpe?g)$/i.test(filename);

export default function RecentlyGenerated() {
  const [rows, setRows] = useState(null); // null = loading
  const [error, setError] = useState("");
  const [brandFilter, setBrandFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  async function load() {
    try {
      const r = await fetch("/api/v2/recent");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setRows(await r.json());
      setError("");
    } catch (e) {
      setError(`Could not load recent jobs (${e.message})`);
    }
  }

  useEffect(() => {
    load();
  }, []);

  // Keep the list live while anything is still queued/converting (e.g. a
  // conversion started from the Automation page in another tab).
  const anyActive = useMemo(() => (rows || []).some((r) => r.status === "queued" || r.status === "converting"), [rows]);
  useEffect(() => {
    if (!anyActive) return undefined;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [anyActive]);

  const brands = useMemo(() => [...new Set((rows || []).map((r) => r.brand))].sort(), [rows]);
  const visible = (rows || []).filter(
    (r) => (!brandFilter || r.brand === brandFilter) && (!statusFilter || r.status === statusFilter),
  );

  return (
    <div>
      <div className="row between" style={{ marginBottom: "var(--space-5)" }}>
        <h1 style={{ margin: 0 }}>Recently generated</h1>
        <div className="row">
          <select value={brandFilter} onChange={(e) => setBrandFilter(e.target.value)} aria-label="Filter by brand">
            <option value="">All brands</option>
            {brands.map((b) => (
              <option key={b}>{b}</option>
            ))}
          </select>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} aria-label="Filter by status">
            <option value="">All statuses</option>
            {Object.entries(STATUS_LABELS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
          <button className="btn ghost small" onClick={load}>
            Refresh
          </button>
        </div>
      </div>

      <section className="card">
        {error && <div className="err">{error}</div>}
        {rows === null && !error && <p className="hint">Loading…</p>}
        {rows !== null && rows.length === 0 && (
          <p className="hint">Nothing generated yet. Upload a master on the Automation page and convert a shop - it will appear here.</p>
        )}
        {rows !== null && rows.length > 0 && visible.length === 0 && <p className="hint">No jobs match these filters.</p>}
        {visible.length > 0 && (
          <table className="shops-table recent-table">
            <thead>
              <tr>
                <th>Preview</th>
                <th>Brand</th>
                <th>Shop</th>
                <th>Size</th>
                <th>Created</th>
                <th>Status</th>
                <th>Downloads</th>
                <th>Editor</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => (
                <tr key={r.shop_id}>
                  <td>
                    {r.files && r.files.preview && isImage(r.files.preview) ? (
                      <img className="recent-thumb" src={fileUrl(r, r.files.preview)} alt="" loading="lazy" />
                    ) : (
                      <div className="recent-thumb empty" />
                    )}
                  </td>
                  <td>{r.brand}</td>
                  <td>
                    <div>{r.name}</div>
                    <div className="hint small-text">{r.master_filename}</div>
                  </td>
                  <td>
                    {r.width} {r.width_unit} × {r.height} {r.height_unit}
                  </td>
                  <td>{fmtDate(r.created_at)}</td>
                  <td>
                    <span className={`pill ${r.status === "done" ? "done" : r.status === "failed" ? "error" : ""}`} title={r.error || undefined}>
                      {STATUS_LABELS[r.status] || r.status}
                    </span>
                  </td>
                  <td>
                    {r.files ? (
                      <div className="row" style={{ gap: "var(--space-1)" }}>
                        {Object.entries(r.files).map(([kind, filename]) => (
                          <a key={kind} className="btn ghost small" href={fileUrl(r, filename)} download={filename}>
                            {fileLabel(kind, filename)}
                          </a>
                        ))}
                      </div>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>
                    {r.status === "done" ? (
                      <button className="btn small" onClick={() => window.open(`/editor/${r.job_id}/${r.shop_id}`, "_blank")}>
                        Open in editor
                      </button>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
