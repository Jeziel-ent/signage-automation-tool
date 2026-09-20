// Per-image alpha maps for click hit-testing: the browser only knows an <image>'s
// bounding box, but a click on a transparent corner of a curve must fall through
// to whatever is underneath (CorelDRAW selects by fill/outline, not by bbox).
// Each distinct image URL is rasterised once into a small alpha grid.

const MAX_SIDE = 192;

export class AlphaMaps {
  constructor() {
    this.maps = new Map(); // url -> {w, h, alpha: Uint8Array} | null (failed: fall back to bbox)
    this.pending = new Set();
    this.queue = [];
    this.pumping = false;
  }

  ensure(url, aspect) {
    if (this.maps.has(url) || this.pending.has(url)) return;
    this.pending.add(url);
    const img = new Image();
    const done = (map) => {
      this.maps.set(url, map);
      this.pending.delete(url);
    };
    img.onload = () => {
      try {
        const cw = aspect >= 1 ? MAX_SIDE : Math.max(1, Math.round(MAX_SIDE * aspect));
        const ch = aspect >= 1 ? Math.max(1, Math.round(MAX_SIDE / aspect)) : MAX_SIDE;
        const c = document.createElement("canvas");
        c.width = cw;
        c.height = ch;
        const ctx = c.getContext("2d", { willReadFrequently: true });
        ctx.drawImage(img, 0, 0, cw, ch);
        const px = ctx.getImageData(0, 0, cw, ch).data;
        const alpha = new Uint8Array(cw * ch);
        for (let i = 0; i < alpha.length; i++) alpha[i] = px[i * 4 + 3];
        done({ w: cw, h: ch, alpha });
      } catch {
        done(null);
      }
    };
    img.onerror = () => done(null);
    img.src = url;
  }

  /** Warm the cache without blocking the UI: a few images per idle tick. */
  preload(items) {
    this.queue.push(...items);
    if (this.pumping) return;
    this.pumping = true;
    const pump = () => {
      for (let i = 0; i < 12 && this.queue.length; i++) {
        const { url, aspect } = this.queue.shift();
        this.ensure(url, aspect);
      }
      if (this.queue.length) setTimeout(pump, 16);
      else this.pumping = false;
    };
    pump();
  }

  /** 0..255 alpha at (u, v) in [0,1]^2 (v downward), or null if unknown (not loaded / unreadable). */
  alphaAt(url, u, v) {
    const m = this.maps.get(url);
    if (!m) return null;
    const cx = Math.min(m.w - 1, Math.max(0, Math.floor(u * m.w)));
    const cy = Math.min(m.h - 1, Math.max(0, Math.floor(v * m.h)));
    let best = 0;
    for (let dy = -1; dy <= 1; dy++) {
      for (let dx = -1; dx <= 1; dx++) {
        const x = cx + dx;
        const y = cy + dy;
        if (x < 0 || y < 0 || x >= m.w || y >= m.h) continue;
        const a = m.alpha[y * m.w + x];
        if (a > best) best = a;
      }
    }
    return best;
  }
}
