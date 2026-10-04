import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { groupMasters } from "../utils/masters.js";

// The master template registry (GET /api/masters), fetched once when the workspace loads and kept here for every page:
// `masters` = every registered master (all brands, oldest first), `landscapeMasters` / `portraitMasters` = the same grouped
// by orientation, `forBrand(brand)` = one brand's { landscape, portrait } (what the Automation page and its queue use).
// `add` records a master the page just uploaded (POST /api/masters/upload); `remove` deletes one (DELETE /api/masters/{id}).
const MasterContext = createContext(null);

export function MasterProvider({ children }) {
  const [masters, setMasters] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
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
  }, [refresh]);

  const add = useCallback((m) => setMasters((list) => (list.some((x) => x.id === m.id) ? list : [...list, m])), []);

  const remove = useCallback(async (id) => {
    const r = await fetch(`/api/masters/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!r.ok && r.status !== 404) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || `Could not delete the master (HTTP ${r.status})`);
    }
    setMasters((list) => list.filter((x) => x.id !== id));
  }, []);

  const value = useMemo(() => {
    const all = groupMasters(masters);
    return {
      masters,
      landscapeMasters: all.landscape,
      portraitMasters: all.portrait,
      forBrand: (brand) => groupMasters(masters, brand || ""),
      loading,
      error,
      refresh,
      add,
      remove,
    };
  }, [masters, loading, error, refresh, add, remove]);

  return <MasterContext.Provider value={value}>{children}</MasterContext.Provider>;
}

export function useMasters() {
  const ctx = useContext(MasterContext);
  if (!ctx) throw new Error("useMasters() needs a <MasterProvider> above it");
  return ctx;
}
