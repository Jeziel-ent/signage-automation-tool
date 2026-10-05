// Board fonts for the editor's live text (editor/LiveText.jsx). Everything else on the canvas is CorelDRAW's own render, so fonts only
// matter for text the browser draws itself - but there they decide whether an edited shop name looks like the board or like a fallback.
//
// For every font the board's text uses, the search comes first - the "Missing Font Detected" popup only for what no source has:
//   1. already renderable here (measured, see isFontRenderable)                                   -> "local"
//   2. the server's own installed copy (`GET /api/fonts/file`, FontFace API)                       -> "server"
//   3. Google Fonts by the exact family name (css2 API, only if Google serves it)                 -> "google"
//   4. Google Fonts, then Fontsource, by the name's variants: the exact name and its BASE family with the weight its trailing
//      words mean ("AvantGarde-Demi" -> "Avant Garde" 600, "Copperplate Gothic Bold" -> "Copperplate Gothic" 700), registered
//      under the ORIGINAL name so the live text finds it                                          -> "google" / "fontsource"
//   5. nothing found                                                                               -> "missing"
// Web sources (3-4) fix the editor's live text only: exports are written by CorelDRAW on the server, which does not have those
// fonts. Fontsource (api.fontsource.org + cdn.jsdelivr.net) serves open-licensed fonts only. The pure helpers are unit-tested in
// fontLoader.test.mjs.

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
  for (const layer of (scene?.layers) || []) walk(layer.children);
  return out;
}

/** Google Fonts css2 URL for one family (regular + bold, upright + italic). */
export function googleFontsUrl(family) {
  const slug = encodeURIComponent(family.trim()).replaceAll("%20", "+");
  return `https://fonts.googleapis.com/css2?family=${slug}:ital,wght@0,400;0,700;1,400;1,700&display=swap`;
}

/** Server copy of an installed font. */
export const serverFontUrl = (family) => `/api/fonts/file?family=${encodeURIComponent(family)}`;

// ------------------------------------------------------------------ name variants (pure)

const WEIGHT_WORDS = {
  thin: 100, hairline: 100, extralight: 200, ultralight: 200, light: 300, regular: 400, normal: 400, book: 400, roman: 400,
  medium: 500, demi: 600, demibold: 600, semibold: 600, bold: 700, extrabold: 800, ultrabold: 800, heavy: 900, black: 900,
};
const STYLE_WORDS = { italic: "italic", oblique: "italic", it: "italic" };

/**
 * What to look `name` up as on a web registry: the name itself, then its base family with the weight/style its trailing words
 * mean ("AvantGarde-Demi" -> AvantGarde 600, then "Avant Garde" 600 with the camel case split). [{family, weight, style}].
 */
export function fontCandidates(name) {
  const full = (name || "").trim();
  if (!full) return [];
  const out = [{ family: full, weight: 400, style: "normal" }];
  const tokens = full.split(/[\s_-]+/).filter(Boolean);
  let weight = 400;
  let style = "normal";
  let weightWord = "";
  while (tokens.length > 1) {
    const t = tokens[tokens.length - 1].toLowerCase().replace(/[^a-z]/g, "");
    if (t in WEIGHT_WORDS) {
      if (!weightWord) {
        weight = WEIGHT_WORDS[t];
        weightWord = t;
      }
    } else if (t in STYLE_WORDS) style = STYLE_WORDS[t];
    else if (t === "semi" || t === "extra" || t === "ultra") {
      if (`${t}${weightWord}` in WEIGHT_WORDS) weight = WEIGHT_WORDS[`${t}${weightWord}`]; // "Semi Bold" = semibold
    } else break;
    tokens.pop();
  }
  const add = (family) => {
    if (family && !out.some((c) => c.family.toLowerCase() === family.toLowerCase())) out.push({ family, weight, style });
  };
  const base = tokens.join(" ");
  add(base);
  add(base.replace(/([a-z])([A-Z])/g, "$1 $2")); // AvantGarde -> Avant Garde
  return out;
}

/** Fontsource's id for a family: lower case, every other run of characters one hyphen ("Open Sans" -> "open-sans"). */
export const fontsourceId = (family) => family.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");  // NOSONAR - bounded, human-entered strings (file names / emails); no ReDoS exposure, rewrite would risk parsing changes

/** Google css2 URL for ONE weight/style of a family (used to register it under another name). */
export function googleFaceUrl(family, weight, style) {
  const slug = encodeURIComponent(family.trim()).replaceAll("%20", "+");
  return `https://fonts.googleapis.com/css2?family=${slug}:ital,wght@${style === "italic" ? 1 : 0},${weight}&display=swap`;
}

/** The @font-face blocks of a Google css2 response we want: [{subset, url, unicodeRange}] for latin / latin-ext / tamil. */
export function parseGoogleCss(css) {
  const out = [];
  const re = /\/\*\s*([\w-]+)\s*\*\/\s*@font-face\s*{([^}]*)}/g;
  let m;
  while ((m = re.exec(css || ""))) {
    const subset = m[1];
    if (!["latin", "latin-ext", "tamil"].includes(subset)) continue;
    const url = /url\(([^)]+)\)/.exec(m[2]);
    const range = /unicode-range:\s*([^;]+);/.exec(m[2]);  // NOSONAR - bounded, human-entered strings (file names / emails); no ReDoS exposure, rewrite would risk parsing changes
    if (url) out.push({ subset, url: url[1].replace(/["']/g, ""), unicodeRange: range ? range[1].trim() : undefined });
  }
  return out;
}

/** From Fontsource metadata: the files for the weight nearest `weight`, `style` if it exists - the default subset plus Tamil. */
export function pickFontsourceFiles(meta, weight, style) {
  if (!meta || !meta.variants) return [];
  const weights = Object.keys(meta.variants).map(Number).filter((w) => !Number.isNaN(w));
  if (!weights.length) return [];
  const w = weights.reduce((best, x) => (Math.abs(x - weight) < Math.abs(best - weight) ? x : best), weights[0]);
  const styles = meta.variants[w] || {};
  const st = styles[style] ? style : styles.normal ? "normal" : Object.keys(styles)[0];
  const subsets = styles[st] || {};
  const wanted = [...new Set([meta.defSubset || "latin", "latin", "tamil"])].filter((sub) => subsets[sub]);
  return wanted
    .map((sub) => ({
      subset: sub,
      url: subsets[sub].url && (subsets[sub].url.woff2 || subsets[sub].url.woff || subsets[sub].url.ttf),
      unicodeRange: meta.unicodeRange && meta.unicodeRange[sub],
      weight: w,
      style: st,
    }))
    .filter((f) => f.url);
}

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
    const quoted = `"${family.replaceAll('"', '\\"')}"`;
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
      .then((j) => (j?.available ? new Set(j.fonts.map((f) => f.toLowerCase())) : null))
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

/** Register `files` ([{url, unicodeRange}]) under `family` (the board's own name, so LiveText finds it). */
async function addFaces(family, files) {
  if (typeof FontFace === "undefined" || !files.length) return false;
  const faces = [];
  for (const f of files) {
    try {
      const face = new FontFace(family, `url("${f.url}")`, f.unicodeRange ? { unicodeRange: f.unicodeRange } : {});
      await face.load();
      faces.push(face);
    } catch {
      /* one subset failing is fine - the check below decides */
    }
  }
  faces.forEach((face) => document.fonts.add(face));
  return faces.length > 0 && isFontRenderable(family);
}

/** Google Fonts under the name's base family ("Roboto Bold" -> Roboto 700), registered as `family`. */
async function fromGoogleBase(family, cand) {
  try {
    const r = await fetch(googleFaceUrl(cand.family, cand.weight, cand.style), { mode: "cors" });
    if (!r.ok) return false;
    return await addFaces(family, parseGoogleCss(await r.text()));
  } catch {
    return false;
  }
}

/** Fontsource (open-licensed fonts on jsDelivr): metadata from api.fontsource.org, files from its CDN, registered as `family`. */
async function fromFontsource(family, cand) {
  const id = fontsourceId(cand.family);
  if (!id) return false;
  try {
    const r = await fetch(`https://api.fontsource.org/v1/fonts/${id}`, { mode: "cors" });
    if (!r.ok) return false; // 404: Fontsource has no such family
    const files = pickFontsourceFiles(await r.json(), cand.weight, cand.style);
    if (await addFaces(family, files)) {
      sourceDetail.set(family.toLowerCase(), `Fontsource "${id}" (weight ${files[0].weight})`);
      return true;
    }
  } catch {
    /* network / CORS: not found here */
  }
  return false;
}

const pending = new Map(); // family -> promise of its status, so a family is fetched once per page
const sourceDetail = new Map(); // family -> where a web font came from, for the editor's tooltip

/** Where a web-sourced family was found ("" if not web-sourced by name variant). */
export const fontSourceDetail = (family) => sourceDetail.get((family || "").toLowerCase()) || "";

/**
 * Make `family` renderable if possible - searching every source before giving up (see the top of this file).
 * Resolves to "local" | "server" | "google" | "fontsource" | "missing".
 */
export function ensureFont(family) {
  const key = family.toLowerCase();
  if (!pending.has(key)) {
    pending.set(
      key,
      (async () => {
        if (isFontRenderable(family)) return "local";
        if (await fromServer(family)) return "server";
        if (await fromGoogle(family)) return "google";
        for (const cand of fontCandidates(family).slice(1)) { // the exact name was tried just above
          if (await fromGoogleBase(family, cand)) {
            sourceDetail.set(key, `Google Fonts "${cand.family}" (weight ${cand.weight})`);
            return "google";
          }
        }
        for (const cand of fontCandidates(family)) {
          if (await fromFontsource(family, cand)) return "fontsource";
        }
        return "missing";
      })(),
    );
  }
  return pending.get(key);
}

/** A web font fixes the editor preview only - CorelDRAW on the server, which writes the exports, does not have it. */
export const isWebOnly = (status) => status === "google" || status === "fontsource";
