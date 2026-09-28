import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import { useOnClickOutside } from "../hooks/useOnClickOutside.js";

/**
 * A searchable font picker (replaces a native <select>, whose OS-drawn popup ignores the dark theme and can't be searched):
 * a trigger showing the chosen font in its own face, and a dark glass popover with a search box and the fonts - each drawn
 * in itself - with a red left accent on the active / selected one. Keyboard: Enter/Space/ArrowDown opens; in the menu
 * ArrowUp/Down/Home/End move, Enter picks, Esc closes the MENU only (stopped before the dialog's own Esc handler), Tab
 * closes. `fonts` is a list of family names; `value` the chosen one.
 */
export default function FontCombobox({ fonts, value, onChange, disabled = false, label }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const rootRef = useRef(null);
  const listRef = useRef(null);
  const searchRef = useRef(null);
  const triggerRef = useRef(null);
  const id = useId();

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? fonts.filter((f) => f.toLowerCase().includes(q)) : fonts;
  }, [fonts, query]);

  // scroll ONLY the list (scrollIntoView would also scroll the dialog and the editor's overflow-hidden layout)
  const scrollToIndex = (i, center) => {
    const list = listRef.current;
    const el = list?.querySelector(`[data-index="${i}"]`);
    if (!list || !el) return;
    const top = el.offsetTop, bottom = top + el.offsetHeight;
    if (center) list.scrollTop = top - (list.clientHeight - el.offsetHeight) / 2;
    else if (top < list.scrollTop) list.scrollTop = top;
    else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
  };

  const close = (refocus = true) => {
    setOpen(false);
    setQuery("");
    if (refocus) triggerRef.current?.focus();
  };
  useOnClickOutside(rootRef, () => close(false), open);

  // opening: focus the search box and start on the chosen font
  useEffect(() => {
    if (!open) return;
    const i = Math.max(0, fonts.indexOf(value));
    setActive(i);
    requestAnimationFrame(() => {
      searchRef.current?.focus({ preventScroll: true });
      scrollToIndex(i, true);
    });
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // typing resets the highlight to the first match
  useEffect(() => { setActive(0); }, [query]);

  // keep the highlighted option in view while arrowing
  useEffect(() => {
    if (open) scrollToIndex(active, false);
  }, [active, open]);

  const pick = (f) => {
    onChange(f);
    close();
  };

  function onMenuKey(e) {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation(); // the dialog closes on Esc too: this Esc is only for the menu
      close();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(shown.length - 1, a + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === "Home") {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setActive(Math.max(0, shown.length - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (shown[active]) pick(shown[active]);
    } else if (e.key === "Tab") {
      close(false);
    }
  }

  function onTriggerKey(e) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setOpen(true);
    }
  }

  const listId = `${id}-list`;
  return (
    <div className="fcb" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className={"fcb-trigger" + (open ? " open" : "")}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onTriggerKey}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={label ? `${label}: ${value || "none"}` : undefined}
      >
        <span className="fcb-value" style={value ? { fontFamily: `"${value}", sans-serif` } : undefined}>{value || "Choose a font"}</span>
        <ChevronDown size={16} className="fcb-chevron" aria-hidden="true" />
      </button>

      {open && (
        <div className="fcb-menu" role="presentation" onKeyDown={onMenuKey}>
          <div className="fcb-search">
            <Search size={14} aria-hidden="true" />
            <input
              ref={searchRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={`Search ${fonts.length} fonts...`}
              role="combobox"
              aria-expanded="true"
              aria-controls={listId}
              aria-autocomplete="list"
              aria-activedescendant={shown[active] ? `${id}-opt-${active}` : undefined}
              spellCheck={false}
              autoComplete="off"
            />
          </div>
          <ul className="fcb-list font-dropdown-list" id={listId} role="listbox" ref={listRef} aria-label={label || "Fonts"}>
            {shown.map((f, i) => (
              <li
                key={f}
                id={`${id}-opt-${i}`}
                data-index={i}
                role="option"
                aria-selected={f === value}
                className={"fcb-option" + (i === active ? " active" : "") + (f === value ? " selected" : "")}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => e.preventDefault()} /* keep focus in the search box */
                onClick={() => pick(f)}
              >
                <span className="fcb-name" style={{ fontFamily: `"${f}", sans-serif` }}>{f}</span>
                {f === value && <Check size={14} className="fcb-check" aria-hidden="true" />}
              </li>
            ))}
            {!shown.length && <li className="fcb-empty">No font matches "{query}"</li>}
          </ul>
        </div>
      )}
    </div>
  );
}
