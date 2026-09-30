import { useEffect, useMemo, useState } from "react";
import { AlignCenter, AlignJustify, AlignLeft, AlignRight } from "lucide-react";
import { CHAR_SPACING_RANGE, LINE_SPACING_RANGE, buildIndex } from "./ops.js";
import { insidePowerclip, unionBox } from "./model.js";
import { fromUnit, toUnit, UNITS } from "./units.js";

const FONTS = ["Arial", "Nirmala UI", "Yu Gothic Medium", "Segoe UI", "Times New Roman", "Calibri", "Verdana"];

const fmtField = (mm, unit) => {
  const s = toUnit(mm, unit).toFixed(UNITS[unit].decimals);
  return s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s;
};

/** Right panel 1: size/position of the selection (centre reference point, like CorelDRAW's default), stacking order, text. */
export default function PropertiesPanel({ scene, sel, unit, onCommit, fonts, onTextPreview }) {
  const idx = useMemo(() => buildIndex(scene), [scene]);
  const nodes = useMemo(() => sel.map((id) => idx.get(id)?.node).filter(Boolean), [sel, idx]);
  const box = useMemo(() => unionBox(nodes), [nodes]);
  const single = nodes.length === 1 ? nodes[0] : null;
  const locked = nodes.some((n) => n.locked || idx.get(n.id).layer.locked);
  // Inside a PowerClip `text`, `move`, `resize` and `order` are accepted (ops.js checkEditable): the
  // stacking buttons restack a clipped object among the clip's own contents. Only a lock disables them.
  const inClip = nodes.some((n) => insidePowerclip(idx, n.id));
  const geomLocked = locked;
  const orderLocked = locked;

  const [f, setF] = useState({ w: "", h: "", x: "", y: "" });
  const [lockRatio, setLockRatio] = useState(true);
  const [tab, setTab] = useState("dimensions");
  useEffect(() => {
    if (!box) return;
    setF({
      w: fmtField(box.w, unit),
      h: fmtField(box.h, unit),
      x: fmtField(box.x + box.w / 2, unit),
      y: fmtField(box.y + box.h / 2, unit),
    });
  }, [box && box.x, box && box.y, box && box.w, box && box.h, unit]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!nodes.length) {
    return (
      <section className="ed-panel">
        <h3>Properties</h3>
        <p className="ed-empty">Nothing selected. Click an object on the canvas or in the layers list.</p>
      </section>
    );
  }

  const parse = (s) => {
    const v = parseFloat(s);
    return Number.isFinite(v) ? fromUnit(v, unit) : null;
  };

  function commit(field) {
    const v = parse(f[field]);
    if (v === null) return setF((p) => ({ ...p, [field]: fmtField(box[field === "x" || field === "y" ? field : field], unit) }));
    let { w, h } = box;
    let cx = box.x + box.w / 2;
    let cy = box.y + box.h / 2;
    if (field === "w") {
      w = Math.max(v, 0.01);
      if (lockRatio) h = (box.h * w) / box.w;
    } else if (field === "h") {
      h = Math.max(v, 0.01);
      if (lockRatio) w = (box.w * h) / box.h;
    } else if (field === "x") cx = v;
    else cy = v;
    const to = { x: cx - w / 2, y: cy - h / 2, w, h };
    const same = (a, b) => Math.abs(a - b) < 1e-4;
    if (same(w, box.w) && same(h, box.h)) {
      if (same(to.x, box.x) && same(to.y, box.y)) return;
      onCommit({ op: "move", ids: sel, dx: to.x - box.x, dy: to.y - box.y });
    } else {
      onCommit({ op: "resize", ids: sel, from: { ...box }, to });
    }
  }

  const input = (field, label) => (
    <label className="ed-field">
      <span>{label}</span>
      <input
        value={f[field]}
        disabled={geomLocked}
        onChange={(e) => setF((p) => ({ ...p, [field]: e.target.value }))}
        onBlur={() => commit(field)}
        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
        inputMode="decimal"
      />
    </label>
  );

  const order = (mode) => onCommit({ op: "order", id: single.id, mode });
  const hasText = !!(single && single.text);
  const active = tab === "text" && !hasText ? "dimensions" : tab;
  const tabs = [
    ["dimensions", "Dimensions"],
    ["position", "Position"],
    ["text", "Text"],
  ];

  return (
    <section className="ed-panel ed-props">
      <h3>
        Properties <span className="ed-unit-tag">{unit}</span>
      </h3>
      <div className="ed-prop-title">
        {single ? (single.name || single.type) : `${nodes.length} objects`}
        {single && <span className="ed-prop-sub"> · {single.kind === "shape" ? single.type : single.kind}</span>}
        {locked && <span className="ed-prop-sub"> · locked</span>}
        {inClip && <span className="ed-prop-sub"> · in PowerClip</span>}
      </div>

      <div className="ed-tabs" role="tablist">
        {tabs.map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={active === id}
            className={`ed-tab${active === id ? " active" : ""}`}
            disabled={id === "text" && !hasText}
            title={id === "text" && !hasText ? "Select a text object to edit its text" : undefined}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="ed-tabbody">
        {active === "dimensions" && (
          <>
            <div className="ed-grid2">
              {input("w", "Width")}
              {input("h", "Height")}
            </div>
            <label className="ed-check">
              <input type="checkbox" checked={lockRatio} disabled={geomLocked} onChange={(e) => setLockRatio(e.target.checked)} /> Keep proportions
            </label>
            {inClip && <div className="ed-hint">Inside a PowerClip - resizing or moving this changes the clipped result; the preview image refreshes after Save and Generate.</div>}
            {single && single.rotation ? <div className="ed-hint">Rotation {single.rotation}° (already in the rendered image)</div> : null}
          </>
        )}

        {active === "position" && (
          <>
            <div className="ed-grid2">
              {input("x", "X")}
              {input("y", "Y")}
            </div>
            <div className="ed-hint">Centre of the selection; origin is the page's bottom-left corner.</div>
            {inClip && single && <div className="ed-hint">Inside a PowerClip - the order buttons restack it among the clip's contents only.</div>}
            {single ? (
              <div className="ed-order">
                <span>Order</span>
                <button className="ed-btn" disabled={orderLocked} onClick={() => order("front")}>To front</button>
                <button className="ed-btn" disabled={orderLocked} onClick={() => order("forward")}>Forward</button>
                <button className="ed-btn" disabled={orderLocked} onClick={() => order("backward")}>Backward</button>
                <button className="ed-btn" disabled={orderLocked} onClick={() => order("back")}>To back</button>
              </div>
            ) : (
              <div className="ed-hint">Select a single object to change its stacking order.</div>
            )}
          </>
        )}

        {active === "text" && hasText && (
          <TextFields key={single.id} node={single} locked={locked} onCommit={onCommit} fonts={fonts} onTextPreview={onTextPreview} />
        )}
      </div>
    </section>
  );
}

function TextFields({ node, locked, onCommit, fonts, onTextPreview }) {
  const known = fonts && fonts.available ? new Set(fonts.fonts.map((f) => f.toLowerCase())) : null;
  const installed = (name) => !known || known.has(name.toLowerCase());
  const [fontError, setFontError] = useState("");
  const t = node.text;
  const [content, setContent] = useState(t.content ?? "");
  const [font, setFont] = useState(t.font ?? "");
  const [size, setSize] = useState(t.size_pt != null ? String(+t.size_pt.toFixed(2)) : "");
  useEffect(() => {
    setContent(t.content ?? "");
    setFont(t.font ?? "");
    setSize(t.size_pt != null ? String(+t.size_pt.toFixed(2)) : "");
  }, [t.content, t.font, t.size_pt]);

  const push = (patch) => onCommit({ op: "text", id: node.id, ...patch });
  // Live, uncommitted preview shown on the canvas as the user types or picks a
  // font - see Canvas.jsx's textPreview rendering. Always carries BOTH the
  // current content and font (whichever field didn't just change keeps its
  // latest typed/picked value, not the last-committed one) so editing one
  // field doesn't revert the other's in-progress preview.
  const preview = (patch) => onTextPreview && onTextPreview({ id: node.id, content, font, ...patch });
  const clearPreview = () => onTextPreview && onTextPreview(null);
  return (
    <div className="ed-text">
      <label className="ed-field wide">
        <span>Text</span>
        <textarea
          rows={2}
          value={content}
          disabled={locked}
          onChange={(e) => {
            const v = e.target.value;
            setContent(v);
            preview({ content: v });
          }}
          onBlur={() => {
            clearPreview();
            if (content !== t.content) push({ content });
          }}
        />
      </label>
      <div className="ed-grid2">
        <label className="ed-field">
          <span>Font</span>
          <input list="ed-fonts" value={font} disabled={locked} onChange={(e) => {
            const v = e.target.value;
            setFont(v);
            // Immediate, uncommitted live preview - the canvas can't re-typeset CorelDRAW's
            // own render, but it can overlay the shape's text in the chosen font (an actual
            // SVG <text> with that font-family) so a pick is seen right away, not only after
            // blur commits the real `text` op.
            preview({ font: v });
          }} onBlur={() => {
            clearPreview();
            if (!font || font === t.font) return setFontError("");
            if (!installed(font)) {
              setFontError(`"${font}" is not installed on this machine - CorelDRAW would silently ignore it and keep the old font. Pick an installed font.`);
              return;
            }
            setFontError("");
            push({ font });
          }} onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()} />
          <datalist id="ed-fonts">{(fonts && fonts.available ? fonts.fonts : FONTS).map((n) => <option key={n} value={n} />)}</datalist>
        </label>
        <label className="ed-field">
          <span>Size pt</span>
          <input value={size} disabled={locked} inputMode="decimal" onChange={(e) => setSize(e.target.value)} onBlur={() => { const v = parseFloat(size); if (v > 0 && v !== t.size_pt) push({ size_pt: v }); }} onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()} />
        </label>
      </div>
      <TextFormat t={t} locked={locked} push={push} />
      {fontError && <div className="ed-warn ed-warn-error">{fontError}</div>}
      {!fontError && t.font && !installed(t.font) && (
        <div className="ed-warn">This board's font "{t.font}" is not installed here - CorelDRAW is substituting another one.</div>
      )}
      {node.stale && <div className="ed-hint">Live preview active - Save and Generate will re-render this exactly through CorelDRAW.</div>}
    </div>
  );
}

const ALIGNS = [
  ["left", AlignLeft, "Align left"],
  ["center", AlignCenter, "Align centre"],
  ["right", AlignRight, "Align right"],
  ["justify", AlignJustify, "Justify"],
];

/** A spacing field: commits on blur / Enter when the number is valid and changed; out of range -> an inline message, no op. */
function SpacingField({ label, value, fallback, range, unitLabel, locked, onSet, title }) {
  const shown = value != null ? String(+Number(value).toFixed(2)) : "";
  const [v, setV] = useState(shown);
  const [err, setErr] = useState("");
  useEffect(() => setV(shown), [shown]);
  const apply = () => {
    if (v.trim() === "" || v === shown) return setErr("");
    const n = Number(v);
    if (!Number.isFinite(n) || n < range[0] || n > range[1]) {
      setErr(`${range[0]} to ${range[1]}`);
      return;
    }
    setErr("");
    onSet(n);
  };
  return (
    <label className="ed-field" title={title}>
      <span>{label}</span>
      <span className="ed-fmt-num">
        <input value={v} placeholder={String(fallback)} disabled={locked} inputMode="decimal" aria-label={label}
          onChange={(e) => setV(e.target.value)} onBlur={apply} onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()} />
        <em>{unitLabel}</em>
      </span>
      {err && <small className="ed-fmt-err">{err}</small>}
    </label>
  );
}

/** Bold / italic / underline, alignment, line and letter spacing of the selected text. Every change is one `text` op (undoable,
 *  autosaved, replayed through CorelDRAW's Story on export); the canvas previews it live. A value the board's CorelDRAW text could
 *  not report as one number (mixed runs, scenes exported before this existed) shows as unset. */
function TextFormat({ t, locked, push }) {
  return (
    <div className="ed-textfmt">
      <div className="ed-fmt-row">
        <div className="ed-seg" role="group" aria-label="Text style">
          {[["bold", "B", "Bold"], ["italic", "I", "Italic"], ["underline", "U", "Underline"]].map(([key, glyph, name]) => (
            <button key={key} type="button" className={"ed-seg-btn fmt-" + key + (t[key] ? " on" : "")} aria-pressed={!!t[key]} title={name}
              aria-label={name} disabled={locked} onClick={() => push({ [key]: !t[key] })}>
              {glyph}
            </button>
          ))}
        </div>
        <div className="ed-seg" role="group" aria-label="Alignment">
          {ALIGNS.map(([key, Icon, name]) => (
            <button key={key} type="button" className={"ed-seg-btn" + (t.align === key ? " on" : "")} aria-pressed={t.align === key}
              title={name} aria-label={name} disabled={locked} onClick={() => t.align !== key && push({ align: key })}>
              <Icon size={14} />
            </button>
          ))}
        </div>
      </div>
      <div className="ed-grid2">
        <SpacingField label="Line spacing" value={t.line_spacing} fallback={100} range={LINE_SPACING_RANGE} unitLabel="%" locked={locked}
          title="Distance between lines, % of the character height (CorelDRAW's default 100%)" onSet={(n) => push({ line_spacing: n })} />
        <SpacingField label="Letter spacing" value={t.char_spacing} fallback={0} range={CHAR_SPACING_RANGE} unitLabel="%" locked={locked}
          title="Extra space between characters, % of a space's width (CorelDRAW's default 0%)" onSet={(n) => push({ char_spacing: n })} />
      </div>
    </div>
  );
}
