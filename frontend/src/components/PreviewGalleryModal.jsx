import { useEffect, useMemo, useState } from "react";
import { ExternalLink, Image as ImageIcon, Loader2, X } from "lucide-react";
import { motion } from "framer-motion";
import { BACKDROP_MOTION, CARD_MOTION } from "./modalMotion.js";
import { galleryImages } from "../utils/gallery.js";
import "./ExportModal.css";

/** The whole board, as large as the window allows (contain: never cropped, tall portrait boards included), with a strip of the other renders. */
function Viewer({ shop, images, sel, onSel }) {
  const cur = images[sel];
  return (
    <div className="pg-view">
      <a className="pg-main" href={cur.url} target="_blank" rel="noopener noreferrer" title="Open the full-size image in a new tab">
        <img key={cur.key} src={cur.url} alt={`${shop.name} - ${cur.label}`} />
        <ExternalLink size={16} className="pg-open" />
      </a>
      <div className="pg-cap">
        <strong>{cur.label}</strong>
        {cur.detail && <span>{cur.detail}</span>}
      </div>
      {images.length > 1 && (
        <div className="pg-strip" role="list">
          {images.map((img, i) => (
            <button key={img.key} type="button" role="listitem" className={"pg-thumb" + (i === sel ? " on" : "")} onClick={() => onSel(i)}
              title={`${img.label}${img.detail ? ` - ${img.detail}` : ""}`} aria-label={`Show ${img.label}`} aria-current={i === sel}>
              <img src={img.url} alt="" loading="lazy" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * A completed row's preview gallery (the Eye icon): every rendered image of that shop - the conversion's preview and the
 * PNG/JPEG of each finished editor export, newest first. Image URLs carry `?v=<time the gallery opened>`, so a board
 * re-exported from the Editor shows its new pixels instead of a cached copy. (Saving in the Editor stores the edits only;
 * new pixels exist once the edits are exported - the row's Download -> More export options.)
 */
export default function PreviewGalleryModal({ shop, onClose }) {
  const [state, setState] = useState({ loading: true, error: "", status: null, exports: [] });
  const [sel, setSel] = useState(0); // which image the big viewer shows
  const v = useMemo(() => Date.now(), []); // one cache-buster per opening

  useEffect(() => {
    let alive = true;
    const json = async (r) => (r.ok ? r.json() : Promise.reject(new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`)));
    Promise.all([
      fetch(`/api/v2/shops/${shop.id}/status`).then(json),
      fetch(`/api/editor/${shop.job_id}/${shop.id}/exports`).then(json).catch(() => []),
    ])
      .then(([status, exports]) => alive && setState({ loading: false, error: "", status, exports }))
      .catch((e) => alive && setState({ loading: false, error: e.message || "Could not load the previews", status: null, exports: [] }));
    return () => {
      alive = false;
    };
  }, [shop.id, shop.job_id]);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const images = state.status ? galleryImages(shop, state.status, state.exports, v) : [];
  return (
    <motion.div className="xm-back" {...BACKDROP_MOTION} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <motion.div {...CARD_MOTION} className="xm pg" role="dialog" aria-modal="true" aria-labelledby="pg-title">
        <header className="xm-head">
          <div>
            <h2 id="pg-title">Output Previews</h2>
            <p className="xm-sub" title={shop.name}>
              <strong>{shop.name}</strong> &middot; {+shop.width} &times; {+shop.height} {shop.unit || shop.width_unit || "in"}
            </p>
          </div>
          <button className="xm-x" onClick={onClose} aria-label="Close" title="Close (Esc)">
            <X size={18} />
          </button>
        </header>
        <div className="xm-body">
          {state.loading && <div className="sd-loading"><Loader2 size={16} className="spin" /> Loading previews...</div>}
          {state.error && <div className="err">{state.error}</div>}
          {!state.loading && !state.error && !images.length && (
            <div className="pg-empty"><ImageIcon size={18} /> No rendered image of this board yet.</div>
          )}
          {images.length > 0 && <Viewer shop={shop} images={images} sel={Math.min(sel, images.length - 1)} onSel={setSel} />}
        </div>
        <footer className="xm-foot">
          <span className="pg-hint">Editor changes show here after an export (Download &rarr; More export options).</span>
          <button className="xm-primary" onClick={onClose}>Close</button>
        </footer>
      </motion.div>
    </motion.div>
  );
}
