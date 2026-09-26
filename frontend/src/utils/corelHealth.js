// Launch-screen readiness check against GET /api/corel/health (unit-tested in corelHealth.test.mjs). There is no running CorelDRAW to
// "connect" to - the server launches a hidden instance per job - so "ready" means: the server answers, and it has a usable CorelDRAW
// install (or runs the mock engine). The endpoint never starts CorelDRAW.

export const HEALTH_URL = "/api/corel/health";

/** "27.0.0.121" -> "27.0" */
const shortVersion = (v) => (v ? v.split(".").slice(0, 2).join(".") : "");

/**
 * What the splash shows for a check result. `result` is the endpoint's JSON, or { unreachable: true } when the request failed.
 * Returns { state: "ok" | "error" | "offline", title, detail, memory, lowMemory, steps, badges, pill }: `detail` is one line about
 * the engine, `memory` one line about free RAM, `steps` what to do about a failure, `badges` [{label, value, warn}] for the HUD's
 * detail chips on success, `pill` the short status label shown on failure.
 */
export function describeHealth(result) {
  if (!result || result.unreachable) {
    return {
      state: "offline",
      pill: "Server unreachable",
      badges: [],
      title: "Automation server not reachable",
      detail: "The Signage Automation backend did not respond.",
      memory: "",
      lowMemory: false,
      steps: [
        "Start the Signage Automation backend on the server machine.",
        "Make sure this page is opened from the same machine or network as the backend.",
        "Then retry the connection.",
      ],
    };
  }
  const lowMemory = !!result.low_memory;
  const memory = result.free_ram_gb == null ? "" : lowMemory
    ? `Memory low: ${result.free_ram_gb} GB free, conversions need ${result.min_free_ram_gb} GB - close other programs before converting.`
    : "Memory nominal";
  if (!result.ok) {
    return {
      state: "error",
      pill: "CorelDRAW not found",
      badges: [],
      title: "CorelDRAW not available",
      detail: result.message || "No usable CorelDRAW installation was found.",
      memory,
      lowMemory,
      steps: [
        "Install CorelDRAW 2019 or newer on the server machine.",
        "If SIGNAGE_COREL_PROGID is set, check that it names an installed version.",
        "Then retry the connection.",
      ],
    };
  }
  if (result.engine === "mock") {
    return { state: "ok", title: "Demo engine ready", detail: "Mock engine - previews are simulated, CorelDRAW is not used.", memory, lowMemory, steps: [],
      badges: [{ label: "Engine", value: "Mock (demo)" }], pill: "" };
  }
  const sel = result.selected || {};
  const others = (result.installs || []).slice(1).map((i) => shortVersion(i.version)).filter(Boolean);
  const detail = others.length ? `Fallback: v${others.join(", v")}` : "Host engine ready";
  const version = shortVersion(sel.version);
  // "COM: Registered" is what the check verified (the COM registration and its executable) - not a live connection
  const badges = [{ label: "CorelDRAW", value: `v${version || "?"} Active` }, { label: "COM", value: "Registered" }];
  if (others.length) badges.push({ label: "Fallback", value: `v${others.join(", v")}` });
  if (result.free_ram_gb != null) badges.push({ label: "Memory", value: lowMemory ? `${result.free_ram_gb} GB free` : "Nominal", warn: lowMemory });
  return { state: "ok", title: `CorelDRAW Connected (v${version})`, version, detail, memory, lowMemory, steps: [], badges, pill: "" };
}

/** Fetch the health report, never throwing: a network failure or a timeout resolves to { unreachable: true }. */
export async function checkCorelConnection({ timeoutMs = 8000, fetchImpl = typeof fetch !== "undefined" ? fetch : null } = {}) {
  if (!fetchImpl) return { unreachable: true };
  const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
  try {
    const r = await fetchImpl(HEALTH_URL, { cache: "no-store", signal: ctrl ? ctrl.signal : undefined });
    if (!r.ok) return { unreachable: true };
    return await r.json();
  } catch {
    return { unreachable: true };
  } finally {
    if (timer) clearTimeout(timer);
  }
}
