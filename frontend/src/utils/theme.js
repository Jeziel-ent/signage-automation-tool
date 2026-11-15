// Dark / light theme. The choice is "auto" (default: light from 06:00 to 18:00 by the computer's clock, dark otherwise), "light" or "dark".
// Only an explicit pick is saved (localStorage "signage.themeChoice"); "auto" saves nothing. The result is applied as data-theme on <html>
// (see theme-tokens.css). index.html applies the same rule before the first paint, so the page never flashes the wrong theme.
import { useCallback, useEffect, useSyncExternalStore } from "react";

export const CHOICE_KEY = "signage.themeChoice";
export const CHOICES = ["auto", "dark", "light"];
export const DAY_START_HOUR = 6;    // light from here ...
export const DAY_END_HOUR = 18;     // ... until here (exclusive)
const META_COLOR = { dark: "#0a0a0a", light: "#f5f5f4" };

/** "light" by day (06:00-17:59), "dark" otherwise. */
export function themeForTime(date = new Date()) {
  const h = date.getHours();
  return h >= DAY_START_HOUR && h < DAY_END_HOUR ? "light" : "dark";
}

/** The saved pick: "dark" | "light", else "auto". */
export function readChoice() {
  try {
    const c = localStorage.getItem(CHOICE_KEY);
    if (c === "dark" || c === "light") return c;
  } catch {
    /* storage blocked: automatic */
  }
  return "auto";
}

/** The theme a choice gives right now. */
export function resolveTheme(choice, date = new Date()) {
  return choice === "dark" || choice === "light" ? choice : themeForTime(date);
}

// every useTheme() in the page (welcome screen, its toggle, the sidebar button) shares one state through this tiny store
const listeners = new Set();
const subscribe = (cb) => {
  listeners.add(cb);
  return () => listeners.delete(cb);
};
const notify = () => listeners.forEach((cb) => cb());

let fadeTimer = null;

/** Put a theme on the page. A change of an already painted page fades (`theme-fade` on <html> for the length of the fade). */
export function paintTheme(theme) {
  const t = theme === "light" ? "light" : "dark";
  const root = document.documentElement;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", META_COLOR[t]);
  if (root.dataset.theme === t) return t;                 // already showing it: nothing to repaint, nobody to tell (the auto timer calls this every minute)
  if (root.dataset.theme && root.classList) {
    root.classList.add("theme-fade");
    clearTimeout(fadeTimer);
    fadeTimer = setTimeout(() => root.classList.remove("theme-fade"), 600);
  }
  root.dataset.theme = t;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", META_COLOR[t]);
  notify();
  return t;
}

/** Remember a choice ("auto" forgets the explicit pick) and paint it. Returns the theme now showing. */
export function applyChoice(choice, date = new Date()) {
  const c = CHOICES.includes(choice) ? choice : "auto";
  try {
    if (c === "auto") localStorage.removeItem(CHOICE_KEY);
    else localStorage.setItem(CHOICE_KEY, c);
  } catch {
    /* not saved: the choice still applies until the tab closes */
  }
  return paintTheme(resolveTheme(c, date));
}

const currentTheme = () => document.documentElement.dataset.theme || resolveTheme(readChoice());

/** { theme, choice, setChoice, toggle } - in "auto" the theme follows the clock (checked every minute); every open tab stays in step. */
export function useTheme() {
  const theme = useSyncExternalStore(subscribe, currentTheme, () => "dark");
  const choice = useSyncExternalStore(subscribe, readChoice, () => "auto");
  const setChoice = useCallback((c) => {
    applyChoice(c);
    notify();
  }, []);
  // flip between dark and light as an explicit pick (what the sidebar button does)
  const toggle = useCallback(() => setChoice(document.documentElement.dataset.theme === "dark" ? "light" : "dark"), [setChoice]);
  useEffect(() => {
    if (choice !== "auto") return undefined;
    paintTheme(themeForTime());   // the first paint came from index.html; make sure it matches the clock now
    const id = setInterval(() => paintTheme(themeForTime()), 60_000);
    return () => clearInterval(id);
  }, [choice]);
  useEffect(() => {
    const onStorage = (e) => {
      if (e.key !== CHOICE_KEY) return;
      paintTheme(resolveTheme(readChoice()));
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);
  return { theme, choice, setChoice, toggle };
}
