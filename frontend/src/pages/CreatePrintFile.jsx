import { useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, FileCode, FileImage, FileText, FileUp, Loader2, Plus, Printer, Trash2, UploadCloud } from "lucide-react";
import { BOARD_TYPES, filenameFrom, fmtSqft, todayISO } from "../utils/printSheet.js";
import { assignSections, buildSpec, parseFileName, sectionTotals, validate } from "../utils/printFile.js";
import { postPrintFile } from "../utils/printFileRequest.js";
import "./CreatePrintFile.css";

let seq = 0;
const uid = (p) => `${p}${++seq}`;
const newSection = (name = "ACP BOARD") => ({ id: uid("s"), name, qty: "", sqft: "" });
const ACCEPTED = /\.(cdr|jpe?g|png|webp|bmp|tiff?)$/i;
const isImage = (f) => /\.(jpe?g|png|webp|bmp|tiff?)$/i.test(f.name);
const STAGES = [["uploading", "Uploading files"], ["rendering", "Building the sheet"], ["downloading", "Preparing download"]];

function Thumb({ url }) {
  const [loaded, setLoaded] = useState(false);
  if (!url) return <span className="pf-thumb-box"><FileCode size={20} /></span>;
  return (
    <span className={"pf-thumb-box" + (loaded ? "" : " loading")}>
      <img src={url} alt="" onLoad={() => setLoaded(true)} onError={() => setLoaded(true)} />
    </span>
  );
}

/**
 * "Create Print File": upload CDR / image files, fill in the details, group the files into sections (a material or board type
 * each) and download the print sheet for the printing team as a JPEG or PDF. Rendered by backend/app/print_file.py.
 * Layout: the steps on the left (details, files, sections), a sticky summary + Generate panel on the right.
 */
export default function CreatePrintFile() {
  const [title, setTitle] = useState("");
  const [projectNo, setProjectNo] = useState("");
  const [date, setDate] = useState(todayISO());
  const [lines, setLines] = useState(["", ""]);
  const [format, setFormat] = useState("pdf");
  const [sections, setSections] = useState(() => [newSection()]);
  const [items, setItems] = useState([]);
  const [drag, setDrag] = useState(false);
  const [reading, setReading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [upPct, setUpPct] = useState(0);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const input = useRef(null);
  const urls = useRef(new Map());

  useEffect(() => {
    const map = urls.current;
    return () => map.forEach((u) => URL.revokeObjectURL(u));
  }, []);

  const add = (fileList) => {
    const all = [...fileList];
    if (!all.length) return;
    setReading(true);
    // yield one frame so the "Reading files" loader paints before the (synchronous) parsing of a big drop
    setTimeout(() => {
      const parsed = all.filter((f) => ACCEPTED.test(f.name)).map((file) => ({ file, ...parseFileName(file.name) }));
      // a board type in the file name files it under the section of that name (made when it does not exist yet)
      const placed = assignSections(sections, parsed.map((p) => p.type), () => uid("s"), sections[sections.length - 1].id);
      const next = parsed.map((p, n) => {
        const id = uid("i");
        if (isImage(p.file)) urls.current.set(id, URL.createObjectURL(p.file));
        const { file, ...fields } = p;
        return { id, file, fileName: file.name, sectionId: placed.sectionIds[n], qty: "", ...fields };
      });
      setError(next.length < all.length ? "Only .cdr and image files (jpg, png, ...) can be added - the others were skipped." : "");
      setDone("");
      setSections(placed.sections);
      setItems((cur) => [...cur, ...next]);
      setReading(false);
    }, 30);
  };
  const edit = (id, patch) => setItems((cur) => cur.map((i) => (i.id === id ? { ...i, ...patch } : i)));
  const remove = (id) => {
    const u = urls.current.get(id);
    if (u) URL.revokeObjectURL(u);
    urls.current.delete(id);
    setItems((cur) => cur.filter((i) => i.id !== id));
  };
  const editSection = (id, patch) => setSections((cur) => cur.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  const removeSection = (id) => {
    if (sections.length < 2) return;
    const fallback = sections.find((s) => s.id !== id).id;
    setSections((cur) => cur.filter((s) => s.id !== id));
    setItems((cur) => cur.map((i) => (i.sectionId === id ? { ...i, sectionId: fallback } : i)));
  };

  const totals = useMemo(
    () => Object.fromEntries(sections.map((s) => [s.id, sectionTotals(items.filter((i) => i.sectionId === s.id))])),
    [sections, items],
  );
  const used = sections.filter((s) => totals[s.id].qty && items.some((i) => i.sectionId === s.id));
  const sum = used.reduce((a, s) => ({
    qty: a.qty + (s.qty === "" ? totals[s.id].qty : Number(s.qty) || 0),
    sqft: a.sqft + (s.sqft === "" ? totals[s.id].sqft : Number(s.sqft) || 0),
  }), { qty: 0, sqft: 0 });

  async function generate() {
    setError("");
    setDone("");
    if (!title.trim()) { setError("Enter a header (printing file title)."); return; }
    const problem = validate(items);
    if (problem) { setError(problem); return; }
    const { spec, files } = buildSpec({ title, projectNo, date, lines, format, sections, items });
    const form = new FormData();
    form.append("spec", JSON.stringify(spec));
    files.forEach((f) => form.append("files", f, f.name));
    setBusy(true);
    setUpPct(0);
    try {
      const { blob, disposition } = await postPrintFile(form, { onStage: setStage, onUpload: setUpPct });
      const name = filenameFrom(disposition, `Print_File.${format === "pdf" ? "pdf" : "jpg"}`);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      setDone(name);
    } catch (e) {
      setError(`Could not create the print file: ${e.message}`);
    } finally {
      setBusy(false);
      setStage("");
    }
  }

  const stageIdx = STAGES.findIndex(([k]) => k === stage);
  const progress = !busy ? 0 : stage === "uploading" ? Math.max(4, upPct * 0.4) : stage === "rendering" ? 40 : 90;

  // while a sheet is being built the inputs are inert (not just greyed): no edits can slip in between the spec and the render
  const lock = (el) => { if (el) el.inert = busy; };

  return (
    <div className="pf-page">
      <header className="pf-top">
        <div className="pf-top-icon"><Printer size={20} /></div>
        <div>
          <h1>Create Print File</h1>
          <p>Upload the artwork, fill in the details and download the sheet for the printing team.</p>
        </div>
      </header>

      <div className="pf-layout">
        <div className="pf-col pf-left" ref={lock}>
          <section className="ws-card pf-card pf-details">
            <div className="pf-step"><span>1</span><h2>Sheet details</h2></div>
            <label>Header (title)<input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. HANGYO - ACP BOARD" /></label>
            <div className="pf-two">
              <label>Project no<input value={projectNo} onChange={(e) => setProjectNo(e.target.value)} placeholder="DT-2026-00065" /></label>
              <label>Date<input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
            </div>
            <div className="pf-two">
              <label>Right side - line 1<input value={lines[0]} onChange={(e) => setLines([e.target.value, lines[1]])} placeholder="region (optional)" /></label>
              <label>Right side - line 2<input value={lines[1]} onChange={(e) => setLines([lines[0], e.target.value])} placeholder="optional" /></label>
            </div>
          </section>

          <section className="ws-card pf-card pf-secs">
            <div className="pf-step"><span>3</span><h2>Sections</h2><em>one red bar each</em></div>
            <div className="pf-sections">
              {sections.map((s) => {
                const count = items.filter((i) => i.sectionId === s.id).length;
                return (
                  <div className="pf-section" key={s.id}>
                    <input className="pf-sname" list="pf-board-types" value={s.name} onChange={(e) => editSection(s.id, { name: e.target.value })} aria-label="Section name" />
                    <input type="number" min="1" value={s.qty} placeholder={`QTY ${totals[s.id].qty}`} onChange={(e) => editSection(s.id, { qty: e.target.value })} aria-label="Section QTY" />
                    <input type="number" min="0" value={s.sqft} placeholder={`Sq.feet ${fmtSqft(totals[s.id].sqft)}`} onChange={(e) => editSection(s.id, { sqft: e.target.value })} aria-label="Section Sq.feet" />
                    <span className="pf-count" title="files in this section">{count} file{count === 1 ? "" : "s"}</span>
                    <button type="button" className="pf-icon" onClick={() => removeSection(s.id)} disabled={sections.length < 2} aria-label="Remove section"><Trash2 size={15} /></button>
                  </div>
                );
              })}
              <datalist id="pf-board-types">{BOARD_TYPES.map((b) => <option key={b} value={b} />)}</datalist>
            </div>
            <button type="button" className="pf-add" onClick={() => setSections((c) => [...c, newSection("")])}><Plus size={14} /> Add section</button>
            <p className="pf-note">Grey figures are calculated from the files (a file&apos;s qty multiplies both); type a number to override.</p>
          </section>
        </div>

        <section className="ws-card pf-card pf-files pf-col" ref={lock}>
          <div className="pf-step"><span>2</span><h2>Files</h2>{items.length > 0 && <em>{items.length} added</em>}</div>
          <div
            className={"pf-drop" + (drag ? " over" : "") + (items.length ? " compact" : "")}
            role="button"
            tabIndex={0}
            data-testid="pf-drop"
            onClick={() => input.current?.click()}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.current?.click(); } }}
            onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files); }}
          >
            {reading ? <Loader2 size={items.length ? 20 : 34} className="pf-spin" /> : <UploadCloud size={items.length ? 20 : 34} />}
            <b>{reading ? "Reading files..." : "Drop CDR or image files here, or click to browse"}</b>
            {!items.length && <span>Names like &quot;16 - 12 X 4 Feet - Nonlit - SHOP NAME.cdr&quot; fill the size, type and name, and file the card under its board type. A CDR shows its saved preview.</span>}
            <input
              ref={input}
              data-testid="pf-input"
              type="file"
              multiple
              accept=".cdr,image/*"
              hidden
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => { add(e.target.files); e.target.value = ""; }}
            />
          </div>

          {items.length > 0 && (
            <div className="pf-table-wrap">
              <table className="shops-table pf-table">
                <colgroup><col className="c-thumb" /><col className="c-no" /><col /><col className="c-num" /><col className="c-num" /><col className="c-unit" /><col className="c-no" /><col className="c-sec" /><col className="c-x" /></colgroup>
                <thead><tr><th></th><th>No</th><th>Name</th><th>W</th><th>H</th><th>Unit</th><th>Qty</th><th>Section</th><th></th></tr></thead>
                <tbody>
                  {items.map((i) => (
                    <tr key={i.id}>
                      <td className="pf-thumb" title={i.fileName}><Thumb url={urls.current.get(i.id)} /></td>
                      <td><input value={i.no} onChange={(e) => edit(i.id, { no: e.target.value })} aria-label="No" /></td>
                      <td><input value={i.name} onChange={(e) => edit(i.id, { name: e.target.value })} aria-label="Name" title={i.name} /></td>
                      <td><input type="number" min="0" step="any" value={i.width} onChange={(e) => edit(i.id, { width: e.target.value })} aria-label="Width" /></td>
                      <td><input type="number" min="0" step="any" value={i.height} onChange={(e) => edit(i.id, { height: e.target.value })} aria-label="Height" /></td>
                      <td>
                        <select value={i.unit} onChange={(e) => edit(i.id, { unit: e.target.value })} aria-label="Unit">
                          {["in", "ft", "cm", "mm"].map((u) => <option key={u}>{u}</option>)}
                        </select>
                      </td>
                      <td><input type="number" min="1" value={i.qty} placeholder="1" onChange={(e) => edit(i.id, { qty: e.target.value })} aria-label="Qty" /></td>
                      <td>
                        <select value={i.sectionId} onChange={(e) => edit(i.id, { sectionId: e.target.value })} aria-label="Section">
                          {sections.map((s, n) => <option key={s.id} value={s.id}>{s.name || `Section ${n + 1}`}</option>)}
                        </select>
                      </td>
                      <td><button type="button" className="pf-icon" onClick={() => remove(i.id)} aria-label={`Remove ${i.fileName}`}><Trash2 size={15} /></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

      </div>
      <footer className="ws-card pf-bar-foot">
        <div className="pf-sum-chips" title="Totals per section">
          {items.length === 0 ? <span className="pf-empty">No files yet - add files to see the totals.</span> : used.map((s) => (
            <span className="pf-chip" key={s.id}>
              <b>{s.name || "Untitled"}</b>
              {s.qty === "" ? totals[s.id].qty : s.qty} Nos · {s.sqft === "" ? fmtSqft(totals[s.id].sqft) : s.sqft} sq.ft
            </span>
          ))}
        </div>
        {items.length > 0 && <div className="pf-sum-total"><span>{items.length} file{items.length === 1 ? "" : "s"}</span><b>{sum.qty} Nos · {fmtSqft(sum.sqft)} sq.ft</b></div>}
        <div className="pf-format" role="radiogroup" aria-label="Output format" title="A4 portrait, 300 dpi. Extra rows move to the next page; several JPEG pages come as a zip.">
          <button type="button" role="radio" aria-checked={format === "pdf"} className={format === "pdf" ? "on" : ""} onClick={() => setFormat("pdf")} disabled={busy}><FileText size={15} /> PDF</button>
          <button type="button" role="radio" aria-checked={format === "jpeg"} className={format === "jpeg" ? "on" : ""} onClick={() => setFormat("jpeg")} disabled={busy}><FileImage size={15} /> JPEG</button>
        </div>
        {busy ? (
          <div className="pf-progress" role="status" aria-live="polite">
            <div className="pf-bar"><i className={stage === "rendering" ? "indeterminate" : ""} style={{ width: `${progress}%` }} /></div>
            <ol>
              {STAGES.map(([k, label], n) => (
                <li key={k} className={n < stageIdx ? "done" : n === stageIdx ? "now" : ""}>
                  {n < stageIdx ? <CheckCircle2 size={14} /> : n === stageIdx ? <Loader2 size={14} className="pf-spin" /> : <span className="pf-dot" />}
                  {label}{k === "uploading" && stage === "uploading" ? ` ${upPct}%` : ""}
                </li>
              ))}
            </ol>
          </div>
        ) : (
          <button type="button" className="btn-gradient pf-go" onClick={generate} data-testid="pf-generate">
            <Printer size={16} /> Generate print file
          </button>
        )}
        {error && <div className="pf-error" role="alert">{error}</div>}
        {done && !busy && <div className="pf-done" role="status"><FileUp size={16} /><span><b>Downloaded</b> {done}</span></div>}
      </footer>
    </div>
  );
}
