// Board fonts for the editor's live text (editor/LiveText.jsx). Everything else on the canvas is CorelDRAW's own render, so fonts only
// matter for text the browser draws itself - but there they decide whether an edited shop name looks like the board or like a fallback.
//
// For every font the board's text uses: already renderable here (measured, see isFontRenderable) -> nothing to do; else the server's
// own installed copy (`GET /api/fonts/file`, loaded with the FontFace API); else Google Fonts (css2 API, only if Google serves that
// family); else it is reported missing. The pure helpers are unit-tested in fontLoader.test.mjs.

/** Unique font families used by the scene's text objects, in first-seen order. */
export function sceneFonts(scene) {
  const out = [];
  const seen = new Set();
  const walk = (nodes) => {
    for (const n of nodes || []) {
      const f = n.text && typeof n.text.font === "string" ? n.text.font.trim() : "";
      if (f && !seen.has(f.toLowerCase())) {
        seen.add(f.toLowerCase());
        out.push(f);
      }
      walk(n.children);
    }
  };
  for (const layer of (scene && scene.layers) || []) walk(layer.children);
  return out;
}

/** Google Fonts css2 URL for one family (regular + bold, upright + italic). */
export function googleFontsUrl(family) {
  const slug = encodeURIComponent(family.trim()).replace(/%20/g, "+");
  return `https://fonts.googleapis.com/css2?family=${slug}:ital,wght@0,400;0,700;1,400;1,700&display=swap`;
}

/** Server copy of an installed font. */
export const serverFontUrl = (family) => `/api/fonts/file?family=${encodeURIComponent(family)}`;

/** The families that are not renderable, from a status map. */
export const missingFonts = (status) => Object.entries(status).filter(([, s]) => s === "missing").map(([f]) => f);

// ------------------------------------------------------------------ browser side

// Latin + digits + Tamil: a font that only covers one script still differs from the fallback on it.
const PROBE = "mmmmmmmmmmlli WQ@#0123 அஆஇ கசட";
const BASES = ["monospace", "serif", "sans-serif"];
let probeCtx = null;

/**
 * True if the browser can render `family` now (installed locally or already loaded). `document.fonts.check()` can't answer this: it
 * returns true for any family the FontFaceSet doesn't track, installed or not. Measuring text against generic fallbacks does.
 */
export function isFontRenderable(family) {
  try {
    if (!probeCtx) probeCtx = document.createElement("canvas").getContext("2d");
    const quoted = `"${family.replace(/"/g, '\\"')}"`;
    return BASES.some((base) => {
      probeCtx.font = `72px ${base}`;
      const a = probeCtx.measureText(PROBE).width;
      probeCtx.font = `72px ${quoted}, ${base}`;
      return Math.abs(probeCtx.measureText(PROBE).width - a) > 0.5;
    });
  } catch {
    return false;
  }
}

let serverList = null; // promise of a lower-cased Set of the server's installed families, or null if the list is unavailable
function serverFamilies() {
  if (!serverList) {
    serverList = fetch("/api/fonts")
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => (j && j.available ? new Set(j.fonts.map((f) => f.toLowerCase())) : null))
      .catch(() => null);
  }
  return serverList;
}

async function fromServer(family) {
  if (typeof FontFace === "undefined") return false;
  const list = await serverFamilies();
  if (list && !list.has(family.toLowerCase())) return false; // not installed there: skip the request (and its console 404)
  try {
    const face = new FontFace(family, `url("${serverFontUrl(family)}")`);
    await face.load(); // rejects on 404 or a file the browser can't parse
    document.fonts.add(face);
    return true;
  } catch {
    return false;
  }
}

async function fromGoogle(family) {
  const url = googleFontsUrl(family);
  try {
    const r = await fetch(url, { mode: "cors" }); // 400 for a family Google doesn't serve - don't inject a dead stylesheet
    if (!r.ok) return false;
  } catch {
    return false;
  }
  const id = `gf-${family.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`;
  if (!document.getElementById(id)) {
    const link = document.createElement("link");
    link.id = id;
    link.rel = "stylesheet";
    link.href = url;
    const loaded = new Promise((res) => {
      link.onload = res;
      link.onerror = res;
    });
    document.head.appendChild(link);
    await loaded;
  }
  try {
    await document.fonts.load(`16px "${family}"`, PROBE); // the @font-face rules are lazy until something asks for them
  } catch {
    /* reported below by the renderability check */
  }
  return isFontRenderable(family);
}

const pending = new Map(); // family -> promise of its status, so a family is fetched once per page

/** Make `family` renderable if possible. Resolves to "local" | "server" | "google" | "missing". */
export function ensureFont(family) {
  const key = family.toLowerCase();
  if (!pending.has(key)) {
    pending.set(
      key,
      (async () => {
        if (isFontRenderable(family)) return "local";
        if (await fromServer(family)) return "server";
        if (await fromGoogle(family)) return "google";
        return "missing";
      })(),
    );
  }
  return pending.get(key);
}
