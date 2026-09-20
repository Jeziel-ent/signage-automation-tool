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
  return { targetId: chain[chain.length - 1].id, ctx: null };
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
  if (!drag || !over || drag.isLayer || dragId === overId) return null;
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
  if (!parentEntry.isLayer && parentEntry.node.kind !== "group") return null;
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
