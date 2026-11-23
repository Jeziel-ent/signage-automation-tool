// The single-object render (backend POST /api/editor/{job}/{shop}/render): which edited text objects to ask CorelDRAW to draw, and
// which of its answers still apply. Pure helpers, no React.
import { iterNodes } from "./ops.js";

export const MAX_RENDER_IDS = 40;
const MOVED_MM = 0.01;

/** The edits as one comparable string: a render answers exactly one such key, and is dropped when the edits move on. */
export function opsKey(ops) {
  return JSON.stringify(ops || []);
}

/** Ids of the text leaves whose look the editor can only guess: text an op changed (`stale`), or resized/moved off the pristine box. */
export function textsToRender(scene, base) {
  if (!scene) return [];
  const orig = new Map();
  if (base) for (const n of iterNodes(base)) orig.set(n.id, n);
  const out = [];
  for (const n of iterNodes(scene)) {
    if (!n.text || n.visible === false || !n.image || (n.children && n.children.length)) continue;
    const o = orig.get(n.id);
    const changed = n.stale || !o || ["x", "y", "w", "h"].some((k) => Math.abs((n[k] || 0) - (o[k] || 0)) > MOVED_MM);
    if (changed) out.push(n.id);
    if (out.length >= MAX_RENDER_IDS) break;
  }
  return out;
}

/** {id: url} of CorelDRAW's finished renders, or null when they were made for other edits (or there are none). */
export function renderOverrides(state, key, urlBase) {
  if (!state || state.key !== key || !state.files) return null;
  const out = {};
  for (const [id, r] of Object.entries(state.files)) if (r && r.file) out[id] = `${urlBase}${state.exportId}/${r.file}`;
  return Object.keys(out).length ? out : null;
}
