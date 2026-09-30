import { useEffect, useState } from "react";
import { Download, FileCode, FileText, Image as ImageIcon, Loader2, SlidersHorizontal, X } from "lucide-react";
import { motion } from "framer-motion";
import { BACKDROP_MOTION, CARD_MOTION } from "./modalMotion.js";
import { downloadHref, SINGLE_FORMATS } from "../utils/shopDownload.js";
import "./ExportModal.css";

const ICONS = { cdr: FileCode, jpg: ImageIcon, png: ImageIcon, pdf: FileText };

/**
 * A completed row's quick Download popup: one click downloads the board's existing CDR / JPG / PNG / PDF (the newest editor
 * export of its current edits, else the conversion's own file) under the standard name "<S.no> - <W> X <H> <Unit> - <Type> -
 * <SHOP>.<ext>". Formats that do not exist for this board are shown disabled with the reason. "More export options" opens the
 * full "Export Signage Files" dialog, which renders new files through CorelDRAW (print resolution, CMYK, bleed, ...).
 * `shop` carries `no` = its S.no in the table.
 */
export default function ShopDownloadModal({ shop, onClose, onMoreOptions }) {
  const [avail, setAvail] = useState(null); // {cdr|jpg|png|pdf: {available, source, note, reason}}
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    fetch(`/api/v2/shops/${shop.id}/downloads`)
      .then(async (r) => (r.ok ? r.json() : Promise.reject(new Error((await r.json().catch(() => ({}))).detail || "Could not read this board's files"))))
      .then((a) => alive && setAvail(a))
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [shop.id]);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const unit = shop.unit || shop.width_unit || "in";
  return (
    <motion.div className="xm-back" {...BACKDROP_MOTION} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <motion.div {...CARD_MOTION} className="xm sd" role="dialog" aria-modal="true" aria-labelledby="sd-title">
        <header className="xm-head">
          <div>
            <h2 id="sd-title">Download Shop Files</h2>
            <p className="xm-sub" title={shop.name}>
              <strong>{shop.name}</strong> &middot; {+shop.width} &times; {+shop.height} {unit}
            </p>
          </div>
          <button className="xm-x" onClick={onClose} aria-label="Close" title="Close (Esc)">
            <X size={18} />
          </button>
        </header>
        <div className="xm-body">
          {error && <div className="err">{error}</div>}
          {!avail && !error && (
            <div className="sd-loading"><Loader2 size={16} className="spin" /> Checking this board&rsquo;s files...</div>
          )}
          {avail && (
            <div className="sd-grid">
              {SINGLE_FORMATS.map((fmt) => {
                const a = avail[fmt] || {};
                const Icon = ICONS[fmt];
                return a.available ? (
                  <a key={fmt} className="sd-btn" href={downloadHref(shop, fmt)} download title={a.note || `Download the ${fmt.toUpperCase()}`}>
                    <Icon size={16} />
                    <span>Download {fmt.toUpperCase()}</span>
                    <Download size={14} className="sd-dl" />
                  </a>
                ) : (
                  <span key={fmt} className="sd-btn disabled" aria-disabled="true" title={a.reason || "Not available"}>
                    <Icon size={16} />
                    <span>{fmt.toUpperCase()} not available</span>
                  </span>
                );
              })}
            </div>
          )}
          {avail && SINGLE_FORMATS.some((f) => avail[f]?.note) && (
            <ul className="sd-notes">
              {[...new Set(SINGLE_FORMATS.map((f) => avail[f]?.note).filter(Boolean))].map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
        </div>
        <footer className="xm-foot">
          <button className="xm-btn" onClick={onMoreOptions} title="Render new files through CorelDRAW: print resolution, CMYK, bleed, text as curves...">
            <SlidersHorizontal size={14} /> More export options
          </button>
          <button className="xm-primary" onClick={onClose}>Done</button>
        </footer>
      </motion.div>
    </motion.div>
  );
}
