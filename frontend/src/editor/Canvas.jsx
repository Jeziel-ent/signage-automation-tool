import { memo, useEffect, useMemo, useRef, useState } from "react";
import { buildIndex, mapBox } from "./ops.js";
import { clipChildAt, flattenLeaves, hitTest, insidePowerclip, leavesOf, marqueeSelect, resolveTarget, snapMove, snapResize, snapTargets, unionBox } from "./model.js";
import { toScene, zoomAt } from "./view.js";
import TextEditor from "./TextEditor.jsx";

const HANDLES = [
  ["nw", 0, 0], ["n", 0.5, 0], ["ne", 1, 0], ["e", 1, 0.5],
  ["se", 1, 1], ["s", 0.5, 1], ["sw", 0, 1], ["w", 0, 0.5],
];
const CURSORS = { nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize", n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize" };
const MIN_MM = 0.5;
const DRAG_PX = 3;
const HANDLE_PX = 9;
const SNAP_PX = 6;
const r4 = (v) => Math.round(v * 1e4) / 1e4;

/** New selection box when `handle` is dragged by (dx, dy) mm (dy up). Corners keep the aspect ratio unless `free`. */
export function resizeBox(handle, start, dx, dy, free) {
  let { x, y, w, h } = start;
  const west = handle.includes("w");
  const east = handle.includes("e");
  const north = handle.includes("n");
  const south = handle.includes("s");
  let nw = w + (east ? dx : west ? -dx : 0);
  let nh = h + (north ? dy : south ? -dy : 0);
  const corner = (west || east) && (north || south);
  if (corner && !free) {
    const sx = nw / w;
    const sy = nh / h;
    const s = Math.abs(sx - 1) > Math.abs(sy - 1) ? sx : sy;
    nw = w * s;
    nh = h * s;
  }
  nw = Math.max(nw, MIN_MM);
  nh = Math.max(nh, MIN_MM);
  return { x: west ? x + w - nw : x, y: south ? y + h - nh : y, w: nw, h: nh };
}

// Every leaf's <image>. Memoised on the leaves array so pointer-move re-renders of
// the overlay never touch the (potentially several hundred) images. `hideId`
// (the node currently under a live text/font preview - see Canvas's own
// textPreview rendering below) is skipped entirely rather than just covered,
// so the live SVG <text> preview is the only thing visible for that shape,
// not CorelDRAW's stale render peeking out from underneath or through any
// transparent pixels in it.
const SceneImages = memo(function SceneImages({ leaves, assetBase, pageH, imgRefs, hideId }) {
  return leaves.map((n) =>
    n.image && n.id !== hideId ? (
      <image
        key={n.id}
        ref={(el) => {
          if (el) imgRefs.current.set(n.id, el);
          else imgRefs.current.delete(n.id);
        }}
        data-id={n.id}
        href={assetBase + n.image.file}
        x={n.x}
        y={pageH - n.y - n.h}
        width={n.w}
        height={n.h}
        preserveAspectRatio="none"
      />
    ) : null,
  );
});

export default function Canvas({ scene, assetBase, sel, ctx, view, setView, showRender, snap, alphaMaps, fonts, textPreview, editingId, onEditText, onTextApply, onEditEnd, onSelect, onCommit, onToast, onCursor, onSize }) {
  const rootRef = useRef(null);
  const svgRef = useRef(null);
  const imgRefs = useRef(new Map());
  const drag = useRef(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [overlay, setOverlay] = useState(null); // {box} while moving/resizing, {marquee} while rubber-banding
  const [hover, setHover] = useState(null);
  const [panning, setPanning] = useState(false);

  const pageW = scene.page.width;
  const pageH = scene.page.height;
  const idx = useMemo(() => buildIndex(scene), [scene]);
  const leaves = useMemo(() => flattenLeaves(scene), [scene]);
  const selNodes = useMemo(() => sel.map((id) => idx.get(id)?.node).filter(Boolean), [sel, idx]);
  const selBox = useMemo(() => unionBox(selNodes), [selNodes]);
  // A PowerClip child only accepts text edits (see ops.js checkEditable) - it is outlined
  // and selectable, but never offered move/resize handles that would only be rejected.
  const selInClip = selNodes.some((n) => insidePowerclip(idx, n.id));
  const selLocked = selNodes.some((n) => n.locked || idx.get(n.id).layer.locked) || selInClip;

  const latest = useRef({});
  latest.current = { scene, view, pageH, idx, leaves, sel, ctx, alphaMaps, assetBase, selBox, selNodes, selLocked };

  useEffect(() => {
    const el = rootRef.current;
    const ro = new ResizeObserver(() => {
      const s = { w: Math.max(200, el.clientWidth), h: Math.max(200, el.clientHeight) };
      setSize(s);
      onSize(s);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [onSize]);

  useEffect(() => {
    alphaMaps.preload(leaves.filter((n) => n.image).map((n) => ({ url: assetBase + n.image.file, aspect: n.w / Math.max(n.h, 1e-6) })));
  }, [leaves, assetBase, alphaMaps]);

  // wheel = zoom about the cursor (needs a non-passive listener to preventDefault); shift+wheel pans sideways
  useEffect(() => {
    const el = svgRef.current;
    const onWheel = (e) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const { view: v, pageH: ph } = latest.current;
      if (e.shiftKey) {
        setView({ ...v, x: v.x - e.deltaY });
        return;
      }
      setView(zoomAt(v, ph, Math.exp(-e.deltaY * 0.0016), e.clientX - rect.left, e.clientY - rect.top));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [setView]);

  const local = (e) => {
    const rect = svgRef.current.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };
  const sceneAt = (p) => toScene(latest.current.view, latest.current.pageH, p.x, p.y);

  const alphaAt = (n, u, v) => {
    if (!n.image) return null;
    const { alphaMaps: am, assetBase: ab } = latest.current;
    const url = ab + n.image.file;
    const a = am.alphaAt(url, u, v);
    if (a === null) am.ensure(url, n.w / Math.max(n.h, 1e-6));
    return a;
  };

  const pick = (p, ctxOverride) => {
    const { leaves: ls, idx: ix, view: v, ctx: c } = latest.current;
    const m = sceneAt(p);
    const leaf = hitTest(ls, m.x, m.y, 3 / v.zoom, alphaAt);
    if (!leaf) return null;
    return { leaf, ...resolveTarget(ix, leaf.id, ctxOverride === undefined ? c : ctxOverride) };
  };

  const setImg = (id, box) => {
    const el = imgRefs.current.get(id);
    if (!el) return;
    el.setAttribute("x", box.x);
    el.setAttribute("y", latest.current.pageH - box.y - box.h);
    el.setAttribute("width", box.w);
    el.setAttribute("height", box.h);
  };
  // Re-derive every image's box from the newest scene (after a commit, or to undo a cancelled preview).
  const syncImages = () => latest.current.leaves.forEach((n) => setImg(n.id, n));

  // Snap lines for a drag: page edges/centre + edges/centres of the other objects in the same context.
  const buildTargets = (ids, c) => {
    const { scene: sc, idx: ix } = latest.current;
    const pool = c ? ix.get(c).node.children : sc.layers.filter((l) => l.visible !== false).flatMap((l) => l.children);
    return snapTargets(sc.page, pool.filter((n) => !ids.includes(n.id)));
  };

  const sameParent = (a, b) => (idx.get(a)?.parent?.id ?? idx.get(a)?.layer.id) === (idx.get(b)?.parent?.id ?? idx.get(b)?.layer.id);

  function onPointerDown(e) {
    const svg = svgRef.current;
    if (e.button === 1) {
      e.preventDefault();
      drag.current = { type: "pan", sx: e.clientX, sy: e.clientY, vx: view.x, vy: view.y };
      svg.setPointerCapture(e.pointerId);
      setPanning(true);
      return;
    }
    if (e.button !== 0) return;
    const p = local(e);
    const additive = e.ctrlKey || e.metaKey || e.shiftKey;
    const hit = pick(p);
    svg.setPointerCapture(e.pointerId);
    if (!hit) {
      drag.current = { type: "marquee", startP: p, additive, started: false };
      return;
    }
    const { targetId, ctx: nextCtx } = hit;
    let next;
    if (additive) {
      if (sel.includes(targetId)) next = sel.filter((i) => i !== targetId);
      else if (sel.length && nextCtx === ctx && sameParent(sel[0], targetId)) next = [...sel, targetId];
      else next = [targetId];
    } else {
      next = sel.includes(targetId) ? sel : [targetId];
    }
    onSelect(next, nextCtx);
    const nodes = next.map((id) => idx.get(id)?.node).filter(Boolean);
    const locked = nodes.some((n) => n.locked || idx.get(n.id).layer.locked);
    if (!nodes.length || locked) return;
    drag.current = { type: "move", startP: p, ids: next, nodes, leaves: nodes.flatMap(leavesOf), startBox: unionBox(nodes), targets: snap ? buildTargets(next, nextCtx) : null, started: false };
  }

  function onHandleDown(e, handle) {
    e.stopPropagation();
    if (e.button !== 0 || !selBox || selLocked) return;
    svgRef.current.setPointerCapture(e.pointerId);
    drag.current = { type: "resize", handle, startP: local(e), startBox: selBox, ids: sel.slice(), leaves: selNodes.flatMap(leavesOf), targets: snap ? buildTargets(sel, ctx) : null, started: false };
  }

  function onPointerMove(e) {
    const d = drag.current;
    const p = local(e);
    onCursor(sceneAt(p));
    if (!d) {
      const h = pick(p);
      const id = h ? h.targetId : null;
      setHover((prev) => (prev === id ? prev : id));
      return;
    }
    if (d.type === "pan") {
      setView({ ...view, x: d.vx + (e.clientX - d.sx), y: d.vy + (e.clientY - d.sy) });
      return;
    }
    const dxPx = p.x - d.startP.x;
    const dyPx = p.y - d.startP.y;
    if (!d.started && Math.hypot(dxPx, dyPx) < DRAG_PX) return;
    d.started = true;
    const z = view.zoom;
    if (d.type === "move") {
      let dx = dxPx / z;
      let dy = -dyPx / z;
      if (e.shiftKey) {
        if (Math.abs(dx) > Math.abs(dy)) dy = 0;
        else dx = 0;
      }
      let gx = null;
      let gy = null;
      if (d.targets && !e.altKey) {
        const s = snapMove({ ...d.startBox, x: d.startBox.x + dx, y: d.startBox.y + dy }, d.targets, SNAP_PX / z);
        if (!(e.shiftKey && dx === 0)) { dx += s.dx; gx = s.guideX; }
        if (!(e.shiftKey && dy === 0)) { dy += s.dy; gy = s.guideY; }
      }
      d.dx = dx;
      d.dy = dy;
      d.leaves.forEach((n) => setImg(n.id, { x: n.x + dx, y: n.y + dy, w: n.w, h: n.h }));
      setOverlay({ box: { ...d.startBox, x: d.startBox.x + dx, y: d.startBox.y + dy }, guideX: gx, guideY: gy });
    } else if (d.type === "resize") {
      let box = resizeBox(d.handle, d.startBox, dxPx / z, -dyPx / z, e.shiftKey);
      let gx = null;
      let gy = null;
      // proportional corner drags are not snapped (snapping one edge would break the aspect ratio)
      if (d.targets && !e.altKey && (d.handle.length === 1 || e.shiftKey)) {
        const s = snapResize(box, d.handle, d.targets, SNAP_PX / z);
        if (s.box.w >= MIN_MM && s.box.h >= MIN_MM) { box = s.box; gx = s.guideX; gy = s.guideY; }
      }
      d.newBox = box;
      d.leaves.forEach((n) => setImg(n.id, mapBox(n, d.startBox, box)));
      setOverlay({ box, guideX: gx, guideY: gy });
    } else if (d.type === "marquee") {
      d.p = p;
      setOverlay({ marquee: { x: Math.min(p.x, d.startP.x), y: Math.min(p.y, d.startP.y), w: Math.abs(dxPx), h: Math.abs(dyPx) } });
    }
  }

  function onPointerUp(e) {
    const d = drag.current;
    drag.current = null;
    try {
      svgRef.current.releasePointerCapture(e.pointerId);
    } catch {
      /* not captured */
    }
    setPanning(false);
    if (!d || d.type === "pan") return;
    setOverlay(null);
    if (d.type === "move") {
      if (d.started && (Math.abs(d.dx) > 1e-4 || Math.abs(d.dy) > 1e-4)) {
        onCommit({ op: "move", ids: d.ids, dx: r4(d.dx), dy: r4(d.dy) });
      }
      requestAnimationFrame(syncImages);
    } else if (d.type === "resize") {
      if (d.started && d.newBox) {
        const b = (o) => ({ x: r4(o.x), y: r4(o.y), w: r4(o.w), h: r4(o.h) });
        onCommit({ op: "resize", ids: d.ids, from: b(d.startBox), to: b(d.newBox) });
      }
      requestAnimationFrame(syncImages);
    } else if (d.type === "marquee") {
      if (!d.started) {
        if (!d.additive) onSelect([], null);
        return;
      }
      const a = sceneAt(d.startP);
      const b = sceneAt(d.p);
      const box = { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(a.x - b.x), h: Math.abs(a.y - b.y) };
      const ids = marqueeSelect(scene, ctx, box);
      onSelect(d.additive ? [...new Set([...sel, ...ids])] : ids, ctx);
    }
  }

  function onDoubleClick(e) {
    const hit = pick(local(e));
    if (!hit) return;
    const top = idx.get(hit.targetId).node;
    if (top.kind === "group") {
      const inner = resolveTarget(idx, hit.leaf.id, top.id);
      onSelect([inner.targetId], top.id);
    } else if (top.kind === "powerclip") {
      // Enter the PowerClip: select the child under the cursor (text first). Only its text is
      // editable - it can't be moved or resized separately from the clipped result.
      const child = clipChildAt(top, sceneAt(local(e)));
      if (!child) return onToast("Nothing to select inside this PowerClip at that point.");
      onSelect([child.id], top.id);
      if (child.text) onEditText(child.id);
      else onToast("Inside a PowerClip only text can be edited - this object moves and resizes with the clipped result.");
    } else if (top.text) {
      onEditText(top.id);
    }
  }

  // ------------------------------------------------------------ overlay geometry
  const sx = (mx) => view.x + mx * view.zoom;
  const sy = (my) => view.y + (pageH - my) * view.zoom;
  const sbox = (b) => ({ x: sx(b.x), y: sy(b.y + b.h), w: b.w * view.zoom, h: b.h * view.zoom });
  const liveBox = overlay && overlay.box ? overlay.box : selBox;
  const lb = liveBox ? sbox(liveBox) : null;
  const showHandles = lb && selNodes.length > 0 && !selLocked && !(overlay && overlay.marquee);
  const ctxNode = ctx ? idx.get(ctx)?.node : null;
  const editNode = editingId ? idx.get(editingId)?.node : null;
  const hoverNode = hover && !sel.includes(hover) ? idx.get(hover)?.node : null;

  return (
    <div className="ed-canvas" ref={rootRef}>
      <svg
        ref={svgRef}
        className="ed-svg"
        width={size.w}
        height={size.h}
        style={{ cursor: panning ? "grabbing" : "default" }}
        tabIndex={-1}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={() => {
          if (!drag.current) {
            onCursor(null);
            setHover(null);
          }
        }}
        onDoubleClick={onDoubleClick}
        onMouseDown={(e) => e.button === 1 && e.preventDefault()}
        onAuxClick={(e) => e.preventDefault()}
        onContextMenu={(e) => e.preventDefault()}
      >
        <g transform={`translate(${view.x} ${view.y}) scale(${view.zoom})`}>
          <rect x={4 / view.zoom} y={4 / view.zoom} width={pageW} height={pageH} fill="rgba(0,0,0,0.28)" />
          <rect x="0" y="0" width={pageW} height={pageH} fill="#ffffff" />
          <g style={{ visibility: showRender && scene.page_image ? "hidden" : "visible" }}>
            <SceneImages leaves={leaves} assetBase={assetBase} pageH={pageH} imgRefs={imgRefs} hideId={textPreview && textPreview.id} />
          </g>
          {showRender && scene.page_image && (
            <image href={assetBase + scene.page_image.file} x="0" y="0" width={pageW} height={pageH} preserveAspectRatio="none" style={{ pointerEvents: "none" }} />
          )}
        </g>

        {ctxNode && (() => {
          const b = sbox(ctxNode);
          return (
            <g pointerEvents="none">
              <rect x={b.x} y={b.y} width={b.w} height={b.h} fill="none" stroke="var(--color-red)" strokeDasharray="2 4" opacity="0.6" />
              <text x={b.x + 4} y={b.y - 5} className="ed-ctx-label">Inside group</text>
            </g>
          );
        })()}

        {hoverNode && !overlay && (() => {
          const b = sbox(hoverNode);
          return <rect pointerEvents="none" x={b.x} y={b.y} width={b.w} height={b.h} fill="none" stroke="var(--color-red)" strokeWidth="1" opacity="0.8" />;
        })()}

        {selNodes.length > 1 && !overlay &&
          selNodes.map((n) => {
            const b = sbox(n);
            return <rect key={n.id} pointerEvents="none" x={b.x} y={b.y} width={b.w} height={b.h} fill="none" stroke="var(--color-red)" strokeWidth="1" strokeDasharray="4 3" />;
          })}

        {selNodes.filter((n) => n.stale).map((n) => {
          const b = sbox(n);
          return <rect key={"stale" + n.id} pointerEvents="none" x={b.x} y={b.y} width={b.w} height={b.h} fill="none" stroke="var(--color-warn)" strokeDasharray="5 3" />;
        })}

        {textPreview && selNodes.filter((n) => n.id === textPreview.id && n.text).map((n) => {
          // Live, uncommitted text/font preview: the canvas can't re-typeset CorelDRAW's
          // own render, so while either field is being edited, this replaces the shape's
          // (now hidden - see SceneImages' hideId) image with a real SVG <text> showing the
          // in-progress content in the in-progress font-family. Removed the instant either
          // field blurs (committed or not), never itself an operation. The fallback stack
          // covers every Tamil-capable font this project has ever relied on or measured:
          // Nirmala UI/Nirmala Text ship with Windows and are what the backend engine itself
          // uses (see CLAUDE.md "Shop name replacement"); Noto Sans Tamil is fetched as a web
          // font (editor.css) specifically so this BROWSER preview isn't limited to whatever
          // happens to be installed locally, unlike the backend's real CorelDRAW output;
          // Latha/InaiMathi/Lohit Tamil cover whichever of them a given browser/OS does have.
          // xml:lang="ta" tells the renderer this may be Tamil script, for engines that pick
          // shaping/rendering behavior by declared language rather than font alone.
          const b = sbox(n);
          const previewFont = textPreview.font || n.text.font || "";
          const fontFamily = `"${previewFont}", "Nirmala UI", "Nirmala Text", "Noto Sans Tamil", "Latha", "InaiMathi", "Lohit Tamil", sans-serif`;
          const fontSizePx = Math.max(8, (n.text.size_pt || 24) * view.zoom * (96 / 72) * 0.5);
          const lines = String(textPreview.content ?? "").split(/\r\n|\r|\n/);
          const lineHeight = fontSizePx * 1.2;
          const startY = b.y + b.h / 2 - (lineHeight * (lines.length - 1)) / 2;
          return (
            <g key={"textpreview" + n.id} pointerEvents="none">
              <rect x={b.x} y={b.y} width={b.w} height={b.h} fill="none" stroke="var(--color-red)" strokeDasharray="3 3" opacity="0.6" />
              <text x={b.x + b.w / 2} textAnchor="middle" xmlLang="ta" style={{ fontFamily, fontSize: fontSizePx }}>
                {lines.map((line, i) => (
                  <tspan key={i} x={b.x + b.w / 2} y={startY + i * lineHeight}>{line || " "}</tspan>
                ))}
              </text>
            </g>
          );
        })}

        {lb && selNodes.length > 0 && !(overlay && overlay.marquee) && (
          <g>
            <rect pointerEvents="none" x={lb.x} y={lb.y} width={lb.w} height={lb.h} fill="none" stroke="var(--color-black)" strokeWidth="1" strokeDasharray="5 3" />
            {showHandles &&
              HANDLES.map(([name, fx, fy]) => (
                <rect
                  key={name}
                  className="ed-handle"
                  data-handle={name}
                  x={lb.x + fx * lb.w - HANDLE_PX / 2}
                  y={lb.y + fy * lb.h - HANDLE_PX / 2}
                  width={HANDLE_PX}
                  height={HANDLE_PX}
                  style={{ cursor: CURSORS[name] }}
                  onPointerDown={(e) => onHandleDown(e, name)}
                />
              ))}
          </g>
        )}

        {overlay && overlay.guideX != null && (
          <line pointerEvents="none" x1={sx(overlay.guideX)} x2={sx(overlay.guideX)} y1="0" y2={size.h} stroke="var(--color-red)" strokeWidth="1" strokeDasharray="4 3" />
        )}
        {overlay && overlay.guideY != null && (
          <line pointerEvents="none" y1={sy(overlay.guideY)} y2={sy(overlay.guideY)} x1="0" x2={size.w} stroke="var(--color-red)" strokeWidth="1" strokeDasharray="4 3" />
        )}

        {overlay && overlay.marquee && (
          <rect pointerEvents="none" x={overlay.marquee.x} y={overlay.marquee.y} width={overlay.marquee.w} height={overlay.marquee.h} fill="rgba(224,24,47,0.08)" stroke="var(--color-red)" strokeDasharray="4 3" />
        )}
      </svg>
      {editNode && editNode.text && (
        <TextEditor
          key={editNode.id}
          node={editNode}
          box={sbox(editNode)}
          fonts={fonts}
          onApply={(content) => onTextApply(editNode.id, content)}
          onCancel={onEditEnd}
        />
      )}
    </div>
  );
}
