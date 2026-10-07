import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Download, Image as ImageIcon, Search, X } from "lucide-react";
import { motion } from "framer-motion";
import { BACKDROP_MOTION, CARD_MOTION } from "./modalMotion.js";
import { downloadHref } from "../utils/shopDownload.js";
import { galleryItems } from "../utils/gallery.js";
import "./ExportModal.css";

const QUICK = ["cdr", "pdf", "png"];

/**
 * The Shops Queue's gallery (the header Eye button): every converted shop in the queue as a card - its preview (the 240 px
 * list thumbnail; click for the full CorelDRAW preview), English and Tamil names, board size and type, and one-click
 * CDR / PDF / PNG downloads under the standard file names. A search box filters by shop name (either script) or board
 * type. `shops` = the queue's converted rows, each with `no` = its S.No.
 */
export default function QueueGalleryModal({ shops, onClose }) {
  const [query, setQuery] = useState("");
  const [enlarged, setEnlarged] = useState(null); // {shop, src}
  const v = useMemo(() => Date.now(), []); // one cache-buster per opening: a re-exported board shows its new pixels
  const items = galleryItems(shops, query);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      if (enlarged) setEnlarged(null);
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enlarged, onClose]);

  async function enlarge(shop) {
    // the full preview's file name comes from the shop's status (the list rows may not carry `files`)
    setEnlarged({ shop, src: `/api/v2/shops/${shop.id}/thumb?size=1000&v=${v}` });
    try {
      const st = await fetch(`/api/v2/shops/${shop.id}/status`).then((r) => (r.ok ? r.json() : null));
      const preview = st?.files?.preview;
      if (preview) setEnlarged((cur) => (cur && cur.shop.id === shop.id
        ? { shop, src: `/api/v2/shops/${shop.id}/files/${encodeURIComponent(preview)}?v=${v}` } : cur));
    } catch {
      /* keep the thumbnail */
    }
  }

  return (
    <motion.div className="xm-back" {...BACKDROP_MOTION} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <motion.div {...CARD_MOTION} className="xm qg" role="dialog" aria-modal="true" aria-labelledby="qg-title">
        <header className="xm-head">
          <div>
            <h2 id="qg-title">Output Gallery</h2>
            <p className="xm-sub">{shops.length} converted board{shops.length === 1 ? "" : "s"} in this queue</p>
          </div>
          <button className="xm-x" onClick={onClose} aria-label="Close" title="Close (Esc)">
            <X size={18} />
          </button>
        </header>
        <div className="xm-body">
          {enlarged ? (
            <div className="qg-light">
              <button className="xm-btn" onClick={() => setEnlarged(null)}>
                <ArrowLeft size={14} /> Back to gallery
              </button>
              <img src={enlarged.src} alt={enlarged.shop.name} />
              <div className="qg-light-cap">
                <strong>{enlarged.shop.name}</strong>
                {enlarged.shop.shop_name_local && <span lang="ta">{enlarged.shop.shop_name_local}</span>}
              </div>
            </div>
          ) : (
            <>
              <label className="qg-search">
                <Search size={15} />
                <input autoFocus value={query} onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search by shop name or board type" aria-label="Search the gallery" />
              </label>
              {!items.length && <div className="pg-empty"><ImageIcon size={18} /> {shops.length ? "No board matches the search." : "No converted boards yet."}</div>}
              <div className="qg-grid">
                {items.map((s) => (
                  <figure key={s.id} className="qg-card">
                    <button className="qg-thumb" onClick={() => enlarge(s)} title="Click to enlarge">
                      <img src={`/api/v2/shops/${s.id}/thumb?size=720&v=${v}`} alt={s.name} loading="lazy"
                        onError={(e) => { e.currentTarget.style.visibility = "hidden"; }} />
                    </button>
                    <figcaption>
                      <div className="qg-name" title={s.name}><span className="qg-no">{s.no}</span> {s.name}</div>
                      {s.shop_name_local && <div className="qg-ta" lang="ta" title={s.shop_name_local}>{s.shop_name_local}</div>}
                      <div className="qg-meta">{+s.width} &times; {+s.height} {s.unit || s.width_unit || "in"}{s.board_type ? ` · ${s.board_type}` : ""}</div>
                      <div className="qg-dl">
                        {QUICK.map((fmt) => (
                          <a key={fmt} href={downloadHref(s, fmt)} download title={`Download the ${fmt.toUpperCase()}`}>
                            <Download size={12} /> {fmt.toUpperCase()}
                          </a>
                        ))}
                      </div>
                    </figcaption>
                  </figure>
                ))}
              </div>
            </>
          )}
        </div>
      </motion.div>
    </motion.div>
  );
}
