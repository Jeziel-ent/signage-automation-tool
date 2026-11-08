// The /admin page: plain-words helpers (no React, so they are unit-tested).

/** "3 min ago" / "2 h ago" / "5 days ago" / "never". `now` in seconds. */
export function ago(ts, now = Date.now() / 1000) {
  if (!ts) return "never";
  const s = Math.max(0, now - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  const d = Math.floor(s / 86400);
  return `${d} day${d === 1 ? "" : "s"} ago`;
}

/** "3 d 4 h" / "5 h 12 min" / "8 min" - how long the server has been up. */
export function fmtUptime(seconds) {
  if (seconds == null) return "unknown";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d} d ${h} h`;
  if (h) return `${h} h ${m} min`;
  return `${m} min`;
}

/** The one line that says how a designer is doing right now: working on a PC, signed in, or away. */
export function presence(user) {
  const online = (user.pcs || []).some((p) => p.online);
  if (online && user.signed_in) return { key: "working", label: "Working now" };
  if (online) return { key: "pc", label: "PC connected" };
  if (user.signed_in) return { key: "signed", label: "Signed in" };
  return { key: "away", label: "Away" };
}

/** Totals for the footer of the designers table. */
export function usageTotals(users) {
  return users.reduce((t, u) => ({ converted: t.converted + u.converted, exported: t.exported + u.exported, failed: t.failed + u.failed, logins: t.logins + u.logins }),
    { converted: 0, exported: 0, failed: 0, logins: 0 });
}

/** Used share of a Spaces bucket (0-100) or null when no allowance is configured. */
export function spacesPct(spaces) {
  return spaces?.quota_bytes ? Math.min(100, (spaces.bytes / spaces.quota_bytes) * 100) : null;
}

export const DAY_CHOICES = [7, 30, 90, 365];
