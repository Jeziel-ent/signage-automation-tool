// The admin file browser (components/AdminFiles.jsx): grouping and totals, no React so they are unit-tested.

/** Local day key + "Wednesday, 12 Nov 2026" for a unix time in seconds. */
export function dayOf(ts) {
  const d = new Date(ts * 1000);
  const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const label = d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short", year: "numeric" });
  return { key, label };
}

/** Boards grouped by the local day they were made, newest day first; each group keeps its items newest first and its total size. */
export function byDay(boards) {
  const groups = new Map();
  for (const b of [...(boards || [])].sort((a, z) => z.made_at - a.made_at)) {
    const { key, label } = dayOf(b.made_at);
    if (!groups.has(key)) groups.set(key, { day: key, label, items: [], bytes: 0 });
    const g = groups.get(key);
    g.items.push(b);
    g.bytes += b.bytes;
  }
  return [...groups.values()];
}

/** Masters grouped by brand (A-Z), with each brand's total size. */
export function byBrand(masters) {
  const groups = new Map();
  for (const m of masters || []) {
    if (!groups.has(m.brand)) groups.set(m.brand, { brand: m.brand, items: [], bytes: 0 });
    const g = groups.get(m.brand);
    g.items.push(m);
    g.bytes += m.bytes;
  }
  return [...groups.values()].sort((a, b) => a.brand.localeCompare(b.brand));
}

/** Count and bytes per category, and everything together. */
export function filesTotals(data) {
  const sum = (list) => ({ n: (list || []).length, bytes: (list || []).reduce((t, x) => t + (x.bytes || 0), 0) });
  const t = { masters: sum(data?.masters), boards: sum(data?.boards), backups: sum(data?.backups), orphans: sum(data?.orphans) };
  t.all = t.masters.bytes + t.boards.bytes + t.backups.bytes + t.orphans.bytes;
  return t;
}
