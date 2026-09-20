import React, { useEffect, useRef, useState } from "react";

const UNITS = ["mm", "cm", "in", "ft", "m"];
const newShop = () => ({ key: Math.random().toString(36).slice(2), name: "", width: "", height: "", unit: "ft" });

export default function App() {
  const [file, setFile] = useState(null);
  const [brands, setBrands] = useState([]);
  const [brand, setBrand] = useState("");
  const [newBrand, setNewBrand] = useState("");
  const [shops, setShops] = useState([newShop()]);
  const [job, setJob] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const timer = useRef(null);

  useEffect(() => {
    fetch("/api/brands").then((r) => r.json()).then(setBrands).catch(() => {});
    return () => clearInterval(timer.current);
  }, []);

  const updateShop = (key, patch) => setShops((s) => s.map((x) => (x.key === key ? { ...x, ...patch } : x)));
  const removeShop = (key) => setShops((s) => (s.length > 1 ? s.filter((x) => x.key !== key) : s));

  async function addBrand() {
    const name = newBrand.trim();
    if (!name) return;
    const r = await fetch("/api/brands", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
    });
    setBrands(await r.json());
    setBrand(name);
    setNewBrand("");
  }

  const valid =
    file && brand && shops.every((s) => s.name.trim() && +s.width > 0 && +s.height > 0);

  async function generate() {
    setError(""); setBusy(true); setJob(null);
    const fd = new FormData();
    fd.append("master", file);
    fd.append("brand", brand);
    fd.append("shops", JSON.stringify(shops.map(({ key, ...s }) => ({ ...s, width: +s.width, height: +s.height }))));
    try {
      const r = await fetch("/api/jobs", { method: "POST", body: fd });
      if (!r.ok) throw new Error((await r.json()).detail || "Upload failed");
      const { id } = await r.json();
      clearInterval(timer.current);
      timer.current = setInterval(async () => {
        const j = await (await fetch(`/api/jobs/${id}`)).json();
        setJob(j);
        if (j.status === "done" || j.status === "error") { clearInterval(timer.current); setBusy(false); }
      }, 1000);
    } catch (e) {
      setError(e.message); setBusy(false);
    }
  }

  return (
    <div className="page">
      <header>
        <h1>Signage Automation Tool</h1>
        <p>Upload a master .cdr, pick the brand, add shops with their sizes, and generate resized artwork.</p>
      </header>

      <section className="card">
        <h2>1. Master file</h2>
        <label className="drop">
          <input type="file" accept=".cdr" onChange={(e) => setFile(e.target.files[0] || null)} />
          {file ? <strong>{file.name}</strong> : <span>Choose a master .cdr file</span>}
        </label>
      </section>

      <section className="card">
        <h2>2. Brand</h2>
        <div className="row">
          <select value={brand} onChange={(e) => setBrand(e.target.value)}>
            <option value="">Select brand…</option>
            {brands.map((b) => <option key={b}>{b}</option>)}
          </select>
          <input placeholder="New brand name" value={newBrand} onChange={(e) => setNewBrand(e.target.value)} />
          <button className="ghost" onClick={addBrand} disabled={!newBrand.trim()}>Add brand</button>
        </div>
      </section>

      <section className="card">
        <h2>3. Shops</h2>
        <div className="shops">
          <div className="shop head"><span>Shop name</span><span>Width</span><span>Height</span><span>Unit</span><span /></div>
          {shops.map((s) => (
            <div className="shop" key={s.key}>
              <input placeholder="e.g. Anna Nagar" value={s.name} onChange={(e) => updateShop(s.key, { name: e.target.value })} />
              <input type="number" min="0" step="any" placeholder="W" value={s.width} onChange={(e) => updateShop(s.key, { width: e.target.value })} />
              <input type="number" min="0" step="any" placeholder="H" value={s.height} onChange={(e) => updateShop(s.key, { height: e.target.value })} />
              <select value={s.unit} onChange={(e) => updateShop(s.key, { unit: e.target.value })}>
                {UNITS.map((u) => <option key={u}>{u}</option>)}
              </select>
              <button className="ghost" onClick={() => removeShop(s.key)} disabled={shops.length === 1} title="Remove">✕</button>
            </div>
          ))}
        </div>
        <button className="ghost" onClick={() => setShops((s) => [...s, newShop()])}>+ Add shop</button>
      </section>

      <div className="actions">
        <button className="primary" onClick={generate} disabled={!valid || busy}>
          {busy ? "Generating…" : "Generate"}
        </button>
        {error && <span className="err">{error}</span>}
      </div>

      {job && <Results job={job} />}
    </div>
  );
}

function Results({ job }) {
  return (
    <section className="card">
      <div className="row between">
        <h2>Results — {job.brand} <small>({job.engine} engine)</small></h2>
        {job.status === "done" && <a className="btn" href={`/api/jobs/${job.id}/download.zip`}>Download all (.zip)</a>}
      </div>
      {job.results.map((r, i) => (
        <div className="result" key={i}>
          <div className="row between">
            <h3>{r.shop}</h3>
            <span className={`pill ${r.status}`}>{r.status}</span>
          </div>
          {r.error && <p className="err">{r.error}</p>}
          {r.note && <p className="note">{r.note}</p>}
          {r.status === "done" && (
            <>
              <div className="grid">
                <div className="preview">
                  {/\.svg$|\.png$|\.jpg$/.test(r.files.preview) &&
                    <img alt="preview" src={`/api/jobs/${job.id}/files/${i + 1}/${r.files.preview}`} />}
                </div>
                <div>
                  <div className="links">
                    {Object.entries(r.files).map(([k, f]) => (
                      <a key={k} className="btn small" href={`/api/jobs/${job.id}/files/${i + 1}/${f}`}>{k.toUpperCase()}</a>
                    ))}
                  </div>
                  <ReportTable report={r.report} />
                </div>
              </div>
            </>
          )}
        </div>
      ))}
    </section>
  );
}

const f = (n) => (Math.round(n * 10) / 10).toLocaleString();

function ReportTable({ report }) {
  return (
    <details>
      <summary>
        Object report — page {f(report.original_page_mm.w)}×{f(report.original_page_mm.h)} mm →{" "}
        {f(report.new_page_mm.w)}×{f(report.new_page_mm.h)} mm
      </summary>
      <table>
        <thead><tr><th>Object</th><th>Role</th><th>Original x,y / w×h</th><th>New x,y / w×h</th><th>Notes</th></tr></thead>
        <tbody>
          {report.objects.map((o) => (
            <tr key={o.id}>
              <td>{o.name}</td><td>{o.role}</td>
              <td>{f(o.orig.x)}, {f(o.orig.y)} / {f(o.orig.w)}×{f(o.orig.h)}</td>
              <td>{f(o.x)}, {f(o.y)} / {f(o.w)}×{f(o.h)}</td>
              <td className="warn">{o.warnings.join("; ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
