import { Clock, Moon, Sun } from "lucide-react";
import { useTheme } from "../utils/theme.js";
import "./ThemeToggle.css";

const OPTIONS = [
  ["auto", "Auto", Clock, "Light from 6 am to 6 pm, dark at night"],
  ["dark", "Dark", Moon, "Always dark"],
  ["light", "Light", Sun, "Always light"],
];

/** Theme switch. variant "segmented" (welcome screen): Auto (follows the clock) / Dark / Light; "compact" (sidebar): one button that flips dark <-> light. */
export default function ThemeToggle({ variant = "segmented" }) {
  const { theme, choice, setChoice, toggle } = useTheme();
  if (variant === "compact") {
    const toLight = theme === "dark";
    return (
      <button type="button" className="sidebar-toggle" onClick={toggle} data-tip={toLight ? "Light mode" : "Dark mode"}
        aria-label={toLight ? "Switch to light mode" : "Switch to dark mode"}>
        {toLight ? <Sun size={18} /> : <Moon size={18} />}
        <span className="sidebar-label">{toLight ? "Light mode" : "Dark mode"}</span>
      </button>
    );
  }
  return (
    <div className="tt-seg" role="radiogroup" aria-label="Colour theme">
      {OPTIONS.map(([id, label, Icon, tip]) => (
        <button key={id} type="button" role="radio" aria-checked={choice === id} title={tip} className={"tt-opt" + (choice === id ? " on" : "")} onClick={() => setChoice(id)}>
          <Icon size={15} aria-hidden="true" /> {label}
        </button>
      ))}
    </div>
  );
}
