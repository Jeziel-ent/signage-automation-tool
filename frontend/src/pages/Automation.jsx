import { useEffect, useRef, useState } from "react";
import UploadDropzone from "../components/UploadDropzone.jsx";
import { useSteppedProgress } from "../hooks/useSteppedProgress.js";

const UNITS = ["in", "cm", "mm", "ft"];
const emptyShopForm = () => ({ name: "", width: "", width_unit: "in", height: "", height_unit: "in", reference: "" });

// CorelEngine's own named steps (see backend/app/engines.py's step() closure
// and CLAUDE.md "Production hardening"), each with the cumulative percent
// reached once that step is confirmed complete - mirrors main.py's
// _STEP_PERCENT. Exported shape reused by useSteppedProgress.
export const CONVERT_STEPS = [
  { key: "launch", endPct: 10 },
  { key: "open", endPct: 25 },
  { key: "tile_resize", endPct: 55 },
  { key: "saveas", endPct: 75 },
  { key: "pdf", endPct: 88 },
  { key: "png", endPct: 97 },
];

export default function Automation() {
  const [brands, setBrands] = useState([]);
  const [brand, setBrand] = useState("");
  const [addingBrand, setAddingBrand] = useState(false);
  const [newBrand, setNewBrand] = useState("");

  const [job, setJob] = useState(null); // {id, preview_url, preview_error}
  const [shops, setShops] = useState([]);
  const [shopForm, setShopForm] = useState(emptyShopForm());
  const [shopError, setShopError] = useState("");
  const [stepEstimates, setStepEstimates] = useState({});
  const pollers = useRef({});

  useEffect(() => {
    fetch("/api/v2/brands")
      .then((r) => r.json())
      .then(setBrands)
      .catch(() => {});
    // Measured average step durations (seconds), used to pace the smoothed
    // per-row progress animation - see useSteppedProgress. Fetched once per
    // page load, not on every status poll.
    fetch("/api/v2/step-estimates")
      .then((r) => r.json())
      .then(setStepEstimates)
      .catch(() => {});
  }, []);

  useEffect(
    () => () => {
      Object.values(pollers.current).forEach(clearInterval);
    },
    [],
  );

  async function addBrand() {
    const name = newBrand.trim();
    if (!name) return;
    const r = await fetch("/api/v2/brands", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const list = await r.json();
    setBrands(list);
    setBrand(name);
    setNewBrand("");
    setAddingBrand(false);
  }

  function onUploaded(body) {
    setJob(body);
    setShops([]);
  }

  async function addShop() {
    setShopError("");
    const f = shopForm;
    if (!f.name.trim() || !(+f.width > 0) || !(+f.height > 0)) {
      setShopError("Shop name, width and height are required");
      return;
    }
    const r = await fetch(`/api/v2/jobs/${job.id}/shops`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...f, width: +f.width, height: +f.height }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      setShopError(body.detail || "Could not add shop");
      return;
    }
    const shop = await r.json();
    setShops((s) => [...s, shop]);
    setShopForm(emptyShopForm());
  }

  function pollShop(shopId) {
    if (pollers.current[shopId]) return;
    pollers.current[shopId] = setInterval(async () => {
      const r = await fetch(`/api/v2/shops/${shopId}/status`);
      if (!r.ok) return;
      const data = await r.json();
      setShops((s) => s.map((x) => (x.id === shopId ? { ...x, ...data } : x)));
      if (data.status === "done" || data.status === "failed") {
        clearInterval(pollers.current[shopId]);
        delete pollers.current[shopId];
      }
    }, 800);
  }

  async function convertShop(shopId) {
    setShops((s) => s.map((x) => (x.id === shopId ? { ...x, status: "queued", progress_pct: 0 } : x)));
    const r = await fetch(`/api/v2/shops/${shopId}/convert`, { method: "POST" });
    if (r.ok || r.status === 409) pollShop(shopId);
  }

  function openEditor(shop) {
    window.open(`/editor/${job.id}/${shop.id}`, "_blank");
  }

  return (
    <div className="automation-page">
      <h1>Automation</h1>

      <section className="card">
        <h2>1. Brand</h2>
        <div className="row">
          <select value={brand} onChange={(e) => setBrand(e.target.value)}>
            <option value="">Select brand…</option>
            {brands.map((b) => (
              <option key={b}>{b}</option>
            ))}
          </select>
          {addingBrand ? (
            <>
              <input
                autoFocus
                placeholder="New brand name"
                value={newBrand}
                onChange={(e) => setNewBrand(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addBrand()}
              />
              <button className="btn" onClick={addBrand} disabled={!newBrand.trim()}>
                Add
              </button>
              <button className="btn ghost" onClick={() => setAddingBrand(false)}>
                Cancel
              </button>
            </>
          ) : (
            <button className="btn-icon" title="Add brand" onClick={() => setAddingBrand(true)}>
              +
            </button>
          )}
        </div>
      </section>

      <section className="card">
        <h2>2. Master file</h2>
        {!brand ? (
          <p className="hint">Select a brand first.</p>
        ) : (
          <>
            <UploadDropzone brand={brand} onUploaded={onUploaded} />
            {job && (
              <div className="preview-row">
                {job.preview_url ? (
                  <img className="master-preview" src={job.preview_url} alt="master preview" />
                ) : (
                  <div className="preview-missing">{job.preview_error || "Preview not available"}</div>
                )}
              </div>
            )}
          </>
        )}
      </section>

      {job && (
        <section className="card">
          <h2>3. Shops</h2>
          <table className="shops-table">
            <thead>
              <tr>
                <th>S.no</th>
                <th>Shop name</th>
                <th>Width</th>
                <th>Unit</th>
                <th>Height</th>
                <th>Unit</th>
                <th>Reference</th>
                <th>Convert</th>
                <th>Editor</th>
              </tr>
            </thead>
            <tbody>
              {shops.map((s) => (
                <tr key={s.id}>
                  <td>{s.seq_no}</td>
                  <td>{s.name}</td>
                  <td>{s.width}</td>
                  <td>{s.width_unit}</td>
                  <td>{s.height}</td>
                  <td>{s.height_unit}</td>
                  <td>{s.reference || "—"}</td>
                  <td>
                    <ConvertCell shop={s} onConvert={() => convertShop(s.id)} stepEstimates={stepEstimates} />
                  </td>
                  <td>
                    {s.status === "done" ? (
                      <button className="btn small" onClick={() => openEditor(s)}>
                        Open
                      </button>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
              <NewShopRow seqNo={shops.length + 1} form={shopForm} setForm={setShopForm} onAdd={addShop} />
            </tbody>
          </table>
          {shopError && <div className="err">{shopError}</div>}
        </section>
      )}
    </div>
  );
}

function NewShopRow({ seqNo, form, setForm, onAdd }) {
  return (
    <tr className="new-shop-row">
      <td>{seqNo}</td>
      <td>
        <input placeholder="Shop name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </td>
      <td>
        <input
          type="number"
          min="0"
          step="any"
          value={form.width}
          onChange={(e) => setForm({ ...form, width: e.target.value })}
        />
      </td>
      <td>
        <select value={form.width_unit} onChange={(e) => setForm({ ...form, width_unit: e.target.value })}>
          {UNITS.map((u) => (
            <option key={u}>{u}</option>
          ))}
        </select>
      </td>
      <td>
        <input
          type="number"
          min="0"
          step="any"
          value={form.height}
          onChange={(e) => setForm({ ...form, height: e.target.value })}
        />
      </td>
      <td>
        <select value={form.height_unit} onChange={(e) => setForm({ ...form, height_unit: e.target.value })}>
          {UNITS.map((u) => (
            <option key={u}>{u}</option>
          ))}
        </select>
      </td>
      <td>
        <input
          placeholder="optional"
          value={form.reference}
          onChange={(e) => setForm({ ...form, reference: e.target.value })}
        />
      </td>
      <td colSpan={2}>
        <button className="btn" onClick={onAdd}>
          Add shop
        </button>
      </td>
    </tr>
  );
}

function ConvertCell({ shop, onConvert, stepEstimates }) {
  // Called unconditionally (hooks can't be conditional) - it's a no-op
  // until `shop.status === "converting"` actually starts reporting steps.
  const smoothedPct = useSteppedProgress(CONVERT_STEPS, shop.step, shop.status === "done", stepEstimates);

  if (shop.status === "new") {
    return (
      <button className="btn small" onClick={onConvert}>
        Convert
      </button>
    );
  }
  if (shop.status === "queued") return <span className="pill">Queued…</span>;
  if (shop.status === "converting") {
    // Eased toward (but capped just below) the next real step threshold -
    // never a straight jump to the backend's last-polled value, and never
    // 100% here (that only happens once status flips to "done").
    return (
      <div className="row-progress">
        <div className="progress-bar small">
          <div className="progress-fill" style={{ width: `${smoothedPct}%` }} />
        </div>
        <span className="progress-pct">{smoothedPct}%</span>
      </div>
    );
  }
  if (shop.status === "done") return <span className="pill done">Done</span>;
  if (shop.status === "failed") {
    return (
      <span className="pill error" title={shop.error}>
        Failed
      </span>
    );
  }
  return null;
}
