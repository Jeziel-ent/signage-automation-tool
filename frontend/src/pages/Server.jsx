import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, HardDrive, Loader2, ScrollText, Trash2 } from "lucide-react";
import { useAuth } from "../context/AuthContext.jsx";
import { fmtBytes } from "../utils/fileSize.js";
import { readError } from "../utils/authApi.js";
import { CLEAN_LABELS, describeEntry, entryDetails, storageRows } from "../utils/serverView.js";
import "../components/Agent.css";
import "./Server.css";

const PAGE = 50;
const fmtTime = (t) => new Date(t * 1000).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });

/** Administrator page: problems on right now, what fills the disk (and a clean-up), and the audit log. */
export default function ServerPage() {
  const { isAdmin } = useAuth();
  const [status, setStatus] = useState(null);
  const [storage, setStorage] = useState(null);
  const [log, setLog] = useState({ entries: [], total: 0, actions: [] });
  const [action, setAction] = useState("");
  const [q, setQ] = useState("");
  const [days, setDays] = useState("0");
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  const loadStatus = useCallback(async () => {
    const [s, st] = await Promise.all([fetch("/api/admin/status"), fetch("/api/admin/storage")]);
    if (s.ok) setStatus(await s.json());
    if (st.ok) setStorage(await st.json());
  }, []);
  const loadLog = useCallback(async (offset = 0) => {
    const r = await fetch(`/api/admin/audit?limit=${PAGE}&offset=${offset}&action=${encodeURIComponent(action)}&q=${encodeURIComponent(q)}`);
    if (!r.ok) return setMsg({ kind: "err", text: await readError(r, "Could not load the log") });
    const body = await r.json();
    setLog((old) => (offset ? { ...body, entries: [...old.entries, ...body.entries] } : body));
  }, [action, q]);

  useEffect(() => { if (isAdmin) loadStatus(); }, [isAdmin, loadStatus]);
  useEffect(() => { if (isAdmin) loadLog(0); }, [isAdmin, loadLog]);

  if (!isAdmin) return <div className="srv-page"><h2>Server</h2><p>Only an administrator can see this page.</p></div>;

  async function clean(dryRun) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await fetch("/api/admin/cleanup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ days: Number(days), dry_run: dryRun }) });
      if (!r.ok) throw new Error(await readError(r, "The clean-up failed"));
      const res = await r.json();
      if (dryRun) setPreview(res);
      else {
        setPreview(null);
        setMsg({ kind: "ok", text: `Removed ${res.deleted} item(s), freed ${fmtBytes(res.bytes)}.` });
        await Promise.all([loadStatus(), loadLog(0)]);
      }
    } catch (e) {
      setMsg({ kind: "err", text: e.message });
    } finally {
      setBusy(false);
    }
  }

  const rows = storageRows(storage?.bytes, storage?.total_bytes);
  const problems = status?.problems || [];

  return (
    <div className="srv-page">
      <h2>Server</h2>

      <section className={"ws-card srv-card " + (problems.length ? "bad" : "ok")} aria-live="polite">
        <h3>{problems.length ? <AlertTriangle size={17} /> : <CheckCircle2 size={17} />} {problems.length ? "Needs attention" : "Everything looks fine"}</h3>
        {problems.map((p) => <p key={p.key} className="srv-problem"><b>{p.title}.</b> {p.detail}</p>)}
        {status && !status.webhook_configured && <p className="srv-note">Alerts are off: set SIGNAGE_ALERT_WEBHOOK to be told by chat when something goes wrong.</p>}
      </section>

      <section className="ws-card srv-card">
        <h3><HardDrive size={17} /> Disk</h3>
        {!storage ? <Loader2 className="spin" size={18} /> : (
          <>
            <p className="srv-note">{fmtBytes(storage.total_bytes)} used by the site · {fmtBytes(storage.free_bytes)} free on the disk</p>
            <table className="srv-table">
              <tbody>
                {rows.map((r) => (
                  <tr key={r.key}>
                    <td>{r.label}</td>
                    <td className="num">{fmtBytes(r.bytes)}</td>
                    <td className="bar"><span style={{ width: `${Math.max(2, r.share * 100)}%` }} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="srv-clean">
              <label>Also remove exports and editor scenes older than
                <select value={days} onChange={(e) => { setDays(e.target.value); setPreview(null); }}>
                  <option value="0">never (only temporary leftovers)</option>
                  <option value="30">30 days</option><option value="60">60 days</option><option value="90">90 days</option><option value="180">180 days</option>
                </select>
              </label>
              <button type="button" className="btn ghost small" onClick={() => clean(true)} disabled={busy}>Check what would go</button>
              {preview && (
                <>
                  <span className="srv-note">
                    {preview.would_delete ? `${preview.would_delete} item(s), ${fmtBytes(preview.bytes)}: ${Object.entries(preview.by_category).map(([k, v]) => `${CLEAN_LABELS[k] || k} ${fmtBytes(v.bytes)}`).join(", ")}` : "Nothing to remove."}
                  </span>
                  {preview.would_delete > 0 && <button type="button" className="btn small" onClick={() => clean(false)} disabled={busy}><Trash2 size={14} /> Remove them</button>}
                </>
              )}
            </div>
            <p className="srv-note">Masters, converted boards and the database are never removed here.{storage.retention_days ? ` Automatic clean-up: every 6 hours, ${storage.retention_days} days.` : " Automatic clean-up of old exports and scenes is off (SIGNAGE_RETENTION_DAYS)."}</p>
          </>
        )}
        {msg && <p className={"users-msg " + msg.kind} role="status">{msg.text}</p>}
      </section>

      <section className="ws-card srv-card">
        <h3><ScrollText size={17} /> Activity</h3>
        <div className="srv-filters">
          <select value={action} onChange={(e) => setAction(e.target.value)} aria-label="Filter by action">
            <option value="">All actions</option>
            {log.actions.map((a) => <option key={a} value={a}>{a.replaceAll("_", " ")}</option>)}
          </select>
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search person, file, address..." aria-label="Search the log" />
        </div>
        <table className="srv-table log">
          <tbody>
            {log.entries.map((e) => (
              <tr key={e.id}>
                <td className="when">{fmtTime(e.at)}</td>
                <td>{describeEntry(e)}{entryDetails(e) && <span className="srv-note"> — {entryDetails(e)}</span>}</td>
                <td className="ip">{e.ip}</td>
              </tr>
            ))}
            {!log.entries.length && <tr><td colSpan="3" className="srv-note">Nothing logged yet.</td></tr>}
          </tbody>
        </table>
        {log.entries.length < log.total && <button type="button" className="btn ghost small" onClick={() => loadLog(log.entries.length)}>Show more ({log.total - log.entries.length} older)</button>}
      </section>
    </div>
  );
}
