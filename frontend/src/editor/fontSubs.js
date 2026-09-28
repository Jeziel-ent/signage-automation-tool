// "Missing Font Detected": which missing font to ask about next, and which font a text node is DRAWN in once a substitute
// is chosen. `subs` maps an original (missing) family to { font, permanent }; permanent ones come from the server
// (GET /api/fonts/substitutions), temporary ones live only in this editor tab (sessionStorage, per shop).

const key = (shopId, kind) => `signage.fontSubs.${kind}.${shopId}`;

/** The first missing font the person has not substituted or dismissed yet (null when there is nothing to ask). */
export function nextMissingFont(missing, subs, dismissed) {
  const done = new Set([...Object.keys(subs || {}), ...(dismissed || [])].map((f) => f.toLowerCase()));
  return (missing || []).find((f) => !done.has(f.toLowerCase())) || null;
}

/** The substitute to draw `font` in, or null (matched case-insensitively). */
export function substituteFor(font, subs) {
  if (!font || !subs) return null;
  const hit = Object.entries(subs).find(([orig]) => orig.toLowerCase() === font.toLowerCase());
  return hit ? hit[1].font : null;
}

/** A sensible first choice for the dropdown: a common installed sans, else the first installed font. */
export function defaultReplacement(installed, current) {
  if (current) return current;
  const lower = new Map((installed || []).map((f) => [f.toLowerCase(), f]));
  for (const f of ["Arial", "Segoe UI", "Calibri", "Helvetica", "Liberation Sans", "DejaVu Sans"]) {
    if (lower.has(f.toLowerCase())) return lower.get(f.toLowerCase());
  }
  return (installed || [])[0] || "";
}

/** Temporary substitutions and dismissed fonts of this editor tab ({} / [] when storage is unavailable). */
export function loadSession(shopId) {
  try {
    return {
      temp: JSON.parse(sessionStorage.getItem(key(shopId, "temp")) || "{}"),
      dismissed: JSON.parse(sessionStorage.getItem(key(shopId, "dismissed")) || "[]"),
    };
  } catch {
    return { temp: {}, dismissed: [] };
  }
}

export function saveSession(shopId, temp, dismissed) {
  try {
    sessionStorage.setItem(key(shopId, "temp"), JSON.stringify(temp));
    sessionStorage.setItem(key(shopId, "dismissed"), JSON.stringify(dismissed));
  } catch {
    /* private mode / blocked storage: this tab still has it in memory */
  }
}
