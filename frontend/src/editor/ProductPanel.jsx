import { useMemo, useRef, useState } from "react";
import { mapSlots, updateSlotOp } from "./product_engine.js";
import { buildIndex } from "./ops.js";
import { ancestry } from "./model.js";

const KIND_LABELS = {
  product_image: "Product image",
  brand_title: "Brand title",
  product_title: "Product title",
  address: "Address",
  contact: "Contact",
};

/**
 * Right panel: product_engine.map_slots's detected slots (product image / brand title / product
 * title / address / contact - see CLAUDE.md "Product slots"), each row clickable to select (and so
 * highlight, via the canvas's existing selection outline - no separate highlight overlay) the shape
 * on the canvas, plus a control to change its content: a file picker for an image slot (uploads
 * through `POST /api/editor/{job}/{shop}/product-assets`, then dispatches `update_product_slot`) or
 * a text field for the other four (dispatches the same op with `text`). Both go through `onCommit`,
 * the editor's normal op pipeline, so they land on the undo/redo stack and the live SVG updates the
 * same way any other edit does - nothing here talks to the canvas directly.
 *
 * Not built here (out of this task's UI scope, though scene_ops.py/product_engine.js already support
 * it - see "Product slots" / this task's own COM-replay work): a font/size picker for text slots, and
 * a persistent highlight overlay for every slot at once (only the SELECTED slot is highlighted).
 */
export default function ProductPanel({ scene, sel, jobId, shopId, onSelect, onCommit, onToast }) {
  const { slots, warnings } = mapSlots(scene);
  const idx = useMemo(() => buildIndex(scene), [scene]);
  const [uploading, setUploading] = useState(null); // slotId while its upload is in flight
  const fileInputs = useRef({});

  if (!slots.length) return null; // nothing tagged/heuristically detected on this master

  const targetId = (slot) => slot.containerId || slot.nodeId;
  // the slot's shape may itself sit inside a plain group (not just a PowerClip) - selecting/
  // highlighting always exits to the top-level object, like clicking it on the canvas would.
  const topLevelId = (slot) => {
    const chain = ancestry(idx, targetId(slot));
    return chain.length ? chain[chain.length - 1].id : targetId(slot);
  };

  const uploadAndSwap = async (slot, file) => {
    setUploading(slot.slotId);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await fetch(`/api/editor/${jobId}/${shopId}/product-assets`, { method: "POST", body: fd });
      if (!r.ok) throw new Error(((await r.json()).detail) || `HTTP ${r.status}`);
      const asset = await r.json();
      onCommit(updateSlotOp(scene, slot.slotId, { asset }));
    } catch (e) {
      onToast(`Could not replace the image: ${e.message}`);
    } finally {
      setUploading(null);
    }
  };

  const applyText = (slot, node, value) => {
    const current = (node && node.text && node.text.content) || "";
    if (value === current) return;
    try {
      onCommit(updateSlotOp(scene, slot.slotId, { text: value }));
    } catch (e) {
      onToast(e.message);
    }
  };

  return (
    <section className="ed-panel ed-product">
      <h3>Product slots</h3>
      <div className="ed-product-list">
        {slots.map((slot) => {
          const node = scene && findFlat(scene, slot.nodeId);
          const isImage = slot.kind === "product_image";
          const selected = sel.includes(topLevelId(slot));
          return (
            <div key={slot.slotId} className={`ed-product-slot${selected ? " selected" : ""}`}>
              <button
                className="ed-product-slot-name"
                title={`${slot.source === "tag" ? "Tagged" : "Detected"} - click to select on the canvas`}
                onClick={() => onSelect([topLevelId(slot)], null)}
              >
                {KIND_LABELS[slot.kind] || slot.kind}
              </button>
              {isImage ? (
                <span className="ed-product-slot-control">
                  <input
                    ref={(el) => { fileInputs.current[slot.slotId] = el; }}
                    type="file"
                    accept="image/*"
                    style={{ display: "none" }}
                    onChange={(e) => {
                      const file = e.target.files[0];
                      e.target.value = "";
                      if (file) uploadAndSwap(slot, file);
                    }}
                  />
                  <button
                    className="ed-btn"
                    disabled={uploading === slot.slotId}
                    onClick={() => fileInputs.current[slot.slotId] && fileInputs.current[slot.slotId].click()}
                  >
                    {uploading === slot.slotId ? "Uploading…" : "Replace image…"}
                  </button>
                </span>
              ) : (
                <input
                  key={(node && node.text && node.text.content) || ""}
                  className="ed-product-slot-control ed-product-slot-text"
                  defaultValue={(node && node.text && node.text.content) || ""}
                  onBlur={(e) => applyText(slot, node, e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
                />
              )}
            </div>
          );
        })}
      </div>
      {warnings.length > 0 && (
        <ul className="ed-product-warnings">
          {warnings.map((w, i) => <li key={i}>{w}</li>)}
        </ul>
      )}
    </section>
  );
}

function findFlat(scene, id) {
  const walk = (nodes) => {
    for (const n of nodes) {
      if (n.id === id) return n;
      if (n.children) {
        const found = walk(n.children);
        if (found) return found;
      }
    }
    return null;
  };
  for (const layer of scene.layers) {
    const found = walk(layer.children);
    if (found) return found;
  }
  return null;
}
