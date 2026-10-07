// Small request helpers the Automation page uses for the shops API (kept out of the component so it stays readable).

/** The server's `detail` of a failed response, else `fallback`. */
export async function apiDetail(response, fallback) {
  return (await response.json().catch(() => ({}))).detail || fallback;
}

/** One batched status request for several shops: {id: status} or null when the request failed (try again on the next tick). */
export async function fetchShopStatuses(ids) {
  try {
    const r = await fetch(`/api/v2/shop-statuses?ids=${ids.map(encodeURIComponent).join(",")}`);
    return r.ok ? await r.json() : null;
  } catch {
    return null; // a network blip
  }
}

/** First save of a draft row: creates the shop on the server. {saved} or {error: "<name>: reason"}. */
export async function createShopFromDraft(jobId, row, payload) {
  const r = await fetch(`/api/v2/jobs/${jobId}/shops`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) return { error: `${row.name || "Shop"}: ${await apiDetail(r, "could not save the shop")}` };
  return { saved: await r.json() };
}
