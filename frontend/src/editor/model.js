// Pure editor-model helpers (no React, no DOM) - selection resolution, hit
// testing, layer-tree rows and drag-reorder planning. Tested in model.test.mjs.
import { buildIndex } from "./ops.js";

/** Nodes that carry their own rendered image (plain shapes, and a PowerClip as one unit). */
export const isLeaf = (n) => n.kind === "shape" || n.kind === "powerclip";

/** Drawing order (bottom -> top) of every visible leaf; a hidden layer/ancestor hides everything below it. */
export function flattenLeaves(scene) {
  const out = [];
  const walk = (children, visible) => {
    for (const n of children) {
      const v = visible && n.visible !== false;
      if (n.kind === "group") walk(n.children || [], v);
      else if (v) out.push(n);
    }
  };
  for (const layer of scene.layers) walk(layer.children, layer.visible !== false);
  return out;
}

/**
 * A PowerClip whose contents can be drawn live: its frame is an axis-aligned rectangle (so a client-side
 * clip to its box is exact - `frame_rect`, recorded by the scene export) and every visible leaf inside
 * has its own image (scene version >= 2). Anything else keeps the single flat CorelDRAW render.
 */
export function livePowerclip(n) {
  if (!n || n.kind !== "powerclip" || n.frame_rect !== true || !(n.children || []).length) return false;
  const ok = (children) =>
    children.every((c) => {
      if (c.visible === false) return true;
      if (c.kind === "group") return ok(c.children || []);
      if (c.kind === "powerclip") return livePowerclip(c) || !!c.image;
      return !!c.image;
    });
  return ok(n.children);
}

/**
 * Drawing plan, bottom -> top: {leaf: node} for a plain image, or {clip: container, items: [...]} for a
 * live PowerClip (its contents drawn inside a clip to the frame, replacing the flat image). Hidden
 * layers/ancestors are skipped like flattenLeaves.
 */
export function renderItems(scene) {
  const walk = (children, visible) => {
    const out = [];
    for (const n of children) {
      const v = visible && n.visible !== false;
      if (!v) continue;
      if (n.kind === "group") out.push(...walk(n.children || [], v));
      else if (livePowerclip(n)) out.push({ clip: n, items: walk(n.children, v) });
      else out.push({ leaf: n });
    }
    return out;
  };
  return scene.layers.flatMap((l) => walk(l.children, l.visible !== false));
}

/** Every drawn image node in a plan (live-clip contents included), plus the live containers themselves. */
export function planNodes(items) {
  return items.flatMap((it) => (it.leaf ? [it.leaf] : [it.clip, ...planNodes(it.items)]));
}

/** Leaf images under `node`, through groups and live PowerClips (not the containers themselves). */
export function contentLeaves(node) {
  if (node.kind === "group" || livePowerclip(node)) return (node.children || []).filter((c) => c.visible !== false).flatMap(contentLeaves);
  return [node];
}

/**
 * What to update while `node` is dragged: its leaf images, and for a live PowerClip also the container
 * itself (its clip rectangle) plus the contents, which are all mapped with the same transform.
 */
export function dragTargets(node) {
  if (node.kind === "group") return (node.children || []).flatMap(dragTargets);
  if (livePowerclip(node)) return [node, ...(node.children || []).flatMap(dragTargets)];
  return [node];
}

/** Leaves under `node` (the node itself if it is one). */
export function leavesOf(node) {
  if (node.kind === "group") return (node.children || []).flatMap(leavesOf);
  return [node];
}

/** [node, parent, grandparent, ...] up to (excluding) the layer. */
export function ancestry(idx, id) {
  const out = [];
  let e = idx.get(id);
  while (e && !e.isLayer) {
    out.push(e.node);
    e = e.parent ? idx.get(e.parent.id) : null;
  }
  return out;
}

/**
 * A click landed on `leafId`; which object does it select given the current
 * context (the group being edited, or null for the top level)? Returns the
 * child of `ctx` that contains the leaf - or the top-level object (with ctx
 * reset to null) when the leaf is outside the context.
 */
export function resolveTarget(idx, leafId, ctx) {
  const chain = ancestry(idx, leafId); // leaf ... top
  if (ctx) {
    const at = chain.findIndex((n) => n.id === ctx);
    if (at > 0) return { targetId: chain[at - 1].id, ctx };
  }
  return { targetId: chain.at(-1).id, ctx: null };
}

export function unionBox(nodes) {
  if (!nodes.length) return null;
  const x0 = Math.min(...nodes.map((n) => n.x));
  const y0 = Math.min(...nodes.map((n) => n.y));
  const x1 = Math.max(...nodes.map((n) => n.x + n.w));
  const y1 = Math.max(...nodes.map((n) => n.y + n.h));
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/**
 * Topmost visible, unlocked leaf under point (mx, my) in mm. `leaves` is in
 * draw order (bottom -> top). `alphaAt(node, u, v)` returns 0..255 or null when
 * the shape's pixels are not loaded yet (treated as a bbox hit).
 * `tol` (mm) widens each box so thin strokes stay clickable.
 */
export function hitTest(leaves, mx, my, tol, alphaAt) {
  for (let i = leaves.length - 1; i >= 0; i--) {
    const n = leaves[i];
    if (n.locked) continue;
    if (mx < n.x - tol || mx > n.x + n.w + tol || my < n.y - tol || my > n.y + n.h + tol) continue;
    const u = n.w > 0 ? (mx - n.x) / n.w : 0.5;
    const v = n.h > 0 ? 1 - (my - n.y) / n.h : 0.5; // image v grows downward, scene y grows upward
    const a = alphaAt(n, u, v, tol);
    if (a === null || a >= 16) return n;
  }
  return null;
}

/** Top-level-in-context objects fully inside a marquee box. */
export function marqueeSelect(scene, ctx, box) {
  const idx = buildIndex(scene);
  const pool = ctx ? idx.get(ctx).node.children : scene.layers.filter((l) => l.visible !== false && !l.locked).flatMap((l) => l.children);
  return pool
    .filter((n) => n.visible !== false && !n.locked)
    .filter((n) => n.x >= box.x && n.y >= box.y && n.x + n.w <= box.x + box.w && n.y + n.h <= box.y + box.h)
    .map((n) => n.id);
}

// ------------------------------------------------------------- layers tree

/** Flat rows for the layers panel, top of the stack first (Corel's Object Manager order). */
/** True when `id` sits anywhere inside a PowerClip (contents there accept text/move/resize/order and a same-parent reorder - see ops.js checkEditable). */
export function insidePowerclip(idx, id) {
  const e = idx.get(id);
  for (let p = e && e.parent; p; p = idx.get(p.id).parent) if (p.kind === "powerclip") return true;
  return false;
}

/** Topmost descendant of a PowerClip whose box contains the scene point (mm), preferring text; null if none. */
export function clipChildAt(powerclip, pt) {
  const hits = [];
  const walk = (children) => {
    for (let i = children.length - 1; i >= 0; i--) {
      const c = children[i];
      if (c.visible === false) continue;
      if (c.children && c.children.length) walk(c.children);
      else if (pt.x >= c.x && pt.x <= c.x + c.w && pt.y >= c.y && pt.y <= c.y + c.h) hits.push(c);
    }
  };
  walk(powerclip.children || []);
  return hits.find((c) => c.text) || hits[0] || null;
}

export function buildRows(scene, expanded) {
  const rows = [];
  const walk = (children, depth, layerId, parentLocked) => {
    for (let i = children.length - 1; i >= 0; i--) {
      const n = children[i];
      const has = !!(n.children && n.children.length);
      rows.push({ id: n.id, node: n, depth, layerId, isLayer: false, hasChildren: has, locked: parentLocked || !!n.locked });
      if (has && expanded.has(n.id)) walk(n.children, depth + 1, layerId, parentLocked || !!n.locked);
    }
  };
  for (let i = scene.layers.length - 1; i >= 0; i--) {
    const layer = scene.layers[i];
    const has = layer.children.length > 0;
    rows.push({ id: layer.id, node: layer, depth: 0, layerId: layer.id, isLayer: true, hasChildren: has, locked: !!layer.locked });
    if (has && expanded.has(layer.id)) walk(layer.children, 1, layer.id, !!layer.locked);
  }
  return rows;
}

/**
 * Turns "drop dragId above/below/into overId" into a `reorder` op (or null when
 * the drop is not allowed). The op's index is into the destination child list
 * AFTER the dragged node has been removed from it, bottom -> top - the
 * semantics scene_ops.py uses.
 */
export function planDrop(scene, dragId, overId, zone) {
  const idx = buildIndex(scene);
  const drag = idx.get(dragId);
  const over = idx.get(overId);
  if (!drag || !over || dragId === overId) return null;
  if (drag.isLayer) {
    // a layer can only be dropped above/below another layer (rows are top-first, the list is bottom-first)
    if (!over.isLayer || zone === "into") return null;
    const others = scene.layers.filter((l) => l.id !== dragId);
    const j = others.findIndex((l) => l.id === overId);
    return { op: "layer_order", id: dragId, index: zone === "above" ? j + 1 : j };
  }
  if (drag.node.locked || drag.layer.locked) return null;

  let parentEntry;
  let index;
  if (zone === "into") {
    if (!over.isLayer && over.node.kind !== "group") return null;
    parentEntry = over;
    const kids = over.node.children.filter((c) => c.id !== dragId);
    index = kids.length; // top of the container
  } else {
    if (over.isLayer) return null; // layers are not reordered in v1
    parentEntry = over.parent ? idx.get(over.parent.id) : idx.get(over.layer.id);
    const kids = parentEntry.node.children.filter((c) => c.id !== dragId);
    const j = kids.findIndex((c) => c.id === overId);
    index = zone === "above" ? j + 1 : j; // "above" in the panel = higher in the stack
  }
  if (parentEntry.layer.locked) return null;
  // PowerClip contents: a row can only be restacked among its own siblings (above/below a sibling), never dragged
  // into or out of a clip - the same rule as ops.js `reorder`.
  const curParentId = drag.parent ? drag.parent.id : drag.layer.id;
  const destInClip = !parentEntry.isLayer && (parentEntry.node.kind === "powerclip" || insidePowerclip(idx, parentEntry.node.id));
  if ((insidePowerclip(idx, dragId) || destInClip) && parentEntry.node.id !== curParentId) return null;
  if (!parentEntry.isLayer && parentEntry.node.kind !== "group" && parentEntry.node.kind !== "powerclip") return null;
  // not into itself / own descendant
  let p = parentEntry;
  while (p && !p.isLayer) {
    if (p.node.id === dragId) return null;
    p = p.parent ? idx.get(p.parent.id) : null;
  }
  return { op: "reorder", id: dragId, parent: parentEntry.node.id, index };
}

/** Deep copy with fresh ids; each copy remembers what it was copied from. */
export function cloneWithNewIds(node, nextId) {
  const c = structuredClone(node);
  const go = (n) => {
    n.src = n.src || n.id;
    n.id = nextId();
    (n.children || []).forEach(go);
  };
  go(c);
  return c;
}

export function shiftNode(node, dx, dy) {
  node.x += dx;
  node.y += dy;
  (node.children || []).forEach((c) => shiftNode(c, dx, dy));
}

// ---------------------------------------------------------------- snapping

/**
 * Snap lines: the page edges and centre, plus the edges and centre of every
 * object in `nodes` (callers pass the other objects in the current context,
 * excluding whatever is being dragged).
 */
export function snapTargets(page, nodes) {
  const xs = [0, page.width / 2, page.width];
  const ys = [0, page.height / 2, page.height];
  for (const n of nodes) {
    if (n.visible === false) continue;
    xs.push(n.x, n.x + n.w / 2, n.x + n.w);
    ys.push(n.y, n.y + n.h / 2, n.y + n.h);
  }
  return { xs, ys };
}

/** Smallest correction (in mm) that puts one of `values` on one of `lines`, or null if none is within `tol`. */
export function nearest(values, lines, tol) {
  let best = null;
  for (const v of values) {
    for (const l of lines) {
      const d = l - v;
      if (Math.abs(d) <= tol && (best === null || Math.abs(d) < Math.abs(best.d))) best = { d, line: l };
    }
  }
  return best;
}

/** Snap a box being moved: its left/centre/right and bottom/middle/top against the targets. */
export function snapMove(box, targets, tol) {
  const sx = nearest([box.x, box.x + box.w / 2, box.x + box.w], targets.xs, tol);
  const sy = nearest([box.y, box.y + box.h / 2, box.y + box.h], targets.ys, tol);
  return { dx: sx ? sx.d : 0, dy: sy ? sy.d : 0, guideX: sx ? sx.line : null, guideY: sy ? sy.line : null };
}

/**
 * Snap only the edges a resize handle is moving. Returns the adjusted box; edges
 * that are not being dragged never move, so the opposite side stays put.
 */
export function snapResize(box, handle, targets, tol) {
  let { x, y, w, h } = box;
  let guideX = null;
  let guideY = null;
  if (handle.includes("e")) {
    const s = nearest([x + w], targets.xs, tol);
    if (s) { w += s.d; guideX = s.line; }
  } else if (handle.includes("w")) {
    const s = nearest([x], targets.xs, tol);
    if (s) { x += s.d; w -= s.d; guideX = s.line; }
  }
  if (handle.includes("n")) {
    const s = nearest([y + h], targets.ys, tol);
    if (s) { h += s.d; guideY = s.line; }
  } else if (handle.includes("s")) {
    const s = nearest([y], targets.ys, tol);
    if (s) { y += s.d; h -= s.d; guideY = s.line; }
  }
  return { box: { x, y, w, h }, guideX, guideY };
}
