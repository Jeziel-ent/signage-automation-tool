// Real-time editor load tracking (pure helpers; unit-tested in loadStages.test.mjs).
//   0-35   BUILD      the master's data: the scene request / CorelDRAW rendering every object (server progress)
//   35-75  TRANSFORM  applying the shop's saved edits and loading every object's image (real per-image progress)
//   75-100 PAINT      mounting the workspace and painting its first frame
export const STAGES = ["BUILD", "TRANSFORM", "PAINT"];

const clamp = (v, lo = 0, hi = 100) => Math.min(hi, Math.max(lo, v));

export function stageOf(progress) {
  return progress < 35 ? "BUILD" : progress < 75 ? "TRANSFORM" : "PAINT";
}

/** The 3D narrative shown for a progress value: hammer 0-39, panel/grid 40-79, paint 80-100. */
export function sceneOf(progress) {
  return progress < 40 ? "hammer" : progress < 80 ? "panel" : "paint";
}

/** Server scene-build progress (0-100) -> the BUILD band (0-35). */
export const mapBuild = (serverPct) => Math.round(clamp(serverPct) * 0.35);

/** Loaded / total object images -> the TRANSFORM band (35-75). */
export const mapPreload = (loaded, total) => (total > 0 ? 35 + clamp(loaded / total, 0, 1) * 40 : 75);

/** Every image file a scene draws (leaf/PowerClip images and the full-page reference), de-duplicated, as `assetBase + file` URLs. */
export function collectImageUrls(scene, assetBase) {
  const files = new Set();
  const walk = (n) => {
    if (n && n.image && n.image.file) files.add(n.image.file);
    (n && n.children ? n.children : []).forEach(walk);
  };
  (scene.layers || []).forEach((l) => (l.children || []).forEach(walk));
  if (scene.page_image && scene.page_image.file) files.add(scene.page_image.file);
  return [...files].map((f) => assetBase + f);
}

/** Preload `urls` in the browser, calling onProgress(loaded, total) as each settles (a failed image counts - the canvas shows what it can).
 *  `timeoutMs` caps the whole thing so one stuck request can never hold the loader. `Img` is injectable for tests. */
export function preloadImages(urls, onProgress, { timeoutMs = 20000, Img = typeof Image !== "undefined" ? Image : null } = {}) {
  const total = urls.length;
  if (!total || !Img) {
    onProgress?.(total, total);
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    let loaded = 0;
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(finish, timeoutMs);
    urls.forEach((u) => {
      const img = new Img();
      const settle = () => {
        loaded += 1;
        onProgress?.(loaded, total);
        if (loaded >= total) finish();
      };
      img.onload = settle;
      img.onerror = settle;
      img.src = u;
    });
  });
}
