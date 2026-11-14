import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronRight, Plus, Search, X } from "lucide-react";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import PageHeader from "../components/PageHeader.jsx";
import { MasterPanel } from "../components/QueueParts.jsx";
import { useMasters } from "../context/MasterContext.jsx";
import { brandRows, filterMasters, masterCount } from "../utils/masters.js";
import "../components/MasterPanels.css";
import "./Masters.css";

/**
 * Masters: a table of brands (S.No, brand, portrait and landscape counts). The arrow at the end of a row opens a panel that slides in from the
 * right with that brand's master files: search them, add more, edit or delete. It reads and writes the same registry as the Automation page
 * (context/MasterContext.jsx), so a master added here is in that page's Master Templates straight away, and the other way round.
 * Masters are stored - they stay after a server restart. Deleting one removes it from the server's master list for every designer;
 * boards already made from it stay under Recently generated.
 */
export default function Masters() {
  const registry = useMasters();
  const [search, setSearch] = useState("");
  const [openBrand, setOpenBrand] = useState("");        // the brand whose panel is open ("" = closed)
  const [masterSearch, setMasterSearch] = useState("");
  const [adding, setAdding] = useState(false);
  const [newBrand, setNewBrand] = useState("");
  const [notice, setNotice] = useState("");
  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const rows = useMemo(() => brandRows(registry.brands, registry.masters, search), [registry.brands, registry.masters, search]);
  const brandMasters = useMemo(() => registry.forBrand(openBrand), [registry, openBrand]);
  const shownMasters = useMemo(() => filterMasters(brandMasters, masterSearch), [brandMasters, masterSearch]);
  const total = masterCount(brandMasters);

  useEffect(() => {
    if (!openBrand) return undefined;
    const onKey = (e) => e.key === "Escape" && !pendingDelete && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openBrand, pendingDelete]);   // eslint-disable-line react-hooks/exhaustive-deps

  function open(name) {
    setMasterSearch("");
    setOpenBrand(name);
  }
  function close() {
    setOpenBrand("");
    setMasterSearch("");
  }

  async function addBrand() {
    const name = newBrand.trim();
    if (!name) return;
    try {
      await registry.addBrand(name);
    } catch (e) {
      setNotice(e.message);
      return;
    }
    setNewBrand("");
    setAdding(false);
    setNotice(`Brand "${name}" created. Open it with the arrow to add its masters.`);
  }

  function onAdded(m) {
    registry.add(m);
    setNotice(`${m.orientation === "landscape" ? "Landscape" : "Portrait"} master "${m.name}" added to ${m.brand}.`);
  }

  async function confirmDelete() {
    const m = pendingDelete;
    setDeleting(true);
    try {
      await registry.remove(m.id);
      setNotice(`Master "${m.name}" deleted from ${m.brand}.`);
    } catch (e) {
      setNotice(e.message);
    } finally {
      setDeleting(false);
      setPendingDelete(null);
    }
  }

  function onUpdated(m, { fileReplaced } = {}) {
    registry.update(m);
    setNotice(fileReplaced ? `Master "${m.name}" updated with the new file.` : `Master "${m.name}" updated.`);
  }

  return (
    <div className="ws-page mp-page">
      <PageHeader title="Masters" description="Master templates for each brand. Open a brand to add, edit or delete its masters." />
      <header className="ws-bar mp-bar">
        <label className="mp-search">
          <Search size={15} aria-hidden="true" />
          <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search brands or master files"
            aria-label="Search brands or master files" />
        </label>
        <div className="mp-bar-actions">
          {adding ? (
            <>
              <input className="ws-input" autoFocus placeholder="New brand name" value={newBrand} onChange={(e) => setNewBrand(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") addBrand(); if (e.key === "Escape") setAdding(false); }} aria-label="New brand name" />
              <button className="ws-cta" onClick={addBrand} disabled={!newBrand.trim()}>Add</button>
              <button className="ws-cta-ghost" onClick={() => { setAdding(false); setNewBrand(""); }}>Cancel</button>
            </>
          ) : (
            <button className="ws-cta" onClick={() => setAdding(true)}><Plus size={14} /> New Brand</button>
          )}
        </div>
      </header>

      {notice && (
        <output className="queue-notice mp-notice">
          <span>{notice}</span>
          <button type="button" className="icon-btn" onClick={() => setNotice("")} aria-label="Dismiss">{"×"}</button>
        </output>
      )}

      <section className="ws-card mp-card" aria-label="Brands and their masters">
        <div className="mp-table-wrap">
          <table className="mp-table">
            <thead>
              <tr>
                <th className="mp-sno">S.No</th>
                <th>Brand</th>
                <th className="mp-num">Portrait</th>
                <th className="mp-num">Landscape</th>
                <th className="mp-go"><span className="sr-only">Open</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.name} className={r.name === openBrand ? "active" : ""} onClick={() => open(r.name)} data-brand={r.name}>
                  <td className="mp-sno">{i + 1}</td>
                  <th scope="row" className="mp-brand">{r.name}</th>
                  <td className="mp-num">{r.portrait}</td>
                  <td className="mp-num">{r.landscape}</td>
                  <td className="mp-go">
                    <button type="button" className="icon-btn" onClick={(e) => { e.stopPropagation(); open(r.name); }}
                      aria-label={`Open masters of ${r.name}`} title={`Open masters of ${r.name}`}>
                      <ChevronRight size={18} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && !registry.loading && (
            <p className="mp-empty">{search.trim() ? `No brand or master matches "${search.trim()}".` : "No brands yet. Use New Brand to create one."}</p>
          )}
          {registry.error && <p className="err mp-empty">{registry.error}</p>}
        </div>

      </section>

      {createPortal(
        <>
        <div className={"mp-scrim" + (openBrand ? " on" : "")} onClick={close} aria-hidden="true" />
        <aside className={"mp-drawer" + (openBrand ? " open" : "")} aria-label={openBrand ? `Masters of ${openBrand}` : "Masters"} aria-hidden={!openBrand} inert={!openBrand}>
          <button type="button" className="mp-handle" onClick={close} aria-label="Close" title="Close"><ChevronRight size={20} /></button>
          {openBrand && (
            <>
              <div className="mp-drawer-head">
                <div>
                  <h2>{openBrand}</h2>
                  <span className="mp-drawer-sub">{total} master{total === 1 ? "" : "s"}</span>
                </div>
                <button type="button" className="icon-btn" onClick={close} aria-label="Close"><X size={18} /></button>
              </div>
              <label className="mp-search mp-drawer-search">
                <Search size={15} aria-hidden="true" />
                <input type="search" value={masterSearch} onChange={(e) => setMasterSearch(e.target.value)} placeholder="Search masters"
                  aria-label="Search masters" />
              </label>
              <div className="mp-drawer-body">
                {["landscape", "portrait"].map((o) => (
                  <MasterPanel key={o} orientation={o} masters={shownMasters} allMasters={brandMasters} brand={openBrand}
                    onRemove={setPendingDelete} onAdded={onAdded} onUpdated={onUpdated} />
                ))}
              </div>
            </>
          )}
        </aside>
        </>,
        document.querySelector(".app-shell") || document.body,
      )}

      {pendingDelete && (
        <ConfirmDialog title="Delete this master?" confirmLabel="Delete master" busy={deleting} onConfirm={confirmDelete} onCancel={() => setPendingDelete(null)}>
          <p><strong>{pendingDelete.name}</strong> ({pendingDelete.file_name || "master.cdr"}, {pendingDelete.orientation}) will be deleted from the server too, so it
            disappears for every designer and cannot be picked for new boards.</p>
          <p>Boards already made from it stay under Recently generated. This cannot be undone.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}
