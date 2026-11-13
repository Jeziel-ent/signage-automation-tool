import { useCallback, useEffect, useMemo, useState } from "react";
import { Brain, Database, Download, FileText, FolderX, Image as ImageIcon, Layers, Loader2, Search, Trash2, X } from "lucide-react";
import { fmtBytes } from "../utils/fileSize.js";
import { readError } from "../utils/authApi.js";
import { byBrand, byDay, filesTotals } from "../utils/adminFiles.js";

const KIND_LABEL = { cdr: "CDR", pdf: "PDF", png: "PNG", jpg: "JPG", svg: "SVG", report: "Report", other: "File" };
const PART_LABEL = { scene: "Editor scene", exports: "Edited exports", product_assets: "Product images" };
const fmtDate = (t) => new Date(t * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
const fmtTime = (t) => new Date(t * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

/** "Files on the server": every master, board, backup and leftover the site keeps, with sizes, open/download and delete. */
export default function AdminFiles({ onClose, onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [cat, setCat] = useState("boards");
  const [q, setQ] = useState("");
  const [brand, setBrand] = useState("");
  const [confirm, setConfirm] = useState(null);      // {key, text, url}
  const [busy, setBusy] = useState(null);
  const [msg, setMsg] = useState(null);

  const load = useCallback(async () => {
    const r = await fetch("/api/admin/files");
    if (r.ok) { setData(await r.json()); setError(null); } else setError(await readError(r, "Could not list the files"));
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") (confirm ? setConfirm(null) : onClose()); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [confirm, onClose]);

  async function remove(c) {
    setBusy(c.key);
    setMsg(null);
    const r = await fetch(c.url, { method: "DELETE" });
    if (r.ok) {
      const body = await r.json();
      setMsg({ kind: "good", text: `${c.done} - ${fmtBytes(body.bytes)} freed.` });
      await load();
      onChanged?.();
    } else setMsg({ kind: "bad", text: await readError(r, "Could not delete it") });
    setBusy(null);
    setConfirm(null);
  }

  const totals = useMemo(() => filesTotals(data), [data]);
  const brands = useMemo(() => [...new Set((data?.boards || []).map((b) => b.brand))].sort(), [data]);
  const boards = useMemo(() => (data?.boards || []).filter((b) => (!brand || b.brand === brand)
    && (!q || `${b.name} ${b.brand} ${b.user_email || ""} ${b.pc || ""}`.toLowerCase().includes(q.toLowerCase()))), [data, q, brand]);

  const cats = [
    { key: "boards", label: "Designer output", Icon: ImageIcon, n: totals.boards.n, bytes: totals.boards.bytes },
    { key: "masters", label: "Masters", Icon: Layers, n: totals.masters.n, bytes: totals.masters.bytes },
    { key: "backups", label: "Database backups", Icon: Database, n: totals.backups.n, bytes: totals.backups.bytes },
    { key: "leftovers", label: "Leftover folders", Icon: FolderX, n: totals.orphans.n, bytes: totals.orphans.bytes },
  ];
  const ask = (c) => setConfirm(c);

  return (
    <div className="adf-backdrop" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="adf" role="dialog" aria-modal="true" aria-label="Files on the server">
        <header className="adf-head">
          <h2>Files on the server</h2>
          {data && <span className="adash-muted">{fmtBytes(totals.all)} in files · database {fmtBytes(data.database_bytes)}</span>}
          <button type="button" className="adash-icon" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </header>
        <div className="adf-body">
          <nav className="adf-nav" aria-label="Categories">
            {cats.map(({ key, label, Icon, n, bytes }) => (
              <button key={key} type="button" className={cat === key ? "on" : ""} onClick={() => setCat(key)}>
                <Icon size={15} /><span>{label}</span><small>{n} · {fmtBytes(bytes)}</small>
              </button>
            ))}
            {data && (
              <div className="adf-learn">
                <Brain size={15} />
                <p><b>{data.learned_records}</b> learned correction{data.learned_records === 1 ? " is" : "s are"} stored as data in the database (Corel Intelligence keeps no CDRs). Deleting files never removes {data.learned_records === 1 ? "it" : "them"}.</p>
              </div>
            )}
          </nav>
          <section className="adf-main">
            {msg && <p className={"adash-msg " + msg.kind} role="status">{msg.text}</p>}
            {error && <p className="adash-msg bad">{error}</p>}
            {!data ? <Loader2 className="spin" size={20} /> : (
              <>
                {cat === "boards" && (
                  <>
                    <div className="adash-filters">
                      <label className="adf-search"><Search size={14} /><input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search shop, brand, designer, PC..." aria-label="Search boards" /></label>
                      <select value={brand} onChange={(e) => setBrand(e.target.value)} aria-label="Brand">
                        <option value="">All brands</option>
                        {brands.map((b) => <option key={b} value={b}>{b}</option>)}
                      </select>
                    </div>
                    {!boards.length && <p className="adash-empty">No board files on the server.</p>}
                    {byDay(boards).map((g) => (
                      <div key={g.day} className="adf-group">
                        <h3>{g.label}<span className="adash-muted"> · {g.items.length} board{g.items.length === 1 ? "" : "s"} · {fmtBytes(g.bytes)}</span></h3>
                        {g.items.map((b) => (
                          <div key={b.shop_id} className="adf-row">
                            <img className="adf-thumb" src={`/api/v2/shops/${b.shop_id}/thumb`} alt="" loading="lazy" onError={(e) => { e.currentTarget.style.visibility = "hidden"; }} />
                            <div className="adf-info">
                              <div><b>{b.name}</b> <span className="adash-muted">· {b.brand} · {b.size} · {fmtTime(b.made_at)}</span>
                                {b.learned && <span className="adf-tag learn" title="A designer correction from this board is stored for Corel Intelligence"><Brain size={11} /> learned</span>}
                                {b.status !== "done" && <span className="adf-tag">{b.status}</span>}
                              </div>
                              <div className="adash-muted">{b.user_email ? `made by ${b.user_email}${b.pc ? ` on ${b.pc}` : ""}` : "made on this server"}</div>
                              <div className="adf-chips">
                                {b.files.map((f) => (
                                  <a key={f.name} className={"adf-chip " + f.kind} href={`/api/v2/shops/${b.shop_id}/files/${encodeURIComponent(f.name)}`} target="_blank" rel="noopener noreferrer" title={f.name}>
                                    <FileText size={12} /> {KIND_LABEL[f.kind]} <span>{fmtBytes(f.bytes)}</span>
                                  </a>
                                ))}
                                {Object.entries(b.parts).map(([part, bytes]) => (
                                  <span key={part} className="adf-chip part">
                                    {PART_LABEL[part] || part} <span>{fmtBytes(bytes)}</span>
                                    <button type="button" aria-label={`Delete the ${PART_LABEL[part] || part} of ${b.name}`} title="Delete this part only"
                                      onClick={() => ask({ key: `${b.shop_id}:${part}`, url: `/api/admin/files/boards/${b.shop_id}?part=${part}`,
                                        text: `Delete the ${(PART_LABEL[part] || part).toLowerCase()} of ${b.name} (${fmtBytes(bytes)})?${part === "scene" ? " It is rebuilt when the board is opened in the editor again." : ""}`,
                                        done: `${PART_LABEL[part] || part} of ${b.name} deleted` })}><X size={11} /></button>
                                  </span>
                                ))}
                              </div>
                            </div>
                            <div className="adf-size">{fmtBytes(b.bytes)}</div>
                            <button type="button" className="adash-icon danger" aria-label={`Delete ${b.name}`} title="Delete this board and its files"
                              disabled={busy === b.shop_id} onClick={() => ask({ key: b.shop_id, url: `/api/admin/files/boards/${b.shop_id}`,
                                text: `Delete ${b.name} and all its files (${fmtBytes(b.bytes)})? It disappears from Recently generated.${b.learned ? " What it taught Corel Intelligence is kept." : ""}`,
                                done: `${b.name} deleted` })}>
                              {busy === b.shop_id ? <Loader2 size={15} className="spin" /> : <Trash2 size={15} />}
                            </button>
                          </div>
                        ))}
                      </div>
                    ))}
                  </>
                )}
                {cat === "masters" && (
                  <>
                    {!data.masters.length && <p className="adash-empty">No master files on the server.</p>}
                    {byBrand(data.masters).map((g) => (
                      <div key={g.brand} className="adf-group">
                        <h3>{g.brand}<span className="adash-muted"> · {g.items.length} master{g.items.length === 1 ? "" : "s"} · {fmtBytes(g.bytes)}</span></h3>
                        {g.items.map((m) => (
                          <div key={m.id} className="adf-row">
                            {m.has_preview ? <img className="adf-thumb" src={`/api/v2/jobs/${m.id}/preview`} alt="" loading="lazy" /> : <div className="adf-thumb" />}
                            <div className="adf-info">
                              <div><b>{m.name}</b> <span className="adash-muted">· {m.file_name}{m.orientation ? ` · ${m.orientation}` : ""}</span>
                                {m.deleted && <span className="adf-tag" title="Removed from the Masters list; the file is still here">not listed</span>}</div>
                              <div className="adash-muted">uploaded {fmtDate(m.created_at)} · {m.boards} board{m.boards === 1 ? "" : "s"} made from it</div>
                            </div>
                            <div className="adf-size">{fmtBytes(m.bytes)}</div>
                            <a className="adash-icon" href={`/api/admin/files/masters/${m.id}/download`} aria-label={`Download ${m.file_name}`} title="Download the .cdr"><Download size={15} /></a>
                            <button type="button" className="adash-icon danger" aria-label={`Delete ${m.file_name}`} title="Delete the master file"
                              onClick={() => ask({ key: m.id, url: `/api/admin/files/masters/${m.id}`,
                                text: `Delete the master ${m.file_name} (${fmtBytes(m.bytes)})? Boards already made from it stay; new boards cannot use it.`,
                                done: `${m.file_name} deleted` })}><Trash2 size={15} /></button>
                          </div>
                        ))}
                      </div>
                    ))}
                  </>
                )}
                {cat === "backups" && (
                  <div className="adf-group">
                    {!data.backups.length && <p className="adash-empty">No backups yet. The backup service writes one every 24 hours and keeps the newest 14.</p>}
                    {data.backups.map((b) => (
                      <div key={b.name} className="adf-row slim">
                        <Database size={16} className="adash-muted" />
                        <div className="adf-info"><b>{b.name}</b> <span className="adash-muted">· {fmtDate(b.at)} {fmtTime(b.at)}</span></div>
                        <div className="adf-size">{fmtBytes(b.bytes)}</div>
                        <button type="button" className="adash-icon danger" aria-label={`Delete ${b.name}`}
                          onClick={() => ask({ key: b.name, url: `/api/admin/files/backups/${encodeURIComponent(b.name)}`, text: `Delete the backup ${b.name}?`, done: `${b.name} deleted` })}><Trash2 size={15} /></button>
                      </div>
                    ))}
                  </div>
                )}
                {cat === "leftovers" && (
                  <div className="adf-group">
                    {!data.orphans.length && <p className="adash-empty">No leftover folders.</p>}
                    {data.orphans.map((o) => (
                      <div key={`${o.job_id}/${o.folder}`} className="adf-row slim">
                        <FolderX size={16} className="adash-muted" />
                        <div className="adf-info"><b>{o.folder}</b> <span className="adash-muted">· a board folder whose board is gone</span></div>
                        <div className="adf-size">{fmtBytes(o.bytes)}</div>
                        <button type="button" className="adash-icon danger" aria-label={`Delete ${o.folder}`}
                          onClick={() => ask({ key: o.folder, url: `/api/admin/files/orphans/${o.job_id}/${o.folder}`, text: `Delete the leftover folder ${o.folder}?`, done: "Leftover folder deleted" })}><Trash2 size={15} /></button>
                      </div>
                    ))}
                    {Object.entries(data.other).some(([, v]) => v) && (
                      <p className="adash-muted adash-note">Also: {Object.entries(data.other).filter(([, v]) => v).map(([k, v]) => `${k.replace("_", " ")} ${fmtBytes(v)}`).join(" · ")} (cleaned up automatically).</p>
                    )}
                  </div>
                )}
              </>
            )}
          </section>
        </div>
        {confirm && (
          <div className="adf-confirm" role="alertdialog" aria-label="Confirm delete">
            <p>{confirm.text} This cannot be undone.</p>
            <div>
              <button type="button" className="adash-btn ghost" onClick={() => setConfirm(null)}>Cancel</button>
              <button type="button" className="adash-btn danger" disabled={!!busy} onClick={() => remove(confirm)}>
                {busy ? <Loader2 size={14} className="spin" /> : <Trash2 size={14} />} Delete
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
