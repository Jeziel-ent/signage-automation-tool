import { useEffect, useState } from "react";
import { defaultReplacement } from "./fontSubs.js";
import FontCombobox from "./FontCombobox.jsx";

/**
 * "Missing Font Detected": a font the board's text uses is neither installed on this server nor on Google Fonts. The
 * person picks an installed replacement and whether it is Temporary (this editor tab only - the canvas draws that text in
 * the replacement) or Permanent (saved for this shop; every export sets that text's font in CorelDRAW). The list is the
 * SERVER's installed fonts, because CorelDRAW on the server writes the exported files and ignores a font it does not have.
 */
export default function FontSubstituteModal({ font, webSource, position, total, sample, installed, current, onApply, onCancel, onRemove }) {
  const [choice, setChoice] = useState(() => defaultReplacement(installed, current && current.font));
  const [permanent, setPermanent] = useState(current ? !!current.permanent : false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !busy) onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);

  async function apply() {
    if (!choice) return;
    setBusy(true);
    setError("");
    try {
      await onApply(choice, permanent);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  return (
    <div className="ed-modal-back" role="dialog" aria-modal="true" aria-labelledby="fs-title">
      <div className="ed-modal ed-fontsub">
        <h2 id="fs-title">
          <span className="ed-fontsub-badge" aria-hidden="true">!</span> {webSource ? "Font Not On The Server" : "Missing Font Detected"}
          {total > 1 && <span className="ed-fontsub-count">{position} of {total}</span>}
        </h2>
        {webSource ? (
          <p>
            <strong>{font}</strong> was found online ({webSource}) and the editor shows it, but it is not installed on this server -
            CorelDRAW, which writes the exports, would substitute it on its own. Choose the replacement exports should use.
          </p>
        ) : (
          <p>
            <strong>{font}</strong> is used by this board but was not found anywhere: not installed on this server, not on Google
            Fonts, not on Fontsource. CorelDRAW substitutes it on its own when it exports, and the preview here shows that substitute.
          </p>
        )}

        <div className="ed-fontsub-field">
          <span id="fs-font-label">Select Replacement Font</span>
          <FontCombobox fonts={installed} value={choice} onChange={setChoice} disabled={busy || !installed.length} label="Replacement font" />
        </div>
        {!installed.length && <div className="ed-warn">The server's font list is not available, so there is nothing to choose from.</div>}
        {choice && (
          <div className="ed-fontsub-sample" style={{ fontFamily: `"${choice}", sans-serif` }} title="Preview in the replacement font">
            {(sample || font).slice(0, 80)}
          </div>
        )}

        <fieldset className="ed-fontsub-duration" disabled={busy}>
          <legend>Substitution Duration</legend>
          <label>
            <input type="radio" name="fs-duration" checked={!permanent} onChange={() => setPermanent(false)} />
            <span><strong>Temporary (This Session Only)</strong> - redraws the text in this editor tab; exports are unchanged.</span>
          </label>
          <label>
            <input type="radio" name="fs-duration" checked={permanent} onChange={() => setPermanent(true)} />
            <span><strong>Permanent (Save to Shop Config)</strong> - saved for this shop: every export of this board sets the text to the replacement in CorelDRAW.</span>
          </label>
        </fieldset>
        {current && current.permanent && !permanent && (
          <p className="ed-hint">Choosing Temporary removes the saved (permanent) substitution for this shop.</p>
        )}
        <p className="ed-hint">The canvas draws that text live in the replacement (browser typesetting - close to, not identical with, CorelDRAW's).</p>

        {error && <div className="ed-warn ed-warn-error">{error}</div>}
        <div className="ed-modal-actions">
          {current && onRemove && (
            <button className="ed-btn" onClick={onRemove} disabled={busy} style={{ marginRight: "auto" }}>Remove substitution</button>
          )}
          <button className="ed-btn" onClick={onCancel} disabled={busy}>Cancel / Use System Default</button>
          <button className="btn" onClick={apply} disabled={busy || !choice}>{busy ? "Applying..." : "Apply Substitution"}</button>
        </div>
      </div>
    </div>
  );
}
