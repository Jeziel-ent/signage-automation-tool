import { useCallback, useEffect, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { ACTION_LABEL, describeShift, describeStyle, diffRects, pageLabel, pickSelection } from "../utils/correctionsView.js";
import "./Corrections.css";

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
    </button>
  );
}

function Detail({ record }) {
  return (
    <div className="cr-detail">
      <header>
        <div>
          <h2>{record.title}</h2>
          <p>{record.brand} · {record.master_file} · {pageLabel(record.page_w_mm, record.page_h_mm)}{record.board_type ? ` · ${record.board_type}` : ""}</p>
          <p>{record.source === "designer-dataset" ? "Learned from a designer's own file" : "Saved from the editor"}</p>
        </div>
      </header>
      <div className="cr-body">
        <DiffDrawing record={record} />
        <ChangeList record={record} />
        <p className="cr-legend"><i className="cr-key before" /> engine <i className="cr-key after" /> designer</p>
      </div>
      <footer>
        <span className="cr-note">Every correction is stored and applied to later boards of this size - nothing to approve.</span>
      </footer>
    </div>
  );
}

export default function Corrections() {
  const [records, setRecords] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/v2/corrections");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setRecords((await r.json()).corrections);
      setError("");
    } catch (e) {
      setError(`Could not load the corrections: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const current = pickSelection(records, selected);
  const record = records.find((r) => r.id === current) ?? null;

  return (
    <div className="cr-page">
      <header className="cr-top">
        <div className="cr-top-icon"><Sparkles size={20} /></div>
        <div>
          <h1>Corel Intelligence - what it has learned</h1>
          <p>Everything designers fixed in the editor, and what was learned from their own files. All of it is applied to later boards of the same size.</p>
        </div>
      </header>
      {error && <div className="cr-error" role="alert">{error}</div>}
      <div className="cr-layout">
        <aside className="cr-list">
          <div className="cr-items">
            {loading && <p className="cr-empty"><Loader2 size={16} className="cr-spin" /> Loading...</p>}
            {!loading && !records.length && <p className="cr-empty">Nothing learned yet - correct a board in the editor and save.</p>}
            {records.map((r) => <ListItem key={r.id} record={r} active={r.id === current} onPick={setSelected} />)}
          </div>
        </aside>
        <section className="cr-main">
          {record ? <Detail record={record} /> : <p className="cr-empty">Pick a correction to see what it changed.</p>}
        </section>
      </div>
    </div>
  );
}
