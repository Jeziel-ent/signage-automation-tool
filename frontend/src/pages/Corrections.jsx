import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, Loader2, RotateCcw, Sparkles, X } from "lucide-react";
import { ACTION_LABEL, STATUS_LABEL, describeShift, describeStyle, diffRects, inTab, pageLabel, pickSelection } from "../utils/correctionsView.js";
import "./Corrections.css";

const TABS = [["pending", "Pending"], ["approved", "Approved"], ["rejected", "Rejected"], ["all", "All"]];

/** The page drawn to scale: dashed grey = where the engine put an object, solid red = where the designer put it. */
function DiffDrawing({ record }) {
  const geo = record.changes.map((c) => diffRects(c, record.page_w_mm, record.page_h_mm));
  const { width, height } = geo[0] ?? diffRects({}, record.page_w_mm, record.page_h_mm);
  return (
    <svg className="cr-draw" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Where each changed object was and where the designer put it">
      <rect className="cr-page" x="0" y="0" width={width} height={height} />
      {geo.map((g, i) => (
        <g key={record.changes[i].id}>
          {g.before && <rect className="cr-before" x={g.before.x} y={g.before.y} width={g.before.w} height={g.before.h} />}
          {g.after && <rect className="cr-after" x={g.after.x} y={g.after.y} width={g.after.w} height={g.after.h} />}
          {g.before && g.after && (
            <line className="cr-arrow" x1={g.before.x + g.before.w / 2} y1={g.before.y + g.before.h / 2}
              x2={g.after.x + g.after.w / 2} y2={g.after.y + g.after.h / 2} />
          )}
          {g.before && !g.after && (
            <path className="cr-gone" d={`M${g.before.x} ${g.before.y}l${g.before.w} ${g.before.h}M${g.before.x + g.before.w} ${g.before.y}l${-g.before.w} ${g.before.h}`} />
          )}
          <text className="cr-num" x={(g.after ?? g.before).x + 4} y={(g.after ?? g.before).y + 14}>{i + 1}</text>
        </g>
      ))}
    </svg>
  );
}

function ChangeList({ record }) {
  return (
    <ol className="cr-changes">
      {record.changes.map((c) => (
        <li key={c.id}>
          <b>{ACTION_LABEL[c.action] ?? c.action}</b> {c.kind === "text" ? "the text" : `a ${c.kind ?? "shape"}`}
          {c.nested && <span className="cr-inside"> inside a group</span>}{" "}
          <span className="cr-shift">{describeShift(c.shift)}</span>
          {c.style && <div className="cr-style">Text style: {describeStyle(c.style)}</div>}
          {!record.applies.includes(c.id) && <em> - not re-applied (only moves and resizes are learned)</em>}
        </li>
      ))}
      {record.text_edits > 0 && <li className="cr-skip">The text itself was edited {record.text_edits} time{record.text_edits === 1 ? "" : "s"} - names are never learned (they come from the sheet).</li>}
    </ol>
  );
}

function ListItem({ record, active, onPick }) {
  return (
    <button type="button" className={"cr-item" + (active ? " active" : "")} onClick={() => onPick(record.id)}>
      <span className="cr-item-title">{record.title}</span>
      <span className="cr-item-sub">{pageLabel(record.page_w_mm, record.page_h_mm)} · {record.changes.length} change{record.changes.length === 1 ? "" : "s"}</span>
      <span className={"cr-pill " + record.status}>{STATUS_LABEL[record.status]}</span>
    </button>
  );
}

function ActionButton({ record, to, icon, label, cls, busy, onSet }) {
  return <button type="button" className={"cr-btn " + cls} disabled={busy} onClick={() => onSet(record.id, to)}>{icon}{label}</button>;
}

function Detail({ record, busy, onSet }) {
  const props = { record, busy, onSet };
  return (
    <div className="cr-detail">
      <header>
        <div>
          <h2>{record.title}</h2>
          <p>{record.brand} · {record.master_file} · {pageLabel(record.page_w_mm, record.page_h_mm)}{record.board_type ? ` · ${record.board_type}` : ""}</p>
          <p>{record.source === "designer-dataset" ? "Learned from a designer's own file" : "Saved from the editor"}</p>
        </div>
        <span className={"cr-pill " + record.status}>{STATUS_LABEL[record.status]}</span>
      </header>
      <div className="cr-body">
        <DiffDrawing record={record} />
        <ChangeList record={record} />
        <p className="cr-legend"><i className="cr-key before" /> engine <i className="cr-key after" /> designer</p>
      </div>
      <footer>
        {record.status !== "approved" && <ActionButton {...props} to="approved" cls="ok" icon={<Check size={15} />} label="Approve" />}
        {record.status !== "rejected" && <ActionButton {...props} to="rejected" cls="no" icon={<X size={15} />} label="Reject" />}
        {record.status !== "pending" && <ActionButton {...props} to="pending" cls="plain" icon={<RotateCcw size={15} />} label="Back to pending" />}
        <span className="cr-note">Only approved corrections are applied to later boards of this size.</span>
      </footer>
    </div>
  );
}

export default function Corrections() {
  const [records, setRecords] = useState([]);
  const [counts, setCounts] = useState({ pending: 0, approved: 0, rejected: 0 });
  const [tab, setTab] = useState("pending");
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/v2/corrections");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setRecords(d.corrections);
      setCounts(d.counts);
      setError("");
    } catch (e) {
      setError(`Could not load the corrections: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const shown = useMemo(() => records.filter((r) => inTab(r, tab)), [records, tab]);
  const current = pickSelection(records, tab, selected);
  const record = records.find((r) => r.id === current) ?? null;

  async function setStatus(id, status) {
    setBusy(true);
    setError("");
    try {
      const r = await fetch(`/api/v2/corrections/${encodeURIComponent(id)}/status`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await load();
    } catch (e) {
      setError(`Could not save the decision: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="cr-page">
      <header className="cr-top">
        <div className="cr-top-icon"><Sparkles size={20} /></div>
        <div>
          <h1>Corel Intelligence - review</h1>
          <p>Approve what designers fixed in the editor. Only approved corrections are applied to later boards.</p>
        </div>
      </header>
      {error && <div className="cr-error" role="alert">{error}</div>}
      <div className="cr-layout">
        <aside className="cr-list">
          <div className="cr-tabs" role="tablist">
            {TABS.map(([k, label]) => (
              <button key={k} type="button" role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
                {label}{k !== "all" && <span className="cr-count">{counts[k]}</span>}
              </button>
            ))}
          </div>
          <div className="cr-items">
            {loading && <p className="cr-empty"><Loader2 size={16} className="cr-spin" /> Loading...</p>}
            {!loading && !shown.length && <p className="cr-empty">Nothing in this tab.</p>}
            {shown.map((r) => <ListItem key={r.id} record={r} active={r.id === current} onPick={setSelected} />)}
          </div>
        </aside>
        <section className="cr-main">
          {record ? <Detail record={record} busy={busy} onSet={setStatus} /> : <p className="cr-empty">Pick a correction to review it.</p>}
        </section>
      </div>
    </div>
  );
}
