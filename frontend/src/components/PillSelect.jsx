import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown } from "lucide-react";
import { useOnClickOutside } from "../hooks/useOnClickOutside.js";

/** Generic pill dropdown (same look/behaviour as BrandSelect, for {value, label} options with an "all" first entry).
 *  The menu is rendered in a portal on document.body (position: fixed, placed from the trigger's rect), so no ancestor's
 *  stacking context, overflow or backdrop-filter can put it behind - or clip it against - the table below.
 *  Keyboard: ArrowDown opens, Arrow keys move, Enter picks, Escape closes; an outside press closes. */
export default function PillSelect({ value, options, onChange, icon: Icon, label }) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const closeOutside = useCallback(() => setOpen(false), []);
  useOnClickOutside([rootRef, menuRef], closeOutside, open);

  // place the menu under the trigger; keep it in view horizontally; follow scroll/resize while open
  const MENU_W = 256;
  const place = useCallback(() => {
    const r = triggerRef.current?.getBoundingClientRect();
    if (!r) return;
    setPos({ top: r.bottom + 8, left: Math.max(8, Math.min(r.left, window.innerWidth - MENU_W - 8)) });
  }, []);
  useLayoutEffect(() => {
    if (!open) return undefined;
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, place]);

  const current = options.find((o) => o.value === value) || options[0];
  useEffect(() => {
    if (open) setActive(Math.max(0, options.findIndex((o) => o.value === value)));
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  function close() {
    setOpen(false);
    triggerRef.current?.focus();
  }
  function pick(o) {
    onChange(o.value);
    close();
  }
  function onKeyDown(e) {
    if (!open) {
      if (e.target === triggerRef.current && e.key === "ArrowDown") {
        e.preventDefault();
        setOpen(true);
      }
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(options.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      pick(options[active]);
    }
  }

  return (
    <div className="brand-select" ref={rootRef} onKeyDown={onKeyDown}>
      <button
        type="button"
        ref={triggerRef}
        className="brand-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={label}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="brand-trigger-label">
          {Icon && <Icon size={16} />}
          <span>{current.label}</span>
        </span>
        <ChevronDown size={16} className={"brand-chevron" + (open ? " open" : "")} aria-hidden="true" />
      </button>
      {open &&
        createPortal(
          <div ref={menuRef} className="brand-menu portal" style={{ position: "fixed", top: pos.top, left: pos.left }} role="presentation">
            <ul role="listbox" aria-label={label} className="brand-list">
              {options.map((o, i) => (
                <li
                  key={o.value}
                  role="option"
                  aria-selected={o.value === value}
                  className={"brand-item" + (o.value === value ? " selected" : "") + (i === active ? " active" : "")}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => pick(o)}
                >
                  <span>{o.label}</span>
                  {o.value === value && <Check size={16} />}
                </li>
              ))}
            </ul>
          </div>,
          document.body,
        )}
    </div>
  );
}
