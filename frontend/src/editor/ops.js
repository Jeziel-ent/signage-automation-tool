// Editor scene operations - a line-for-line mirror of backend/app/scene_ops.py
// (read its docstring for the model). Every edit is an entry in an operation
// list; the visible scene is always applyOps(baseScene, ops). Both
// implementations are run against backend/tests/fixtures/ops_golden.json
// (see ops.test.mjs) so they cannot drift apart silently.
//
// product_engine.js (product slots: swap_image / update_product_slot) imports several helpers
// from this file and this file calls back into it from the two ops below - a circular import that
// is safe here because neither module calls the other at module-evaluation time, only from inside
// functions invoked later (mirrors backend/app/scene_ops.py's lazy `from . import product_engine`).
import * as productEngine from "./product_engine.js";

export const MIN_SIZE = 0.001;
const r = (v) => Math.round(Number(v) * 1e4) / 1e4;

export class OpError extends Error {}

export const cloneScene = (s) => structuredClone(s);

// ----------------------------------------------------------------- indexing

export function buildIndex(scene) {
  const idx = new Map();
  const walk = (children, parent, layer) => {
    for (const n of children) {
      idx.set(n.id, { node: n, list: children, parent, layer });
      if (n.children?.length) walk(n.children, n, layer);
    }
  };
  for (const layer of scene.layers) {
    idx.set(layer.id, { node: layer, list: scene.layers, parent: null, layer, isLayer: true });
    walk(layer.children, null, layer);
  }
  return idx;
}

export function findNode(scene, id) {
  const e = buildIndex(scene).get(id);
  return e ? e.node : null;
}

/** Depth-first, bottom -> top, every node (not layers). */
export function* iterNodes(scene) {
  function* walk(children) {
    for (const n of children) {
      yield n;
      if (n.children?.length) yield* walk(n.children);
    }
  }
  for (const layer of scene.layers) yield* walk(layer.children);
}

export function need(idx, id) {
  const e = idx.get(id);
  if (!e) throw new OpError(`unknown id '${id}'`);
  return e;
}

function topIds(idx, ids) {
  if (!ids || !ids.length) throw new OpError("no ids given");
  const seen = new Set(ids);
  const out = [];
  for (const i of new Set(ids)) {
    const e = need(idx, i);
    if (e.isLayer) throw new OpError(`'${i}' is a layer, not an object`);
    let p = e.parent;
    let nested = false;
    while (p) {
      if (seen.has(p.id)) {
        nested = true;
        break;
      }
      p = idx.get(p.id).parent;
    }
    if (!nested) out.push(i);
  }
  return out;
}

// allowPowerclip=true is the narrow exception - only `text`, `move`, `resize`, `order`
// and a same-parent `reorder` pass it, so a PowerClip child can be edited in place (and
// restacked among its siblings) without opening up group/ungroup/delete or moving it
// out of the clip (see scene_ops.py's docstring).
/** True when some ancestor of `id` is a PowerClip. */
function inClip(idx, id) {
  for (let p = idx.get(id).parent; p; p = idx.get(p.id).parent) if (p.kind === "powerclip") return true;
  return false;
}

function checkEditable(idx, id, allowPowerclip = false) {
  const e = idx.get(id);
  if (e.layer.locked) throw new OpError(`'${id}' is on a locked layer`);
  let n = e.node;
  while (n) {
    if (n.locked) throw new OpError(`'${n.id}' is locked`);
    const p = idx.get(n.id).parent;
    if (p?.kind === "powerclip" && !allowPowerclip) throw new OpError(`'${id}' is inside a PowerClip - only text, move, resize and order can be applied to its contents`);
    n = p;
  }
}

/** A child moved/resized inside a PowerClip: the container's rendered image is now out of date. */
function markClipStale(idx, id) {
  for (let p = idx.get(id).parent; p; p = idx.get(p.id).parent) if (p.kind === "powerclip") p.stale = true;
}

function refreshChain(scene, startParent) {
  let p = startParent;
  while (p) {
    const entry = buildIndex(scene).get(p.id);
    if (!entry) return; // already removed as an empty group by an earlier refresh
    const parent = entry.parent;
    if (p.kind === "group") {
      const kids = p.children || [];
      if (!kids.length) {
        entry.list.splice(entry.list.indexOf(p), 1);
      } else {
        const x0 = Math.min(...kids.map((k) => k.x));
        const y0 = Math.min(...kids.map((k) => k.y));
        const x1 = Math.max(...kids.map((k) => k.x + k.w));
        const y1 = Math.max(...kids.map((k) => k.y + k.h));
        p.x = r(x0);
        p.y = r(y0);
        p.w = r(x1 - x0);
        p.h = r(y1 - y0);
      }
    }
    p = parent;
  }
}

// --------------------------------------------------------------------- math

function translate(node, dx, dy) {
  node.x = r(node.x + dx);
  node.y = r(node.y + dy);
  for (const c of node.children || []) translate(c, dx, dy);
}

function scaleSubtree(node, frm, to) {
  const sx = frm.w > MIN_SIZE ? to.w / frm.w : 1;
  const sy = frm.h > MIN_SIZE ? to.h / frm.h : 1;
  const go = (n) => {
    n.x = r(to.x + (n.x - frm.x) * sx);
    n.y = r(to.y + (n.y - frm.y) * sy);
    n.w = r(n.w * sx);
    n.h = r(n.h * sy);
    if (n.text?.size_pt) n.text.size_pt = r(n.text.size_pt * sy);
    for (const c of n.children || []) go(c);
  };
  go(node);
}

// Paragraph formatting a `text` op may also carry - mirrors scene_ops._apply_text_format line for line.
export const TEXT_ALIGNS = ["left", "center", "right", "justify"];
export const LINE_SPACING_RANGE = [10, 1000]; // % of the character height (CorelDRAW default 100)
export const CHAR_SPACING_RANGE = [-100, 2000]; // % of a space's width between characters (CorelDRAW default 0)

function applyTextFormat(t, op) {
  let changed = false;
  if (op.align != null) {
    if (!TEXT_ALIGNS.includes(op.align)) throw new OpError("align must be one of left, center, right, justify");
    t.align = op.align;
    changed = true;
  }
  for (const key of ["bold", "italic", "underline"]) {
    if (op[key] != null) {
      if (typeof op[key] !== "boolean") throw new OpError(`${key} must be true or false`);
      t[key] = op[key];
      changed = true;
    }
  }
  for (const [key, [lo, hi]] of [["line_spacing", LINE_SPACING_RANGE], ["char_spacing", CHAR_SPACING_RANGE]]) {
    if (op[key] != null) {
      const v = Number(op[key]);
      if (!Number.isFinite(v) || typeof op[key] === "boolean") throw new OpError(`${key} must be a number`);
      if (!(v >= lo && v <= hi)) throw new OpError(`${key} must be between ${lo} and ${hi}`);
      t[key] = r(v);
      changed = true;
    }
  }
  return changed;
}

/** Where a box goes when the selection box `frm` is mapped onto `to` (used for live drag previews). */
export function mapBox(box, frm, to) {
  const sx = frm.w > MIN_SIZE ? to.w / frm.w : 1;
  const sy = frm.h > MIN_SIZE ? to.h / frm.h : 1;
  return {
    x: to.x + (box.x - frm.x) * sx,
    y: to.y + (box.y - frm.y) * sy,
    w: box.w * sx,
    h: box.h * sy,
  };
}

export function bboxArg(b, what) {
  const out = {};
  for (const k of ["x", "y", "w", "h"]) {
    const v = b == null ? Number.NaN : Number(b[k]);
    if (!Number.isFinite(v)) throw new OpError(`${what} must be an object with numeric x, y, w, h`);
    out[k] = v;
  }
  return out;
}

const field = (op, name) => {
  if (!(name in op)) throw new OpError(`missing field '${name}'`);
  return op[name];
};

// --------------------------------------------------------------------- ops

const APPLY = {
  move(s, op) {
    const idx = buildIndex(s);
    const ids = topIds(idx, op.ids);
    const dx = Number(field(op, "dx"));
    const dy = Number(field(op, "dy"));
    ids.forEach((i) => checkEditable(idx, i, true));
    const parents = ids.map((i) => idx.get(i).parent);
    ids.forEach((i) => {
      translate(idx.get(i).node, dx, dy);
      markClipStale(idx, i);
    });
    parents.forEach((p) => refreshChain(s, p));
  },

  resize(s, op) {
    const idx = buildIndex(s);
    const ids = topIds(idx, op.ids);
    const frm = bboxArg(op.from, "from");
    const to = bboxArg(op.to, "to");
    if (to.w < MIN_SIZE || to.h < MIN_SIZE) throw new OpError("target width/height must be positive");
    ids.forEach((i) => checkEditable(idx, i, true));
    const parents = ids.map((i) => idx.get(i).parent);
    ids.forEach((i) => {
      scaleSubtree(idx.get(i).node, frm, to);
      markClipStale(idx, i);
    });
    parents.forEach((p) => refreshChain(s, p));
  },

  order(s, op) {
    const idx = buildIndex(s);
    const e = need(idx, field(op, "id"));
    if (e.isLayer) throw new OpError("cannot reorder a layer with 'order'");
    // a PowerClip child is restacked among its own siblings only (never out of the clip), like CorelDRAW's Order menu
    checkEditable(idx, op.id, true);
    const lst = e.list;
    const i = lst.indexOf(e.node);
    if (op.mode === "front") lst.push(lst.splice(i, 1)[0]);
    else if (op.mode === "back") lst.unshift(lst.splice(i, 1)[0]);
    else if (op.mode === "forward") {
      if (i < lst.length - 1) [lst[i], lst[i + 1]] = [lst[i + 1], lst[i]];
    } else if (op.mode === "backward") {
      if (i > 0) [lst[i], lst[i - 1]] = [lst[i - 1], lst[i]];
    } else throw new OpError(`unknown order mode '${op.mode}'`);
    markClipStale(idx, op.id);
  },

  reorder(s, op) {
    const idx = buildIndex(s);
    const e = need(idx, field(op, "id"));
    if (e.isLayer) throw new OpError("layers are reordered with 'layer_order'");
    const dest = need(idx, field(op, "parent"));
    const dn = dest.node;
    // Inside a PowerClip (the object is clipped, or the destination is/sits in a clip) only a restack among the
    // SAME parent's children is allowed - moving anything into or out of a clip changes what is clipped.
    const curParent = e.parent ? e.parent.id : e.layer.id;
    const clipInvolved = inClip(idx, op.id) || (!dest.isLayer && (dn.kind === "powerclip" || inClip(idx, dn.id)));
    const sameParent = op.parent === curParent;
    if (clipInvolved && !sameParent) throw new OpError(`'${op.id}' cannot be moved into or out of a PowerClip - only restacked among its siblings`);
    checkEditable(idx, op.id, sameParent);
    if (!dest.isLayer && dn.kind !== "group" && dn.kind !== "powerclip") throw new OpError(`'${op.parent}' is not a layer or group`);
    if (dest.layer.locked) throw new OpError("destination layer is locked");
    let p = dn;
    while (p && !dest.isLayer) {
      if (p.id === op.id) throw new OpError("cannot move an object into itself");
      p = idx.get(p.id).parent;
    }
    const node = e.node;
    const oldParent = e.parent;
    e.list.splice(e.list.indexOf(node), 1);
    const target = dn.children;
    const index = Math.max(0, Math.min(Math.trunc(Number(field(op, "index"))), target.length));
    target.splice(index, 0, node);
    refreshChain(s, oldParent);
    refreshChain(s, dest.isLayer ? null : dn);
    if (clipInvolved) markClipStale(idx, op.id);
  },

  visibility(s, op) {
    const idx = buildIndex(s);
    need(idx, field(op, "id")).node.visible = Boolean(field(op, "visible"));
  },

  group(s, op) {
    const idx = buildIndex(s);
    const ids = [...new Set(op.ids || [])];
    if (ids.length < 2) throw new OpError("grouping needs at least two objects");
    const gid = field(op, "group_id");
    if (idx.has(gid)) throw new OpError(`id '${gid}' already exists`);
    const entries = ids.map((i) => need(idx, i));
    if (entries.some((e) => e.isLayer)) throw new OpError("cannot group layers");
    const list = entries[0].list;
    if (entries.some((e) => e.list !== list)) throw new OpError("objects must share the same parent to be grouped");
    ids.forEach((i) => checkEditable(idx, i));
    const members = entries.map((e) => e.node).sort((a, b) => list.indexOf(a) - list.indexOf(b));
    const at = Math.max(...members.map((m) => list.indexOf(m)));
    const pos = at - (members.length - 1);
    members.forEach((m) => list.splice(list.indexOf(m), 1));
    const x0 = Math.min(...members.map((m) => m.x));
    const y0 = Math.min(...members.map((m) => m.y));
    const x1 = Math.max(...members.map((m) => m.x + m.w));
    const y1 = Math.max(...members.map((m) => m.y + m.h));
    list.splice(pos, 0, {
      id: gid, kind: "group", type: "group", name: op.name || "Group",
      x: r(x0), y: r(y0), w: r(x1 - x0), h: r(y1 - y0),
      rotation: 0, visible: true, locked: false, image: null, children: members,
    });
    refreshChain(s, entries[0].parent);
  },

  ungroup(s, op) {
    const idx = buildIndex(s);
    const e = need(idx, field(op, "id"));
    if (e.isLayer || e.node.kind !== "group") throw new OpError(`'${op.id}' is not a group`);
    checkEditable(idx, op.id);
    const i = e.list.indexOf(e.node);
    e.list.splice(i, 1, ...e.node.children);
  },

  text(s, op) {
    const idx = buildIndex(s);
    const e = need(idx, field(op, "id"));
    const n = e.node;
    if (!n.text) throw new OpError(`'${op.id}' is not a text object`);
    checkEditable(idx, op.id, true);
    if (op.content != null) {
      n.text.content = String(op.content);
      n.stale = true;
    }
    if (op.font != null) {
      n.text.font = String(op.font);
      n.stale = true;
    }
    if (op.size_pt != null) {
      const size = Number(op.size_pt);
      if (!(size > 0)) throw new OpError("font size must be positive");
      n.text.size_pt = r(size);
      n.stale = true;
    }
    if (applyTextFormat(n.text, op)) n.stale = true;
  },

  delete(s, op) {
    const idx = buildIndex(s);
    const ids = topIds(idx, op.ids);
    ids.forEach((i) => checkEditable(idx, i));
    const parents = ids.map((i) => idx.get(i).parent);
    ids.forEach((i) => {
      const e = idx.get(i);
      e.list.splice(e.list.indexOf(e.node), 1);
    });
    parents.forEach((p) => refreshChain(s, p));
  },

  paste(s, op) {
    const idx = buildIndex(s);
    const dest = need(idx, field(op, "parent"));
    const dn = dest.node;
    if (!dest.isLayer && dn.kind !== "group") throw new OpError(`'${op.parent}' is not a layer or group`);
    if (dest.layer.locked) throw new OpError("destination layer is locked");
    const nodes = op.nodes || [];
    if (!nodes.length) throw new OpError("paste has no nodes");
    const newIds = [];
    const collect = (n) => {
      newIds.push(n.id);
      (n.children || []).forEach(collect);
    };
    nodes.forEach(collect);
    const clash = newIds.filter((i) => idx.has(i));
    if (clash.length || new Set(newIds).size !== newIds.length) {
      throw new OpError(`pasted ids are not unique: ${clash.length ? clash : "duplicates inside paste"}`);
    }
    nodes.forEach((n) => dn.children.push(structuredClone(n)));
    refreshChain(s, dest.isLayer ? null : dn);
  },

  layer_order(s, op) {
    const idx = buildIndex(s);
    const e = need(idx, field(op, "id"));
    if (!e.isLayer) throw new OpError(`'${op.id}' is not a layer`);
    const layers = s.layers;
    layers.splice(layers.indexOf(e.node), 1);
    layers.splice(Math.max(0, Math.min(Math.trunc(Number(field(op, "index"))), layers.length)), 0, e.node);
  },

  page(s, op) {
    const w = Number(field(op, "width"));
    const h = Number(field(op, "height"));
    if (!(w >= MIN_SIZE) || !(h >= MIN_SIZE)) throw new OpError("page size must be positive");
    s.page.width = r(w);
    s.page.height = r(h);
  },

  swap_image(s, op) {
    const idx = buildIndex(s);
    const e = need(idx, field(op, "id"));
    const n = e.node;
    if (!productEngine.isBitmap(n)) throw new OpError(`'${op.id}' is not an image shape`);
    checkEditable(idx, op.id, true);
    const asset = productEngine.checkAsset(op.asset);
    const fit = op.fit || "contain";
    const padding = Number(op.padding || 0);
    const { frame, clipId } = productEngine.resolveFrame(idx, op.id, fit, op.frame ?? null);
    const box = productEngine.aspectFit(asset.w, asset.h, frame, fit, padding);
    n.x = box.x;
    n.y = box.y;
    n.w = box.w;
    n.h = box.h;
    n.image_asset = asset;
    n.fit = fit;
    if (padding) n.padding = padding;
    else delete n.padding;
    if (clipId == null) n.slot_frame = { x: r(frame.x), y: r(frame.y), w: r(frame.w), h: r(frame.h) };
    else delete n.slot_frame;
    n.stale = true;
    markClipStale(idx, op.id);
    refreshChain(s, e.parent);
  },

  update_product_slot(s, op) {
    const idx = buildIndex(s);
    const e = need(idx, field(op, "id"));
    const n = e.node;
    const kind = op.kind;
    if (!productEngine.SLOT_KINDS.includes(kind)) throw new OpError(`unknown product slot kind '${kind}'`);
    if (productEngine.IMAGE_KINDS.includes(kind)) {
      if (op.text != null || op.font != null || op.size_pt != null) {
        throw new OpError(`a ${kind} slot takes 'asset', not 'text'/'font'/'size_pt'`);
      }
      APPLY.swap_image(s, { ...op, op: "swap_image" });
    } else {
      if (!n.text) throw new OpError(`'${op.id}' is not a text object`);
      if (op.asset != null) throw new OpError(`a ${kind} slot takes 'text'/'font'/'size_pt', not 'asset'`);
      checkEditable(idx, op.id, true);
      if (op.text == null && op.font == null && op.size_pt == null) throw new OpError("missing field 'text'");
      if (op.text != null) n.text.content = String(op.text);
      if (op.font != null) n.text.font = String(op.font);
      if (op.size_pt != null) {
        const size = Number(op.size_pt);
        if (!(size > 0)) throw new OpError("font size must be positive");
        n.text.size_pt = r(size);
      }
      n.stale = true;
    }
  },
};

/** Mutates `scene` in place. Throws OpError (scene may be partially changed). */
export function applyOp(scene, op) {
  const fn = APPLY[op?.op];
  if (!fn) throw new OpError(`unknown op '${op?.op}'`);
  fn(scene, op);
}

/** Returns a new scene; the input is never modified. */
export function applyOps(scene, ops) {
  const out = cloneScene(scene);
  ops.forEach((op, n) => {
    try {
      applyOp(out, op);
    } catch (e) {
      if (e instanceof OpError) throw new OpError(`op #${n} (${op.op}): ${e.message}`);
      throw e;
    }
  });
  return out;
}
