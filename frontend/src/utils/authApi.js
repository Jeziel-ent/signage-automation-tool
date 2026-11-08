// Small helpers around /api/auth/* (the session cookie is HttpOnly and sent by the browser itself).

export async function readError(r, fallback) {
  try {
    const body = await r.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail[0]?.msg) return body.detail[0].msg;
  } catch {
    /* not JSON */
  }
  return fallback;
}

/** {auth_enabled, user, needs_setup}; a network failure answers null. */
export async function fetchMe() {
  try {
    const r = await fetch("/api/auth/me");
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}

/** POST login or first-run setup. Returns {user} or throws Error(message). */
export async function sendCredentials(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(await readError(r, "Sign-in failed"));
  return r.json();
}

/** True for a response that means "the session ended" (any /api call except the auth ones that answer 401 on purpose). */
export function isSessionExpired(url, status) {
  return status === 401 && String(url).includes("/api/") && !String(url).includes("/api/auth/login");
}
