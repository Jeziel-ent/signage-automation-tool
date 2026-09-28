import { useEffect, useMemo, useState } from "react";
import { FileText, Image as ImageIcon, Loader2, Printer, X } from "lucide-react";
import { BOARD_TYPES, filenameFrom, fmtSqft, printTotals, sqFeet, todayISO } from "../utils/printSheet.js";
import "./ExportModal.css";

/**
 * "Create Print File Details": job metadata + a choice of converted shops, then POST /api/print-sheet/generate renders
 * the "Print Details" summary sheet (backend/app/print_sheet.py) and the browser downloads it.
 * `shops`: the queue's converted shops, each with `no` = its S.no in the table.
 */
export default function PrintFileModal({ shops, brand, onClose }) {
  const [title, setTitle] = useState(brand ? `${brand.toUpperCase()} - ACP BOARD` : "");
  const [projectNo, setProjectNo] = useState("");
  const [date, setDate] = useState(todayISO());
  const [location, setLocation] = useState("");
  const [boardType, setBoardType] = useState("ACP BOARD");
  const [picked, setPicked] = useState(() => new Set(shops.map((s) => s.id)));
  const [format, setFormat] = useState("pdf");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const selected = useMemo(() => shops.filter((s) => picked.has(s.id)), [shops, picked]);
  const totals = printTotals(selected);
  const allOn = selected.length === shops.length;

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const toggle = (id) => setPicked((p) => {
    const n = new Set(p);
    if (n.has(id)) n.delete(id);
    else n.add(id);
    return n;
  });

  async function create() {
    setError("");
    if (!title.trim()) { setError("Enter a header (printing file title)."); return; }
    if (!selected.length) { setError("Select at least one shop."); return; }
    setBusy(true);
    try {
      const r = await fetch("/api/print-sheet/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title, project_no: projectNo, date, location, board_type: boardType, format,
          shop_ids: selected.map((s) => s.id),
          numbers: Object.fromEntries(selected.map((s) => [s.id, s.no])),
        }),
      });
      if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try { const b = await r.json(); detail = typeof b.detail === "string" ? b.detail : JSON.stringify(b.detail); } catch { /* not json */ }
        throw new Error(detail);
      }
      const blob = await r.blob();
      const name = filenameFrom(r.headers.get("Content-Disposition"), `Print_Details.${format === "pdf" ? "pdf" : "jpg"}`);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      onClose();
    } catch (e) {
      setError(`Could not create the print file: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="xm-back" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="xm pf" role="dialog" aria-modal="true" aria-labelledby="pf-title">
        <header className="xm-head">
          <div>
            <h2 id="pf-title">Create Print File Details</h2>
            <p className="xm-sub">A &ldquo;Print Details&rdquo; summary sheet of the selected boards, for the print team.</p>
          </div>
          {!busy && (
            <button className="xm-x" onClick={onClose} aria-label="Close" title="Close (Esc)">
              <X size={18} />
            </button>
          )}
        </header>

        <div className="xm-body">
          <div className="pf-grid">
            <label className="pf-field pf-wide">
              <span className="xm-field-label">Header (printing file title)</span>
              <input className="pf-input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="ULTRATECH CEMENT LIMITED - ACP BOARD" autoFocus />
            </label>
            <label className="pf-field">
              <span className="xm-field-label">Project ID / Number</span>
              <input className="pf-input" value={projectNo} onChange={(e) => setProjectNo(e.target.value)} placeholder="22071195" />
            </label>
            <label className="pf-field">
              <span className="xm-field-label">Date</span>
              <input className="pf-input" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </label>
            <label className="pf-field">
              <span className="xm-field-label">Location</span>
              <input className="pf-input" value={location} onChange={(e) => setLocation(e.target.value)} placeholder="CHENNAI" />
            </label>
            <label className="pf-field">
              <span className="xm-field-label">Board type</span>
              <input className="pf-input" list="pf-board-types" value={boardType} onChange={(e) => setBoardType(e.target.value)} placeholder="ACP BOARD" />
              <datalist id="pf-board-types">
                {BOARD_TYPES.map((t) => <option key={t} value={t} />)}
              </datalist>
            </label>
          </div>

          <div className="pf-field">
            <div className="pf-list-head">
              <span className="xm-field-label">Shops / CDR files ({selected.length} of {shops.length})</span>
              <button type="button" className="pf-link" onClick={() => setPicked(allOn ? new Set() : new Set(shops.map((s) => s.id)))}>
                {allOn ? "Clear all" : "Select all"}
              </button>
            </div>
            <div className="pf-list" role="group" aria-label="Converted shops">
              {shops.map((s) => (
                <label key={s.id} className={`pf-shop${picked.has(s.id) ? " on" : ""}`}>
                  <input type="checkbox" checked={picked.has(s.id)} onChange={() => toggle(s.id)} />
                  <span className="pf-no">{String(s.no).padStart(2, "0")}</span>
                  <span className="pf-name">{s.name}</span>
                  <span className="pf-size">{s.width} &times; {s.height} {s.unit || s.width_unit}</span>
                  <span className="pf-sq">{fmtSqft(sqFeet(s))} sq ft</span>
                </label>
              ))}
            </div>
            <div className="pf-metrics" aria-live="polite">
              <span>Total Qty: <strong>{totals.qty} Nos</strong></span>
              <span>Total Sq.Feet: <strong>{fmtSqft(totals.sqft)}</strong></span>
            </div>
          </div>

          <div className="xm-field">
            <span className="xm-field-label">Export format</span>
            <div className="xm-seg" role="radiogroup">
              {[["pdf", "PDF", FileText], ["jpeg", "JPEG", ImageIcon]].map(([v, l, Icon]) => (
                <button key={v} type="button" role="radio" aria-checked={format === v} className={format === v ? "on" : ""} onClick={() => setFormat(v)}>
                  <Icon size={13} style={{ verticalAlign: "-2px", marginRight: 5 }} />{l}
                </button>
              ))}
            </div>
          </div>

          {error && <p className="xm-err" role="alert">{error}</p>}
        </div>

        <footer className="xm-foot">
          <span className="xm-foot-hint">A4 portrait, 300 dpi. Thumbnails use each board&apos;s latest export, else its preview.</span>
          <button className="xm-btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="xm-primary" onClick={create} disabled={busy || !selected.length}>
            {busy ? <Loader2 size={15} className="xm-spin-white" /> : <Printer size={15} />}
            {busy ? "Creating..." : "Create Print File"}
          </button>
        </footer>
      </div>
    </div>
  );
}
