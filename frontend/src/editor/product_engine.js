// Product slots - a mirror of backend/app/product_engine.py (read its docstring for the model:
// what a slot is, how tags/heuristics find one, and the image-fit geometry). Both are run against
// the shared golden fixtures (see ops.test.mjs) so they cannot drift apart silently.
import { OpError, MIN_SIZE, bboxArg, buildIndex, iterNodes, need } from "./ops.js";

export const SLOT_PRODUCT_IMAGE = "product_image";
export const SLOT_BRAND_TITLE = "brand_title";
export const SLOT_PRODUCT_TITLE = "product_title";
export const SLOT_ADDRESS = "address";
export const SLOT_CONTACT = "contact";

export const IMAGE_KINDS = [SLOT_PRODUCT_IMAGE];
export const TEXT_KINDS = [SLOT_BRAND_TITLE, SLOT_PRODUCT_TITLE, SLOT_ADDRESS, SLOT_CONTACT];
export const SLOT_KINDS = [...IMAGE_KINDS, ...TEXT_KINDS];

const TAGS = {
  [SLOT_PRODUCT_IMAGE]: ["product_image", "product_img", "productimage"],
  [SLOT_BRAND_TITLE]: ["brand_title", "brandtitle"],
  [SLOT_PRODUCT_TITLE]: ["product_title", "producttitle"],
  [SLOT_ADDRESS]: ["address"],
  [SLOT_CONTACT]: ["contact"],
};

const FITS = ["contain", "cover"];
const BG_AREA_RATIO = 0.9; // a bitmap covering this much of the page is a background, not a product photo
const FRAME_TOL_MM = 0.5;
const CONTACT_RE = /phone\s*no|gst\s*no/i;

const r = (v) => Math.round(Number(v) * 1e4) / 1e4;

export function kindFromName(name) {
  const n = (name || "").trim().toLowerCase();
  for (const [kind, prefixes] of Object.entries(TAGS)) {
    if (prefixes.some((p) => n.startsWith(p))) return kind;
  }
  return null;
}

export function isBitmap(node) {
  return node.kind === "shape" && node.type === "bitmap";
}

// -------------------------------------------------------------------- geometry

export function checkAsset(asset) {
  const name = asset && asset.name != null ? String(asset.name).trim() : "";
  const w = asset ? Number(asset.w) : NaN;
  const h = asset ? Number(asset.h) : NaN;
  if (!name || !(w > 0) || !(h > 0)) throw new OpError("asset must be an object with a name and positive numeric w, h (pixels)");
  return { name, w, h };
}

/** Box (mm) for an asset of assetW x assetH (any unit - only the ratio counts) inside `frame`,
 * centred; "contain" = whole image visible, "cover" = frame completely filled (overflow is clipped
 * by the PowerClip). `padding` (mm) insets the frame on every side first. */
export function aspectFit(assetW, assetH, frame, fit = "contain", padding = 0) {
  if (!FITS.includes(fit)) throw new OpError("fit must be 'contain' or 'cover'");
  if (!(assetW > 0 && assetH > 0)) throw new OpError("asset must be an object with a name and positive numeric w, h (pixels)");
  if (!(padding >= 0)) throw new OpError("padding must be zero or positive");
  const fx = frame.x + padding;
  const fy = frame.y + padding;
  const fw = frame.w - 2 * padding;
  const fh = frame.h - 2 * padding;
  if (fw < MIN_SIZE || fh < MIN_SIZE) throw new OpError("padding leaves no room inside the frame");
  const scale = fit === "contain" ? Math.min(fw / assetW, fh / assetH) : Math.max(fw / assetW, fh / assetH);
  const w = assetW * scale;
  const h = assetH * scale;
  return { x: r(fx + (fw - w) / 2), y: r(fy + (fh - h) / 2), w: r(w), h: r(h) };
}

const box = (node) => ({ x: node.x, y: node.y, w: node.w, h: node.h });

/** Nearest PowerClip container above `nodeId`, or null. */
export function clipAncestor(idx, nodeId) {
  let p = idx.get(nodeId).parent;
  while (p) {
    if (p.kind === "powerclip") return p;
    p = idx.get(p.id).parent;
  }
  return null;
}

/** {frame, clipId} an image is fitted into. Inside a PowerClip the frame is always the container's
 * box - an `explicit` frame that disagrees is refused rather than silently ignored. Otherwise:
 * `explicit`, else the frame remembered from an earlier swap, else the node's own box. "cover" needs
 * a PowerClip. */
export function resolveFrame(idx, nodeId, fit = "contain", explicit = null) {
  const node = idx.get(nodeId).node;
  const clip = clipAncestor(idx, nodeId);
  let given = null;
  if (explicit != null) {
    given = bboxArg(explicit, "frame");
    if (given.w < MIN_SIZE || given.h < MIN_SIZE) throw new OpError("frame width/height must be positive");
  }
  if (clip) {
    const frame = box(clip);
    if (given && ["x", "y", "w", "h"].some((k) => Math.abs(given[k] - frame[k]) > FRAME_TOL_MM)) {
      throw new OpError("frame differs from the PowerClip frame the image is inside");
    }
    return { frame, clipId: clip.id };
  }
  if (fit === "cover") throw new OpError("'cover' needs the image to be inside a PowerClip so the overflow is clipped");
  const frame = given || (node.slot_frame ? { ...node.slot_frame } : box(node));
  return { frame, clipId: null };
}

// ------------------------------------------------------------------ operations

/** A self-contained `swap_image` op: the resolved frame is embedded for a top-level image (so
 * replaying the op later does not depend on what the node looked like then); inside a PowerClip the
 * container is the frame and none is stored. */
export function swapImageOp(scene, nodeId, asset, fit = "contain", padding = 0) {
  const idx = buildIndex(scene);
  need(idx, nodeId);
  const { frame, clipId } = resolveFrame(idx, nodeId, fit);
  const op = { op: "swap_image", id: nodeId, asset: checkAsset(asset), fit };
  if (padding) op.padding = padding;
  if (clipId == null) op.frame = { x: r(frame.x), y: r(frame.y), w: r(frame.w), h: r(frame.h) };
  return op;
}

/** What a swap would do, without applying it: {id, frame, containerId, box}. */
export function planImageSwap(scene, nodeId, asset, fit = "contain", padding = 0) {
  const idx = buildIndex(scene);
  need(idx, nodeId);
  const a = checkAsset(asset);
  const { frame, clipId } = resolveFrame(idx, nodeId, fit);
  return { id: nodeId, frame, containerId: clipId, box: aspectFit(a.w, a.h, frame, fit, padding) };
}

// -------------------------------------------------------------------- registry

function visibleChain(idx, nodeId) {
  const e = idx.get(nodeId);
  if (e.layer.visible === false) return false;
  let n = e.node;
  while (n) {
    if (n.visible === false) return false;
    n = idx.get(n.id).parent;
  }
  return true;
}

function* descendants(node) {
  for (const c of node.children || []) {
    yield c;
    yield* descendants(c);
  }
}

/** Maps a scene's shapes to product slots, in drawing order (bottom -> top).
 * Returns {slots, warnings}; a warning is a tagged shape that cannot be a slot. */
export function mapSlots(scene) {
  const idx = buildIndex(scene);
  const pageArea = Number(scene.page.width) * Number(scene.page.height);
  const slots = [];
  const warnings = [];
  const claimed = new Set();

  const imageSlot = (target, source, name) => {
    if (claimed.has(target.id)) return;
    claimed.add(target.id);
    let frame = null;
    let clipId = null;
    try {
      ({ frame, clipId } = resolveFrame(idx, target.id));
    } catch {
      /* no usable frame yet - slot is still reported, just without one */
    }
    slots.push({
      slotId: `${SLOT_PRODUCT_IMAGE}:${target.id}`, kind: SLOT_PRODUCT_IMAGE, nodeId: target.id,
      containerId: clipId, frame: frame ? { x: r(frame.x), y: r(frame.y), w: r(frame.w), h: r(frame.h) } : null,
      source, name,
    });
  };

  const textSlot = (node, kind, source) => {
    if (claimed.has(node.id)) return;
    claimed.add(node.id);
    slots.push({ slotId: `${kind}:${node.id}`, kind, nodeId: node.id, containerId: null, frame: null, source, name: node.name || "" });
  };

  const nodes = [...iterNodes(scene)];

  // 1. tags win over heuristics
  for (const n of nodes) {
    const kind = kindFromName(n.name);
    if (kind == null) continue;
    if (IMAGE_KINDS.includes(kind)) {
      if (isBitmap(n)) {
        imageSlot(n, "tag", n.name || "");
      } else if (n.kind === "powerclip" || n.kind === "group") {
        const bitmaps = [...descendants(n)].filter((c) => isBitmap(c) && c.visible !== false);
        if (!bitmaps.length) {
          warnings.push(`${n.id} is tagged ${kind} but holds no bitmap to swap`);
          continue;
        }
        if (bitmaps.length > 1) warnings.push(`${n.id} is tagged ${kind} and holds ${bitmaps.length} bitmaps - using the largest`);
        const target = bitmaps.reduce((a, b) => (a.w * a.h >= b.w * b.h ? a : b));
        imageSlot(target, "tag", n.name || "");
      } else {
        warnings.push(`${n.id} is tagged ${kind} but is a ${n.type}, not a bitmap`);
      }
    } else if (n.text) {
      textSlot(n, kind, "tag");
    } else {
      warnings.push(`${n.id} is tagged ${kind} but is not a text shape`);
    }
  }

  // 2. heuristics for what is still unclaimed
  for (const n of nodes) {
    if (claimed.has(n.id) || n.visible === false || !visibleChain(idx, n.id)) continue;
    if (isBitmap(n) && kindFromName(n.name) == null) {
      if (pageArea > 0 && n.w * n.h >= BG_AREA_RATIO * pageArea) continue; // a page-sized photo is the background
      imageSlot(n, "heuristic", n.name || "");
    } else if (n.text && CONTACT_RE.test(String(n.text.content || "")) && kindFromName(n.name) == null) {
      textSlot(n, SLOT_CONTACT, "heuristic");
    }
  }

  for (const s of slots) {
    if (IMAGE_KINDS.includes(s.kind) && s.containerId && idx.get(s.containerId).node.frame_rect === false) {
      warnings.push(`${s.slotId}: its PowerClip frame is not a plain rectangle - images are fitted to the frame's bounding box`);
    }
  }
  const order = new Map(nodes.map((n, i) => [n.id, i]));
  slots.sort((a, b) => order.get(a.nodeId) - order.get(b.nodeId));
  return { slots, warnings };
}

export function slotForNode(scene, nodeId) {
  const { slots } = mapSlots(scene);
  return slots.find((s) => s.nodeId === nodeId) || null;
}

/** An `update_product_slot` op for a slot found by mapSlots: an image slot takes `asset`, the text
 * slots take `text`. */
export function updateSlotOp(scene, slotId, { asset = null, text = null, fit = "contain", padding = 0 } = {}) {
  const { slots } = mapSlots(scene);
  const slot = slots.find((s) => s.slotId === slotId);
  if (!slot) throw new OpError(`unknown product slot '${slotId}'`);
  const op = { op: "update_product_slot", id: slot.nodeId, kind: slot.kind };
  if (IMAGE_KINDS.includes(slot.kind)) {
    if (asset == null || text != null) throw new OpError(`a ${slot.kind} slot takes 'asset', not 'text'`);
    const swap = swapImageOp(scene, slot.nodeId, asset, fit, padding);
    for (const [k, v] of Object.entries(swap)) if (k !== "op" && k !== "id") op[k] = v;
  } else {
    if (text == null || asset != null) throw new OpError(`a ${slot.kind} slot takes 'text', not 'asset'`);
    op.text = String(text);
  }
  return op;
}
