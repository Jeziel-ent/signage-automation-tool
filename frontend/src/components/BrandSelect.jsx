import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Building2, Check, ChevronDown } from "lucide-react";
import { useOnClickOutside } from "../hooks/useOnClickOutside.js";

const SEARCH_MIN_OPTIONS = 5; // a search box only earns its space with a longer list

/** Custom brand dropdown (replaces the native <select>, whose OS-styled menu ignored the app's styling).
 *  Keyboard: Enter/Space/ArrowDown open, ArrowUp/Down move, Enter picks, Escape closes; click outside closes. */
export default function BrandSelect({ value, options, onChange, placeholder = "Select brand…" }) {
  const [isBrandOpen, setIsBrandOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  const searchRef = useRef(null);

  const closeOutside = useCallback(() => setIsBrandOpen(false), []);
  useOnClickOutside(rootRef, closeOutside, isBrandOpen);

  const showSearch = options.length >= SEARCH_MIN_OPTIONS;
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? options.filter((o) => o.toLowerCase().includes(q)) : options;
  }, [options, query]);

  useEffect(() => {
    if (!isBrandOpen) return;
    setQuery("");
    setActive(Math.max(0, options.indexOf(value)));
    if (showSearch) searchRef.current?.focus();
  }, [isBrandOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  function close(refocus = true) {
    setIsBrandOpen(false);
    if (refocus) triggerRef.current?.focus();
  }
  function pick(o) {
    onChange(o);
    close();
  }
  function onKeyDown(e) {
    if (!isBrandOpen) {
      if (e.target === triggerRef.current && ["ArrowDown"].includes(e.key)) {
        e.preventDefault();
        setIsBrandOpen(true);
      }
      return; // Enter/Space on the trigger button already click it
    }
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(filtered.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter" && filtered[active]) {
      e.preventDefault();
      pick(filtered[active]);
    }
  }

  return (
    <div className="brand-select" ref={rootRef} onKeyDown={onKeyDown}>
      <button
        type="button"
        ref={triggerRef}
        className="brand-trigger"
        aria-haspopup="listbox"
        aria-expanded={isBrandOpen}
        aria-label="Brand"
        onClick={() => setIsBrandOpen((o) => !o)}
      >
        <span className="brand-trigger-label">
          <Building2 size={16} />
          <span className={value ? "" : "placeholder"}>{value || placeholder}</span>
        </span>
        <ChevronDown size={16} className={"brand-chevron" + (isBrandOpen ? " open" : "")} aria-hidden="true" />
      </button>
      {isBrandOpen && (
        <div className="brand-menu" role="presentation">
          {showSearch && (
            <input
              ref={searchRef}
              className="brand-search"
              placeholder="Search brands…"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setActive(0);
              }}
            />
          )}
          <ul role="listbox" aria-label="Brands" className="brand-list">
            {filtered.map((o, i) => (
              <li
                key={o}
                role="option"
                aria-selected={o === value}
                className={"brand-item" + (o === value ? " selected" : "") + (i === active ? " active" : "")}
                onMouseEnter={() => setActive(i)}
                tabIndex={-1}
                onClick={() => pick(o)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(o); } }}
              >
                <span>{o}</span>
                {o === value && <Check size={16} />}
              </li>
            ))}
            {filtered.length === 0 && (
              <li className="brand-empty">{options.length ? "No matching brand" : "No brands yet - add one"}</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
