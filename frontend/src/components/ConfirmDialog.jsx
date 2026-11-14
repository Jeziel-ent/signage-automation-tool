import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { TriangleAlert } from "lucide-react";
import "./ConfirmDialog.css";

/** A plain confirmation box for actions that cannot be undone (replaces window.confirm, which cannot hold a longer warning).
 *  Escape or a click on the dim background cancels; focus starts on Cancel so a stray Enter never deletes anything. */
export default function ConfirmDialog({ title, children, confirmLabel = "Delete", busy = false, onConfirm, onCancel }) {
  const cancelRef = useRef(null);
  useEffect(() => {
    cancelRef.current?.focus();
    const onKey = (e) => e.key === "Escape" && !busy && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, busy]);

  return createPortal(
    <div className="cd-scrim" onMouseDown={(e) => e.target === e.currentTarget && !busy && onCancel()}>
      <div className="cd-box app-shell-dialog" role="alertdialog" aria-modal="true" aria-labelledby="cd-title">
        <div className="cd-head">
          <span className="cd-icon"><TriangleAlert size={18} /></span>
          <h3 id="cd-title">{title}</h3>
        </div>
        <div className="cd-body">{children}</div>
        <div className="cd-actions">
          <button type="button" className="ws-cta-ghost" ref={cancelRef} onClick={onCancel} disabled={busy}>Cancel</button>
          <button type="button" className="cd-danger" onClick={onConfirm} disabled={busy}>{busy ? "Deleting…" : confirmLabel}</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
