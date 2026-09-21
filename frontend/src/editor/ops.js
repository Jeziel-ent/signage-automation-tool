// Editor scene operations - a line-for-line mirror of backend/app/scene_ops.py
// (read its docstring for the model). Every edit is an entry in an operation
// list; the visible scene is always applyOps(baseScene, ops). Both
// implementations are run against backend/tests/fixtures/ops_golden.json
// (see ops.test.mjs) so they cannot drift apart silently.

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
      if (n.children && n.children.length) walk(n.children, n, layer);
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
      if (n.children && n.children.length) yield* walk(n.children);
    }
  }
  for (const layer of scene.layers) yield* walk(layer.children);
}

function need(idx, id) {
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

// allowPowerclip=true is the narrow exception - only `text`, `move` and `resize`
// pass it, so a PowerClip child can be edited in place without opening up
// order/reorder/group/ungroup/delete on it (see scene_ops.py's docstring).
function checkEditable(idx, id, allowPowerclip = false) {
  const e = idx.get(id);
  if (e.layer.locked) throw new OpError(`'${id}' is on a locked layer`);
  let n = e.node;
  while (n) {
    if (n.locked) throw new OpError(`'${n.id}' is locked`);
    const p = idx.get(n.id).parent;
    if (p && p.kind === "powerclip" && !allowPowerclip) throw new OpError(`'${id}' is inside a PowerClip (contents are read-only in v1)`);
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
    if (n.text && n.text.size_pt) n.text.size_pt = r(n.text.size_pt * sy);
    for (const c of n.children || []) go(c);
  };
  go(node);
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

function bboxArg(b, what) {
  const out = {};
  for (const k of ["x", "y", "w", "h"]) {
    const v = b == null ? NaN : Number(b[k]);
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
    checkEditable(idx, op.id);
    const lst = e.list;
    const i = lst.indexOf(e.node);
    if (op.mode === "front") lst.push(lst.splice(i, 1)[0]);
    else if (op.mode === "back") lst.unshift(lst.splice(i, 1)[0]);
    else if (op.mode === "forward") {
      if (i < lst.length - 1) [lst[i], lst[i + 1]] = [lst[i + 1], lst[i]];
    } else if (op.mode === "backward") {
      if (i > 0) [lst[i], lst[i - 1]] = [lst[i - 1], lst[i]];
    } else throw new OpError(`unknown order mode '${op.mode}'`);
  },

  reorder(s, op) {
    const idx = buildIndex(s);
    const e = need(idx, field(op, "id"));
    if (e.isLayer) throw new OpError("layers are reordered with 'layer_order'");
    checkEditable(idx, op.id);
    const dest = need(idx, field(op, "parent"));
    const dn = dest.node;
    if (!dest.isLayer && dn.kind !== "group") throw new OpError(`'${op.parent}' is not a layer or group`);
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
};

/** Mutates `scene` in place. Throws OpError (scene may be partially changed). */
export function applyOp(scene, op) {
  const fn = APPLY[op && op.op];
  if (!fn) throw new OpError(`unknown op '${op && op.op}'`);
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
