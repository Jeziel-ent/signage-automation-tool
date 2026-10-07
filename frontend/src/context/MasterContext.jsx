import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { groupMasters } from "../utils/masters.js";

// The master template registry (GET /api/masters), fetched once when the workspace loads and kept here for every page:
// `masters` = every registered master (all brands, oldest first), `landscapeMasters` / `portraitMasters` = the same grouped
// by orientation, `forBrand(brand)` = one brand's { landscape, portrait } (what the Automation page and its queue use).
// `add` records a master the page just uploaded (POST /api/masters/upload); `remove` deletes one (DELETE /api/masters/{id}).
const MasterContext = createContext(null);

export function MasterProvider({ children }) {
  const [masters, setMasters] = useState([]);
  const [brands, setBrands] = useState([]);
  const [mastersBrand, setMastersBrand] = useState(""); // the brand picked on the Masters page: kept while the app is open, empty after a reload
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async ({ quiet = false } = {}) => {
    if (!quiet) setLoading(true);
    try {
      const r = await fetch("/api/masters");
      if (!r.ok) throw new Error(`Could not load master templates (HTTP ${r.status})`);
      setMasters((await r.json()).masters || []);
      setError("");
    } catch (e) {
      setError(e.message || "Could not load master templates");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    fetch("/api/v2/brands")
      .then((r) => r.json())
      .then((list) => Array.isArray(list) && setBrands(list))
      .catch(() => {});
  }, [refresh]);

  // Brands are shared by the Automation and Masters pages: a brand created on one is in the other's dropdown straight away.
  const addBrand = useCallback(async (name) => {
    const r = await fetch("/api/v2/brands", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const list = await r.json();
    if (!r.ok) throw new Error(list.detail || `Could not add the brand (HTTP ${r.status})`);
    setBrands(list);
    return list;
  }, []);

  // A master edited (renamed / re-sized / file replaced) on either page: take the server's version of it.
  const update = useCallback((m) => setMasters((list) => list.map((x) => (x.id === m.id ? m : x))), []);

  const add = useCallback((m) => setMasters((list) => (list.some((x) => x.id === m.id) ? list : [...list, m])), []);

  const remove = useCallback(async (id) => {
    const r = await fetch(`/api/masters/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!r.ok && r.status !== 404) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || `Could not delete the master (HTTP ${r.status})`);
    }
    setMasters((list) => list.filter((x) => x.id !== id));
    await refresh({ quiet: true });   // the automatic names ("Master 1", "Master 2" ...) are numbered by the server: renumber after a delete
  }, [refresh]);

  const value = useMemo(() => {
    const all = groupMasters(masters);
    return {
      masters,
      brands,
      mastersBrand,
      setMastersBrand,
      addBrand,
      update,
      landscapeMasters: all.landscape,
      portraitMasters: all.portrait,
      forBrand: (brand) => groupMasters(masters, brand || ""),
      loading,
      error,
      refresh,
      add,
      remove,
    };
  }, [masters, brands, mastersBrand, addBrand, update, loading, error, refresh, add, remove]);

  return <MasterContext.Provider value={value}>{children}</MasterContext.Provider>;
}

export function useMasters() {
  const ctx = useContext(MasterContext);
  if (!ctx) throw new Error("useMasters() needs a <MasterProvider> above it");
  return ctx;
}
