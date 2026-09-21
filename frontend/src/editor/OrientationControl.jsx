import { useState } from "react";
import { fromUnit } from "./units.js";

// Presets are defined in inches regardless of the editor's current display unit (fromUnit converts
// at click time) - these two numbers are the ones named in the task this control was built for.
const PRESETS = [
  { id: "landscape-90x30in", label: "Landscape (90 × 30 in)", w: 90, h: 30, unit: "in" },
  { id: "portrait-30x90in", label: "Portrait (30 × 90 in)", w: 30, h: 90, unit: "in" },
];

/**
 * Orientation / aspect-ratio preset selector for the editor toolbar. Purely presentational - it
 * converts whatever the user picked into millimetres and calls `onConvert(targetWmm, targetHmm,
 * label)`; EditorPage.jsx owns talking to the API and merging the result into the undo timeline.
 */
export default function OrientationControl({ unit, disabled, onConvert }) {
  const [choice, setChoice] = useState("");
  const [customW, setCustomW] = useState("");
  const [customH, setCustomH] = useState("");

  const applyPreset = (id) => {
    const preset = PRESETS.find((p) => p.id === id);
    if (preset) onConvert(fromUnit(preset.w, preset.unit), fromUnit(preset.h, preset.unit), preset.label);
  };

  const applyCustom = () => {
    const w = parseFloat(customW);
    const h = parseFloat(customH);
    if (!(w > 0) || !(h > 0)) return;
    onConvert(fromUnit(w, unit), fromUnit(h, unit), `${w} × ${h} ${unit}`);
    setCustomW("");
    setCustomH("");
  };

  return (
    <span className="ed-orientation">
      <select
        className="ed-select"
        aria-label="Orientation / aspect ratio preset"
        title="Re-lay the board out for a different orientation or aspect ratio - each product/text slot moves to a purpose-built zone of the new page (undo with Ctrl+Z)"
        value={choice}
        disabled={disabled}
        onChange={(e) => {
          const id = e.target.value;
          if (id === "custom") {
            setChoice("custom");
            return;
          }
          applyPreset(id);
          setChoice("");
        }}
      >
        <option value="" disabled>Orientation…</option>
        {PRESETS.map((p) => (
          <option key={p.id} value={p.id}>{p.label}</option>
        ))}
        <option value="custom">Custom dimensions…</option>
      </select>
      {choice === "custom" && (
        <span className="ed-orientation-custom">
          <input
            aria-label="Custom width"
            placeholder={`W (${unit})`}
            value={customW}
            onChange={(e) => setCustomW(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applyCustom()}
          />
          <span>×</span>
          <input
            aria-label="Custom height"
            placeholder={`H (${unit})`}
            value={customH}
            onChange={(e) => setCustomH(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applyCustom()}
          />
          <button className="btn ghost" disabled={disabled} onClick={applyCustom}>Convert</button>
          <button className="btn ghost" onClick={() => { setChoice(""); setCustomW(""); setCustomH(""); }}>Cancel</button>
        </span>
      )}
    </span>
  );
}
