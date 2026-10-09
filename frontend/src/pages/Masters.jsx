import { useMemo, useState } from "react";
import { Building2, Plus } from "lucide-react";
import BrandSelect from "../components/BrandSelect.jsx";
import { MasterPanel } from "../components/QueueParts.jsx";
import { useMasters } from "../context/MasterContext.jsx";
import { masterCount, mastersOf } from "../utils/masters.js";
import "../components/MasterPanels.css";

/**
 * Masters: pick a brand (or create one), then see, upload, edit and delete its landscape and portrait master templates. It reads and
 * writes the same registry as the Automation page (context/MasterContext.jsx), so a master uploaded here is in that page's Master
 * Templates straight away, and the other way round. Masters are stored - they stay after a server restart.
 */
export default function Masters() {
  const registry = useMasters();
  const brands = registry.brands;
  const brand = registry.mastersBrand;      // empty on a fresh load: pick a brand to see its masters (kept while you move between pages)
  const setBrand = registry.setMastersBrand;
  const [adding, setAdding] = useState(false);
  const [newBrand, setNewBrand] = useState("");
  const [notice, setNotice] = useState("");
  const masters = useMemo(() => registry.forBrand(brand), [registry, brand]);
  const total = masterCount(masters);
  const plural = total === 1 ? "" : "s";
  const badgeText = brand ? `${total} master${plural} for ${brand}` : "No brand selected";

  async function addBrand() {
    const name = newBrand.trim();
    if (!name) return;
    try {
      await registry.addBrand(name);
    } catch (e) {
      setNotice(e.message);
      return;
    }
    setBrand(name);
    setNewBrand("");
    setAdding(false);
  }

  function onAdded(m) {
    registry.add(m);
    setNotice(`${m.orientation === "landscape" ? "Landscape" : "Portrait"} master "${m.name}" added to ${m.brand}.`);
  }

  async function onRemove(m) {
    if (!window.confirm(`Delete ${m.orientation} master "${m.name}"? Boards already made from it stay under Recently generated.`)) return false;
    try {
      await registry.remove(m.id);
    } catch (e) {
      setNotice(e.message);
      return false;
    }
    setNotice(`Master "${m.name}" deleted.`);
    return true;
  }

  function onUpdated(m, { fileReplaced } = {}) {
    registry.update(m);
    setNotice(fileReplaced ? `Master "${m.name}" updated with the new file.` : `Master "${m.name}" updated.`);
  }

  return (
    <div className="ws-page">
      <header className="ws-bar">
        <div className="ws-bar-left">
          <span className="ws-bar-title"><Building2 size={14} /> Brand</span>
          <BrandSelect value={brand} options={brands} onChange={setBrand} />
          {adding ? (
            <>
              <input className="ws-input" autoFocus placeholder="New brand name" value={newBrand} onChange={(e) => setNewBrand(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addBrand()} />
              <button className="ws-cta" onClick={addBrand} disabled={!newBrand.trim()}>Add</button>
              <button className="ws-cta-ghost" onClick={() => setAdding(false)}>Cancel</button>
            </>
          ) : (
            <button className="ws-cta" onClick={() => setAdding(true)}><Plus size={14} /> New Brand</button>
          )}
        </div>
        <div className="ws-badges">
          <span className="ws-badge" title={`${mastersOf(masters, "landscape").length} landscape, ${mastersOf(masters, "portrait").length} portrait`}>
            {badgeText}
          </span>
        </div>
      </header>

      <div className="ws-stack">
        <section className="ws-card ws-masters" aria-label="Master Templates">
          <div className="ws-card-head">
            <h2>Master Templates</h2>
          </div>
          <div className="ws-card-body">
            {notice && (
              <output className="queue-notice">
                <span>{notice}</span>
                <button type="button" className="icon-btn" onClick={() => setNotice("")} aria-label="Dismiss">{"×"}</button>
              </output>
            )}
            <div className="mt-grid">
              {["landscape", "portrait"].map((o) => (
                <MasterPanel key={o} orientation={o} masters={masters} brand={brand} onRemove={onRemove} onAdded={onAdded} onUpdated={onUpdated} />
              ))}
            </div>
            {registry.error && <p className="err">{registry.error}</p>}
            {!brand && <p className="hint hero-note">Pick or create a brand above to see and upload its masters.</p>}
            {brand && total === 0 && !registry.loading && (
              <p className="hint hero-note">No masters for {brand} yet - drop a landscape and/or portrait .cdr above. They also appear on the Automation page.</p>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
