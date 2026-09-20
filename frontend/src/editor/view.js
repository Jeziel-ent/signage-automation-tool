// Viewport math. A view is {zoom, x, y}: `zoom` is screen px per mm and (x, y)
// is where the page's top-left corner sits on screen. Scene y grows upward, screen y downward.

export const MIN_ZOOM = 0.004;
export const MAX_ZOOM = 80;

export const clampZoom = (z) => Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, z));

export const toScreen = (view, pageH, mx, my) => ({ x: view.x + mx * view.zoom, y: view.y + (pageH - my) * view.zoom });
export const toScene = (view, pageH, sx, sy) => ({ x: (sx - view.x) / view.zoom, y: pageH - (sy - view.y) / view.zoom });

export function fitView(size, page, pad = 56) {
  const zoom = clampZoom(Math.min((size.w - 2 * pad) / page.width, (size.h - 2 * pad) / page.height));
  return { zoom, x: (size.w - page.width * zoom) / 2, y: (size.h - page.height * zoom) / 2 };
}

/** Zoom by `factor`, keeping the scene point under screen point (px, py) fixed. */
export function zoomAt(view, pageH, factor, px, py) {
  const zoom = clampZoom(view.zoom * factor);
  const p = toScene(view, pageH, px, py);
  return { zoom, x: px - p.x * zoom, y: py - (pageH - p.y) * zoom };
}
