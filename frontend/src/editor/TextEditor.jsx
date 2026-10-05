import { useEffect, useRef, useState } from "react";

/**
 * Inline text editing: double-click a text object (or press F2 with one selected) and type.
 * The canvas image is CorelDRAW's render and cannot be re-typeset in the browser, so this is a
 * plain overlay editor - the change becomes a `text` operation, and the real result appears in the
 * file CorelDRAW exports (Save and Generate).
 *
 * Enter = new line, Ctrl+Enter or clicking away = apply, Esc = cancel.
 */
export default function TextEditor({ node, box, fonts, onApply, onCancel }) {
  const ref = useRef(null);
  const cancelled = useRef(false);
  const [value, setValue] = useState(node.text.content ?? "");

  useEffect(() => {
    const el = ref.current;
    el.focus();
    el.select();
  }, []);

  const font = node.text.font || "";
  const known = fonts?.available ? new Set(fonts.fonts.map((f) => f.toLowerCase())) : null;
  const missing = known && font && !known.has(font.toLowerCase());

  const finish = () => {
    if (cancelled.current) return;
    if (value !== (node.text.content ?? "")) onApply(value);
    else onCancel();
  };

  const width = Math.max(box.w, 240);
  const rows = Math.max(2, Math.min(8, value.split("\n").length + 1));
  return (
    <div
      className="ed-texteditor"
      style={{ left: Math.max(4, Math.min(box.x, window.innerWidth - width - 360)), top: Math.max(4, box.y - 30), width }}
      onPointerDown={(e) => e.stopPropagation()}
    >
      <div className="ed-texteditor-bar">
        <span>Editing text</span>
        <span className="ed-hint">Ctrl+Enter apply · Esc cancel</span>
      </div>
      <textarea
        ref={ref}
        value={value}
        rows={rows}
        spellCheck={false}
        style={{ fontFamily: `"${font}", "Nirmala UI", Arial, sans-serif` }}
        onChange={(e) => setValue(e.target.value)}
        onBlur={finish}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === "Escape") {
            cancelled.current = true;
            onCancel();
          } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            finish();
          }
        }}
      />
      {missing && (
        <div className="ed-warn ed-warn-error" role="alert">
          The font "{font}" is not installed on this machine - CorelDRAW will substitute another font for this text.
        </div>
      )}
    </div>
  );
}
