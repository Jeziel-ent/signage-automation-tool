import { useEffect } from "react";
import { Download, FileCode, FileText, Image as ImageIcon, X } from "lucide-react";
import { motion } from "framer-motion";
import { BACKDROP_MOTION, CARD_MOTION } from "./modalMotion.js";
import { allDownloadUrl, SINGLE_FORMATS } from "../utils/shopDownload.js";
import "./ExportModal.css";

const ICONS = { cdr: FileCode, jpg: ImageIcon, png: ImageIcon, pdf: FileText };
const HINTS = {
  cdr: "Editable CorelDRAW source of every board",
  pdf: "Print PDF of every board",
  jpg: "JPG image of every board",
  png: "PNG image of every board",
};

/**
 * "Download All" in the Shops Queue footer: pick ONE format and every converted shop's file of that format downloads as one
 * ZIP (GET /api/v2/download-all), each under its standard name "<S.no> - <W> X <H> <Unit> - <Type> - <SHOP>.<ext>". The file
 * per shop is chosen like the row's own Download popup (the newest editor export of its current edits, else the
 * conversion's file); shops without that format are listed in MISSING_FILES.txt inside the ZIP. `shops` = converted rows
 * with `no` = their S.No.
 */
export default function DownloadAllModal({ shops, onClose }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <motion.div className="xm-back" {...BACKDROP_MOTION} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <motion.div {...CARD_MOTION} className="xm sd" role="dialog" aria-modal="true" aria-labelledby="da-title">
        <header className="xm-head">
          <div>
            <h2 id="da-title">Download All</h2>
            <p className="xm-sub">
              Choose a format - {shops.length} converted shop{shops.length === 1 ? "" : "s"} in one ZIP
            </p>
          </div>
          <button className="xm-x" onClick={onClose} aria-label="Close" title="Close (Esc)">
            <X size={18} />
          </button>
        </header>
        <div className="xm-body">
          <div className="sd-grid">
            {SINGLE_FORMATS.map((fmt) => {
              const Icon = ICONS[fmt];
              return (
                <a key={fmt} className="sd-btn" href={allDownloadUrl(shops, fmt)} download onClick={() => setTimeout(onClose, 0)}
                  title={`${HINTS[fmt]} (ZIP)`}>
                  <Icon size={16} />
                  <span>All {fmt.toUpperCase()} files</span>
                  <Download size={14} className="sd-dl" />
                </a>
              );
            })}
          </div>
          <ul className="sd-notes">
            <li>Shops without the chosen format are listed in MISSING_FILES.txt inside the ZIP.</li>
          </ul>
        </div>
        <footer className="xm-foot">
          <button className="xm-primary" onClick={onClose}>Cancel</button>
        </footer>
      </motion.div>
    </motion.div>
  );
}
