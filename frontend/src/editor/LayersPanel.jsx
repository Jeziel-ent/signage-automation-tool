import { useEffect, useMemo, useRef, useState } from "react";
import { buildIndex } from "./ops.js";
import { buildRows, insidePowerclip, planDrop } from "./model.js";
import { Caret, Eye, EyeOff, GroupIcon, KindIcon, Lock, UngroupIcon } from "./icons.jsx";

const TYPE_LABEL = { curve: "Curve", text: "Text", bitmap: "Bitmap", rectangle: "Rectangle", ellipse: "Ellipse", polygon: "Polygon", group: "Group", perfect_shape: "Perfect Shape", ole_object: "OLE Object", symbol: "Symbol", eps: "EPS" };

// Wording follows CorelDRAW's Object Manager: "Group of 3 Objects", "Artistic Text: ...", "Curve", "Bitmap" ...
// A name the designer gave the object in Corel wins over the generated label.
function rowLabel(node) {
  if (node.name) return node.name;
  if (node.kind === "group") return `Group of ${node.children.length} Object${node.children.length === 1 ? "" : "s"}`;
  if (node.kind === "powerclip") return `PowerClip (${node.children.length} Object${node.children.length === 1 ? "" : "s"})`;
  if (node.text) {
    const kind = node.text.kind === "paragraph" ? "Paragraph Text" : "Artistic Text";
    const body = (node.text.content || "").replace(/\s+/g, " ").trim().slice(0, 28);
    return body ? `${kind}: ${body}` : kind;
  }
  return TYPE_LABEL[node.type] || node.type;
}

/** Right panel 2: the layers tree, mirroring CorelDRAW's Object Manager (top of the stack first). */
// Enter / Space on the row itself (not on a button inside it) selects it, like a click.
function onRowKey(e, activate) {
  if (e.target !== e.currentTarget || (e.key !== "Enter" && e.key !== " ")) return;
  e.preventDefault();
  activate();
}

export default function LayersPanel({ scene, sel, ctx, onSelect, onCommit, nextId }) {
  const idx = useMemo(() => buildIndex(scene), [scene]);
  const [expanded, setExpanded] = useState(() => new Set(scene.layers.map((l) => l.id)));
  const [drop, setDrop] = useState(null); // {id, zone}
  const dragId = useRef(null);
  const listRef = useRef(null);

  // reveal the selection: expand every ancestor and scroll the first selected row into view
  useEffect(() => {
    if (!sel.length) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      for (const id of sel) {
        let e = idx.get(id);
        if (!e) continue;
        next.add(e.layer.id);
        for (let p = e.parent; p; p = idx.get(p.id).parent) next.add(p.id);
      }
      return next.size === prev.size ? prev : next;
    });
    const el = listRef.current?.querySelector(`[data-row="${sel[0]}"]`);
    if (el?.scrollIntoView) el.scrollIntoView({ block: "nearest" });
  }, [sel, idx]);

  const rows = useMemo(() => buildRows(scene, expanded), [scene, expanded]);
  const toggle = (id) => setExpanded((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  function clickRow(e, row) {
    if (row.isLayer) return toggle(row.id);
    const entry = idx.get(row.id);
    const parentId = entry.parent ? entry.parent.id : null;
    const additive = e.ctrlKey || e.metaKey || e.shiftKey;
    const cur = sel.map((id) => idx.get(id)).filter(Boolean);
    const sameList = cur.length && cur[0].list === entry.list;
    if (additive && sameList) {
      onSelect(sel.includes(row.id) ? sel.filter((i) => i !== row.id) : [...sel, row.id], parentId);
    } else {
      onSelect([row.id], parentId);
    }
  }

  const canGroup = sel.length >= 2 && sel.every((id) => idx.get(id) && idx.get(id).list === idx.get(sel[0]).list) && !sel.some((id) => idx.get(id).node.locked || insidePowerclip(idx, id));
  const selGroup = sel.length === 1 && idx.get(sel[0]) && idx.get(sel[0]).node.kind === "group" ? sel[0] : null;

  const group = () => canGroup && onCommit({ op: "group", ids: sel, group_id: nextId(), name: "Group" });
  const ungroup = (id) => onCommit({ op: "ungroup", id });

  function zoneFor(e, row) {
    const rect = e.currentTarget.getBoundingClientRect();
    const y = (e.clientY - rect.top) / rect.height;
    const draggingLayer = dragId.current && idx.get(dragId.current) && idx.get(dragId.current).isLayer;
    // dropping INTO a group can't apply to PowerClip contents (they are only restacked among their siblings), so rows
    // there, and any row while a clipped object is dragged, split into just above / below
    const clipDrag = dragId.current && !draggingLayer && idx.get(dragId.current) && insidePowerclip(idx, dragId.current);
    const container = (row.isLayer || row.node.kind === "group") && !clipDrag && !(!row.isLayer && insidePowerclip(idx, row.id));
    if (container && !draggingLayer && y > 0.25 && y < 0.75) return "into";
    return y < 0.5 ? "above" : "below";
  }

  return (
    <section className="ed-panel ed-layers">
      <h3>
        <span>Layers</span>
        <span className="ed-panel-actions">
          <button className="ed-icon-btn" title="Group (Ctrl+G)" disabled={!canGroup} onClick={group}><GroupIcon /> Group</button>
          <button className="ed-icon-btn" title="Ungroup (Ctrl+U)" disabled={!selGroup} onClick={() => selGroup && ungroup(selGroup)}><UngroupIcon /> Ungroup</button>
        </span>
      </h3>
      <div className="ed-tree" ref={listRef} onDragEnd={() => { dragId.current = null; setDrop(null); }}>
        {rows.map((row) => {
          const n = row.node;
          const selected = sel.includes(row.id);
          const dropCls = drop && drop.id === row.id ? ` drop-${drop.zone}` : "";
          const inCtx = ctx && row.id === ctx;
          const inClip = !row.isLayer && insidePowerclip(idx, row.id);
          return (
            <div
              key={row.id}
              data-row={row.id}
              className={`ed-row${selected ? " selected" : ""}${row.isLayer ? " layer" : ""}${n.visible === false ? " hidden" : ""}${inCtx ? " ctx" : ""}${inClip ? " clip-child" : ""}${dropCls}`}
              title={inClip ? "Inside a PowerClip - drag it above/below its siblings to restack it inside the clip; it can't be dragged out of the clip, grouped or deleted separately" : undefined}
              style={{ paddingLeft: 6 + row.depth * 16 }}
              draggable={row.isLayer || !row.locked}
              role="treeitem"
              aria-selected={selected}
              tabIndex={-1}
              onClick={(e) => clickRow(e, row)}
              onKeyDown={(e) => onRowKey(e, () => clickRow(e, row))}
              onDragStart={(e) => { dragId.current = row.id; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", row.id); }}
              onDragOver={(e) => {
                if (!dragId.current) return;
                const zone = zoneFor(e, row);
                if (planDrop(scene, dragId.current, row.id, zone)) {
                  e.preventDefault();
                  setDrop((d) => (d && d.id === row.id && d.zone === zone ? d : { id: row.id, zone }));
                } else setDrop(null);
              }}
              onDrop={(e) => {
                e.preventDefault();
                const plan = dragId.current && drop && planDrop(scene, dragId.current, row.id, drop.zone);
                dragId.current = null;
                setDrop(null);
                if (plan) onCommit(plan);
              }}
            >
              <button type="button" className="ed-caret" disabled={!row.hasChildren} aria-label="Expand or collapse" onClick={(e) => { e.stopPropagation(); toggle(row.id); }}>
                {row.hasChildren ? <Caret open={expanded.has(row.id)} /> : null}
              </button>
              {!row.isLayer && <span className="ed-kind"><KindIcon kind={n.kind} type={n.type} /></span>}
              <span className="ed-name" title={rowLabel(n)}>{row.isLayer ? n.name : rowLabel(n)}</span>
              {n.stale && <span className="ed-badge" title={n.text ? "Edited - drawn as live text here; CorelDRAW's exact result appears after Save and Generate" : "Edited - preview refreshes after Save and Generate"}>edited</span>}
              {row.locked && <span className="ed-lock" title="Locked"><Lock /></span>}
              {!row.isLayer && n.kind === "group" && (
                <button className="ed-icon-only" title="Ungroup" onClick={(e) => { e.stopPropagation(); ungroup(n.id); }}><UngroupIcon /></button>
              )}
              <button
                className="ed-icon-only"
                title={n.visible === false ? "Show" : "Hide"}
                onClick={(e) => { e.stopPropagation(); onCommit({ op: "visibility", id: row.id, visible: n.visible === false }); }}
              >
                {n.visible === false ? <EyeOff /> : <Eye />}
              </button>
            </div>
          );
        })}
      </div>
      <div className="ed-hint">Drag rows to reorder or move into a group; drag a layer to change the layer order. Ctrl+click selects several.</div>
    </section>
  );
}
