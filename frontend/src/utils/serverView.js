// The Server page (administrators): what the audit log says in plain words, and the storage categories by their names.

export const STORAGE_LABELS = {
  masters: "Masters (.cdr uploads)", boards: "Converted boards (CDR, PDF, previews)", scenes: "Editor scenes (can be rebuilt)",
  exports: "Exports", product_assets: "Product images", fonts: "Fonts fetched from PCs", shared: "Share links",
  runs: "Working files", backups: "Database backups", database: "Database", other: "Other",
};

/** Categories that cleaning can shrink, as the page words them. */
export const CLEAN_LABELS = { temporary: "temporary leftovers", exports: "old exports", scenes: "old editor scenes" };

const ACTIONS = {
  login: "signed in", logout: "signed out", login_failed: "failed to sign in", login_locked: "was locked out after failed sign-ins",
  setup_admin: "created the first administrator", user_created: "created the account", user_updated: "changed the account",
  master_uploaded: "uploaded the master", master_edited: "edited the master", master_file_replaced: "replaced the file of the master",
  master_deleted: "deleted the master", correction_deleted: "deleted the learned correction", shop_deleted: "deleted the shop",
  agent_paired: "connected the PC", agent_revoked: "disconnected the PC", font_substituted: "set a font replacement",
  cleanup: "cleaned up the server disk", user_deleted: "deleted the account",
  conversion_stopped: "stopped the conversion of shop", batch_stopped: "stopped a conversion batch:",
};

export const actionLabel = (a) => ACTIONS[a] || a.replaceAll("_", " ");

/** "boss@x.com signed in" / "boss@x.com deleted the master Hangyo / m.cdr" - the one-line version of an audit entry. */
export function describeEntry(e) {
  const who = e.user_email || "someone";
  const base = ["login_failed", "login_locked"].includes(e.action) ? `${e.target || "someone"} ${actionLabel(e.action)}` : `${who} ${actionLabel(e.action)}`;
  const target = ["login", "logout", "login_failed", "login_locked", "setup_admin"].includes(e.action) ? "" : e.target;
  return target ? `${base} ${target}` : base;
}

/** Facts worth showing beside an entry (never secrets: the server does not log them). */
export function entryDetails(e) {
  const d = e.detail || {};
  const out = [];
  if (d.changed?.length) out.push(`changed: ${d.changed.join(", ")}`);
  if (d.role) out.push(`role: ${d.role}`);
  if (d.active !== undefined) out.push(d.active ? "enabled" : "disabled");
  if (d.fields?.length) out.push(`fields: ${d.fields.join(", ")}`);
  if (d.was) out.push(`was ${d.was}`);
  if (d.deleted !== undefined) out.push(`${d.deleted} item(s)`);
  return out.join(" · ");
}

/** Storage rows biggest first, with the share of the whole, skipping empty categories. */
export function storageRows(bytes, total) {
  return Object.entries(bytes || {})
    .filter(([, n]) => n > 0)
    .map(([key, n]) => ({ key, label: STORAGE_LABELS[key] || key, bytes: n, share: total ? n / total : 0 }))
    .sort((a, b) => b.bytes - a.bytes);
}
