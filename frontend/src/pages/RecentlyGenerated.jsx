import { useEffect, useMemo, useState } from "react";
import { Activity, Building2, ExternalLink, RefreshCw, Search } from "lucide-react";
import PillSelect from "../components/PillSelect.jsx";
import AnimatedCount from "../components/AnimatedCount.jsx";
import { matchesSearch } from "../utils/recentFilter.js";
import { prefetchEditor } from "../utils/prefetchEditor.js";

const KIND_LABELS = { cdr: "CDR", pdf: "PDF", report: "Report" };
const STATUS_LABELS = { new: "Not converted", queued: "Queued", converting: "Converting", done: "Done", failed: "Failed" };
const STATUS_BADGE = { new: "badge-queued", queued: "badge-queued", converting: "badge-processing", done: "badge-done", failed: "badge-failed" };

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
const dims = (r) =>
  r.width_unit === r.height_unit ? `${r.width} × ${r.height} ${r.width_unit}` : `${r.width} ${r.width_unit} × ${r.height} ${r.height_unit}`;

export default function RecentlyGenerated() {
  const [rows, setRows] = useState(null); // null = loading
  const [error, setError] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedBrand, setSelectedBrand] = useState("");
  const [selectedStatus, setSelectedStatus] = useState("");
  const [refreshing, setRefreshing] = useState(false);

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
  async function refresh() {
    setRefreshing(true);
    await load();
    setRefreshing(false);
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

  const brandOptions = useMemo(
    () => [{ value: "", label: "All brands" }, ...[...new Set((rows || []).map((r) => r.brand))].sort().map((b) => ({ value: b, label: b }))],
    [rows],
  );
  const statusOptions = useMemo(() => [{ value: "", label: "All statuses" }, ...Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label }))], []);
  const visible = useMemo(
    () => (rows || []).filter((r) => (!selectedBrand || r.brand === selectedBrand) && (!selectedStatus || r.status === selectedStatus) && matchesSearch(r, searchTerm)),
    [rows, selectedBrand, selectedStatus, searchTerm],
  );
  const filtered = !!(searchTerm.trim() || selectedBrand || selectedStatus);

  return (
    <div className="rg-page">
      {/* control bar */}
      <header className="rg-bar">
        <div className="rg-search">
          <Search size={16} className="rg-search-icon" aria-hidden="true" />
          <input
            type="search"
            placeholder="Search by shop name or file name…"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            aria-label="Search generated jobs"
          />
        </div>
        <div className="rg-filters">
          <PillSelect value={selectedBrand} options={brandOptions} onChange={setSelectedBrand} icon={Building2} label="Filter by brand" />
          <PillSelect value={selectedStatus} options={statusOptions} onChange={setSelectedStatus} icon={Activity} label="Filter by status" />
          <button className="rg-refresh" onClick={refresh} disabled={refreshing}>
            <RefreshCw size={14} className={refreshing ? "spin" : ""} /> Refresh
          </button>
        </div>
      </header>

      {/* data table */}
      <section className="rg-table-card" aria-label="Generated jobs">
        {error && <div className="err rg-msg">{error}</div>}
        {rows === null && !error && <p className="hint rg-msg">Loading…</p>}
        {rows !== null && rows.length === 0 && (
          <p className="hint rg-msg">Nothing generated yet. Upload a master on the Automation page and convert a shop - it will appear here.</p>
        )}
        {rows !== null && rows.length > 0 && visible.length === 0 && <p className="hint rg-msg">No jobs match these filters.</p>}
        {visible.length > 0 && (
          <div className="rg-scroll">
            <table className="rg-table">
              <thead>
                <tr>
                  <th>Preview</th>
                  <th>Brand</th>
                  <th>Shop details</th>
                  <th>Dimensions</th>
                  <th>Generated</th>
                  <th>Status</th>
                  <th>Download assets</th>
                  <th>Editor</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((r) => {
                  const previewUrl = r.files && r.files.preview && isImage(r.files.preview) ? fileUrl(r, r.files.preview) : null;
                  return (
                    <tr key={r.shop_id}>
                      <td>
                        {previewUrl ? (
                          <img className="rg-thumb" src={previewUrl} alt={`${r.name} preview`} loading="lazy" title="Open preview" onClick={() => window.open(previewUrl, "_blank")} />
                        ) : (
                          <div className="rg-thumb empty" />
                        )}
                      </td>
                      <td className="rg-brand">{r.brand}</td>
                      <td className="rg-details">
                        <div className="rg-name" title={r.name}>{r.name}</div>
                        <div className="rg-sub" title={r.master_filename}>{r.master_filename}</div>
                      </td>
                      <td>
                        <span className="rg-dims">{dims(r)}</span>
                      </td>
                      <td className="rg-date">{fmtDate(r.created_at)}</td>
                      <td>
                        <span className={`badge ${STATUS_BADGE[r.status] || "badge-queued"}`} title={r.error || undefined}>
                          {STATUS_LABELS[r.status] || r.status}
                        </span>
                      </td>
                      <td>
                        {r.files ? (
                          <div className="rg-segment">
                            {Object.entries(r.files).map(([kind, filename]) => (
                              <a key={kind} href={fileUrl(r, filename)} download={filename} title={filename}>
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
                          <button className="rg-editor-btn" onMouseEnter={prefetchEditor} onFocus={prefetchEditor} onClick={() => window.open(`/editor/${r.job_id}/${r.shop_id}`, "_blank")}>
                            <ExternalLink size={13} /> Open in editor
                          </button>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {rows !== null && rows.length > 0 && (
          <div className="rg-foot">
            {filtered
              ? <><AnimatedCount value={visible.length} /> of {rows.length} jobs</>
              : <><AnimatedCount value={rows.length} /> job{rows.length === 1 ? "" : "s"}</>}
          </div>
        )}
      </section>
    </div>
  );
}
